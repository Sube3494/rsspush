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

            # 获取最后推送的条目GUID（用于检测漏推）
            last_pushed_guid = self.storage.get_last_pushed_guid(sub.id)
            
            # 找出最后推送的条目在feed中的位置
            last_pushed_entry_time = None
            if last_pushed_guid:
                for entry in entries:
                    if entry.get("guid") == last_pushed_guid:
                        last_pushed_entry_time = entry.get("pubDate")
                        break
            
            # 过滤已推送的条目，并找出最新的一条和漏推的条目
            latest_entry = None
            missed_entries = []
            pushed_count = 0
            
            # 先找出所有未推送的条目
            unpushed_entries = []
            for entry in entries:
                guid = entry.get("guid", "")
                if not guid:
                    logger.warning(f"条目缺少GUID，跳过: {entry.get('title', 'Unknown')[:50]}")
                    continue
                
                # 检查是否已推送
                if self.storage.is_pushed(guid, sub.id):
                    pushed_count += 1
                    continue
                
                entry_time = entry.get("pubDate")
                if not entry_time:
                    # 没有时间的条目，跳过
                    continue
                
                unpushed_entries.append(entry)
            
            if not unpushed_entries:
                logger.info(f"没有新内容需要推送: {sub.name}")
                sub.stats.success_checks += 1
                self.sub_manager.update_subscription(sub)
                return
            
            # 最新的一条就是第一条未推送的（因为已经按时间排序，最新的在前）
            latest_entry = unpushed_entries[0]
            
            # 如果有最后推送的条目时间，找出漏推的条目
            # 漏推的条目：发布时间在最后推送条目时间之后，但不是最新的一条
            if last_pushed_entry_time and len(unpushed_entries) > 1:
                for entry in unpushed_entries[1:]:  # 跳过最新的一条
                    entry_time = entry.get("pubDate")
                    if entry_time and entry_time > last_pushed_entry_time:
                        missed_entries.append(entry)
            
            logger.info(
                f"条目统计: 总计 {len(entries)} 条, "
                f"已推送 {pushed_count} 条, "
                f"最新未推送: 1 条, "
                f"漏推: {len(missed_entries)} 条"
            )

            # 确定要推送的条目
            to_push = []
            
            # 如果有漏推的条目，先推送漏推的（按时间从旧到新），再推送最新的
            if missed_entries:
                # 漏推的条目按时间从旧到新排序
                missed_entries.sort(
                    key=lambda x: x.get("pubDate") or datetime.min,
                    reverse=False,
                )
                to_push.extend(missed_entries)
                logger.info(
                    f"检测到 {len(missed_entries)} 个漏推条目（服务中断期间），"
                    f"将补推这些条目"
                )
            
            # 添加最新的一条
            to_push.append(latest_entry)
            
            # 限制推送数量，避免一次性推送太多（最多20条）
            max_push_limit = 20
            if len(to_push) > max_push_limit:
                logger.warning(
                    f"推送条目过多 ({len(to_push)} 条)，"
                    f"将只推送最新的 {max_push_limit} 条以避免刷屏"
                )
                # 保留漏推的最新几条 + 最新的一条
                if len(missed_entries) >= max_push_limit:
                    to_push = missed_entries[-max_push_limit+1:] + [latest_entry]
                else:
                    to_push = missed_entries + [latest_entry]
                    to_push = to_push[-max_push_limit:]

            logger.info(
                f"准备推送 {len(to_push)} 个条目: "
                f"{len(missed_entries) if missed_entries else 0} 个漏推 + 1 个最新"
            )

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
