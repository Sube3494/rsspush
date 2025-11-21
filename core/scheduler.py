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

            # 先按发布时间排序（最新的优先，用于后续处理）
            entries.sort(
                key=lambda x: x["pubDate"] if x.get("pubDate") else datetime.min,
                reverse=True,
            )

            # 过滤已推送的条目
            new_entries = []
            pushed_count = 0
            for entry in entries:
                guid = entry.get("guid", "")
                if not guid:
                    logger.warning(f"条目缺少GUID，跳过: {entry.get('title', 'Unknown')[:50]}")
                    continue
                    
                if not self.storage.is_pushed(guid, sub.id):
                    new_entries.append(entry)
                else:
                    pushed_count += 1

            logger.info(
                f"条目统计: 总计 {len(entries)} 条, "
                f"已推送 {pushed_count} 条, "
                f"新条目 {len(new_entries)} 条"
            )

            if not new_entries:
                logger.info(f"没有新内容需要推送: {sub.name}")
                sub.stats.success_checks += 1
                self.sub_manager.update_subscription(sub)
                return

            # 按时间从旧到新排序，确保按顺序推送（避免服务中断后漏掉中间的条目）
            new_entries.sort(
                key=lambda x: x["pubDate"] if x.get("pubDate") else datetime.min,
                reverse=False,  # 从旧到新
            )

            # 智能推送策略：
            # 1. 如果有多个未推送的条目（可能服务中断了），限制推送数量避免刷屏
            # 2. 如果只有一个未推送的条目，按配置的 max_items 限制推送
            max_items = sub.max_items or 1
            
            # 限制单次推送的最大条目数，避免一次性推送太多
            # 如果检测到大量未推送条目，可能是数据库问题或服务长时间中断
            max_push_limit = min(10, max_items * 3)  # 最多推送10条或max_items的3倍
            
            if len(new_entries) > max_push_limit:
                # 检测到大量未推送条目，可能是数据库问题，只推送最新的几条
                logger.warning(
                    f"检测到 {len(new_entries)} 个未推送条目（可能异常），"
                    f"将只推送最新的 {max_push_limit} 条以避免刷屏"
                )
                # 取最新的几条（因为已经按时间从旧到新排序，所以取最后几条）
                to_push = new_entries[-max_push_limit:]
            elif len(new_entries) > 1:
                # 检测到少量未推送条目，可能是服务短暂中断，推送所有
                logger.info(
                    f"检测到 {len(new_entries)} 个未推送条目，将按时间顺序推送所有条目"
                )
                to_push = new_entries
            else:
                # 只有一个未推送条目，按配置的 max_items 限制推送
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
