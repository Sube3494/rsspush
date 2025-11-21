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

            # 按发布时间排序（最新的在前）
            entries.sort(
                key=lambda x: x["pubDate"] if x.get("pubDate") else datetime.min,
                reverse=True,  # 最新的在前
            )

            # 获取最后推送的条目GUID，并找出其发布时间
            last_pushed_guid = self.storage.get_last_pushed_guid(sub.id)
            last_pushed_entry_time = None
            
            if last_pushed_guid:
                # 在feed中找到最后推送的条目，获取其发布时间
                for entry in entries:
                    if entry.get("guid") == last_pushed_guid:
                        last_pushed_entry_time = entry.get("pubDate")
                        break
            
            # 找出所有比最后推送条目时间新的条目
            # 只推送比已推送内容更新的内容
            newer_entries = []
            pushed_count = 0
            
            for entry in entries:
                guid = entry.get("guid", "")
                if not guid:
                    logger.warning(f"条目缺少GUID，跳过: {entry.get('title', 'Unknown')[:50]}")
                    continue
                
                entry_time = entry.get("pubDate")
                if not entry_time:
                    # 没有时间的条目，跳过
                    continue
                
                # 检查是否已推送
                if self.storage.is_pushed(guid, sub.id):
                    pushed_count += 1
                    continue
                
                # 如果没有最后推送时间，说明是第一次推送，只推送最新的一条
                if last_pushed_entry_time is None:
                    # 第一次推送，只推送最新的一条
                    newer_entries.append(entry)
                    break  # 只取第一条（最新的）
                
                # 只推送比最后推送条目时间新的条目
                if entry_time > last_pushed_entry_time:
                    newer_entries.append(entry)
            
            logger.info(
                f"条目统计: 总计 {len(entries)} 条, "
                f"已推送 {pushed_count} 条, "
                f"比最后推送时间新的条目: {len(newer_entries)} 条"
            )
            
            if not newer_entries:
                logger.info(f"没有新内容需要推送: {sub.name}")
                sub.stats.success_checks += 1
                self.sub_manager.update_subscription(sub)
                return
            
            # 确定要推送的条目
            to_push = []
            
            if len(newer_entries) == 1:
                # 只有一条新的，正常情况，只推送这一条
                to_push = newer_entries
                logger.info(
                    f"正常推送最新 1 条: {sub.name} - {to_push[0].get('title', 'Unknown')[:50]}"
                )
            else:
                # 有多条新的，说明服务中断导致漏推了
                # 推送所有漏推的条目（按时间从旧到新排序）+ 最新的一条
                # 先按时间从旧到新排序
                newer_entries_sorted = sorted(
                    newer_entries,
                    key=lambda x: x.get("pubDate") or datetime.min,
                    reverse=False,  # 从旧到新
                )
                
                # 推送所有漏推的条目
                to_push = newer_entries_sorted
                
                logger.info(
                    f"检测到漏推，将推送 {len(to_push)} 条: "
                    f"{len(newer_entries)-1} 个漏推 + 1 个最新"
                )
            
            # 限制推送数量，避免一次性推送太多（最多20条）
            max_push_limit = 20
            if len(to_push) > max_push_limit:
                logger.warning(
                    f"推送条目过多 ({len(to_push)} 条)，"
                    f"将只推送最新的 {max_push_limit} 条以避免刷屏"
                )
                # 保留最新的几条
                to_push = to_push[-max_push_limit:]

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
