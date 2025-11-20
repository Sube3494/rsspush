"""调度器模块"""

from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from astrbot.api import logger

from ..utils.parser import RSSParser
from .pusher import Pusher
from .rss_fetcher import RSSFetcher
from .storage import Storage
from .subscription_manager import SubscriptionManager


class RSSScheduler:
    """RSS订阅调度器"""

    def __init__(
        self,
        sub_manager: SubscriptionManager,
        fetcher: RSSFetcher,
        pusher: Pusher,
        storage: Storage,
        interval: int = 30,
    ):
        self.sub_manager = sub_manager
        self.fetcher = fetcher
        self.pusher = pusher
        self.storage = storage
        self.interval = interval
        self.scheduler = AsyncIOScheduler()
        self.parser = RSSParser()

    def start(self):
        """启动调度器"""
        try:
            # 添加轮询任务
            self.scheduler.add_job(
                self.check_all_subscriptions,
                "interval",
                minutes=self.interval,
                id="rss_polling",
                replace_existing=True,
            )

            self.scheduler.start()
            logger.info(f"RSS调度器已启动，轮询间隔: {self.interval} 分钟")
        except Exception as e:
            logger.error(f"启动调度器失败: {e}")

    async def check_all_subscriptions(self):
        """检查所有启用的订阅"""
        logger.info("开始检查所有RSS订阅...")

        enabled_subs = self.sub_manager.list_enabled()
        logger.info(f"启用的订阅数: {len(enabled_subs)}")

        for sub in enabled_subs:
            try:
                await self.check_subscription(sub)
            except Exception as e:
                logger.error(f"检查订阅失败 {sub.name}: {e}")
                sub.stats.last_error = str(e)
                self.sub_manager.update_subscription(sub)

    async def check_subscription(self, sub):
        """检查单个订阅

        Args:
            sub: 订阅对象
        """
        logger.info(f"检查订阅: {sub.name}")

        # 更新统计
        sub.stats.total_checks += 1
        sub.last_check = datetime.now()

        try:
            # 获取RSS内容（带重试）
            feed_data = await self.fetcher.fetch_with_retry(sub.url)

            if not feed_data:
                logger.warning(f"获取RSS失败: {sub.name}")
                self.sub_manager.update_subscription(sub)
                return

            # 解析条目 (在线程池中运行)
            import asyncio
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(None, self.parser.parse_entries, feed_data)

            if not entries:
                logger.info(f"订阅无新内容: {sub.name}")
                sub.stats.success_checks += 1
                self.sub_manager.update_subscription(sub)
                return

            logger.info(f"解析到 {len(entries)} 个条目: {sub.name}")

            # 先按发布时间排序（最新的优先）
            entries.sort(
                key=lambda x: x["pubDate"] if x.get("pubDate") else datetime.min,
                reverse=True,
            )

            # 只检查最新的内容
            if entries:
                latest_entry = entries[0]
                latest_guid = latest_entry["guid"]

                # 如果最新的已经推送过，说明没有新内容
                if self.storage.is_pushed(latest_guid, sub.id):
                    logger.info(f"最新内容已推送，跳过: {sub.name}")
                    sub.stats.success_checks += 1
                    self.sub_manager.update_subscription(sub)
                    return

            # 过滤已推送的条目
            new_entries = []
            for entry in entries:
                guid = entry["guid"]
                if not self.storage.is_pushed(guid, sub.id):
                    new_entries.append(entry)

            logger.info(f"新条目数: {len(new_entries)} / {len(entries)}")

            if not new_entries:
                logger.info(f"没有新内容需要推送: {sub.name}")
                sub.stats.success_checks += 1
                self.sub_manager.update_subscription(sub)
                return

            # 限制推送数量（已经是按时间排序的）
            max_items = sub.max_items or 1
            to_push = new_entries[:max_items]

            logger.info(f"准备推送 {len(to_push)} 个条目: {sub.name}")

            # 推送
            await self.pusher.push(sub, to_push)

            # 标记为已推送
            target_ids = [t.id for t in sub.targets]
            for entry in to_push:
                self.storage.mark_pushed(entry["guid"], sub.id, target_ids)

            # 更新统计
            sub.stats.success_checks += 1
            self.sub_manager.update_subscription(sub)

            logger.info(f"订阅检查完成: {sub.name}")

        except Exception as e:
            logger.error(f"检查订阅异常 {sub.name}: {e}")
            sub.stats.last_error = str(e)
            self.sub_manager.update_subscription(sub)
            raise

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("RSS调度器已停止")
