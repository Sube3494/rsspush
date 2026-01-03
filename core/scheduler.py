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
        self.time_offset = 0  # 网络时间 - 本地时间

    async def get_network_time_offset(self):
        """获取网络时间偏差 (网络时间 - 本地时间)"""
        urls = [
            "https://www.baidu.com",
            "https://www.taobao.com",
            "https://www.google.com",
        ]
        import email.utils
        import time
        
        for url in urls:
            try:
                # 使用 fetcher 的 session 但不解析内容，只拿头部
                if not self.fetcher.session:
                    import aiohttp
                    self.fetcher.session = aiohttp.ClientSession(timeout=self.fetcher.timeout)
                
                start_local = time.time()
                async with self.fetcher.session.head(url, timeout=5) as resp:
                    # 获取 Date 响应头以同步网络时间
                    headers = resp.headers
                    date_str = None
                    if hasattr(headers, "get"):
                        date_str = headers.get("Date")
                    elif "Date" in headers:
                        date_str = headers["Date"]
                        
                    if date_str:
                        # 假设请求耗时一半作为网络时间点
                        rtt = time.time() - start_local
                        net_time_struct = email.utils.parsedate_tz(date_str)
                        if net_time_struct:
                            net_timestamp = email.utils.mktime_tz(net_time_struct)
                            # 网络时间 = 头部时间 + RTT/2
                            network_at = net_timestamp + (rtt / 2)
                            local_at = time.time()
                            self.time_offset = network_at - local_at
                            logger.info(f"成功获取网络时间，偏差: {self.time_offset:.2f}秒 (URL: {url})")
                            return self.time_offset
            except Exception as e:
                logger.debug(f"尝试从 {url} 获取时间失败: {e}")
                continue
        
        logger.warning("未能获取网络时间，将使用系统时间")
        return 0

    async def start(self):
        """启动调度器"""
        try:
            # 获取网络时间偏差
            await self.get_network_time_offset()

            from datetime import datetime, timedelta
            now_local = datetime.now()
            now_net = now_local + timedelta(seconds=self.time_offset)
            
            # 计算下一次对齐点（网络时间）
            if self.interval < 60:
                # 对齐到下一个 interval 分钟的整点
                next_min = ((now_net.minute // self.interval) + 1) * self.interval
                next_net = now_net.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=next_min)
            else:
                # 对齐到下一个整小时
                hours = self.interval // 60
                next_hour = ((now_net.hour // hours) + 1) * hours
                next_net = now_net.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=next_hour)
            
            # 转换为本地时间
            start_date_local = next_net - timedelta(seconds=self.time_offset)
            
            # 如果 start_date 太近（比如小于 5 秒），APScheduler 可能会报错，往后推一个周期
            if (start_date_local - now_local).total_seconds() < 5:
                start_date_local += timedelta(minutes=self.interval)
                next_net += timedelta(minutes=self.interval)

            logger.info(
                f"RSS调度器启动: 间隔 {self.interval} 分钟. "
                f"预定网络时间 {next_net.strftime('%H:%M:%S')} (本地时间 {start_date_local.strftime('%H:%M:%S')}) 开始对齐运行"
            )

            # 添加轮询任务
            self.scheduler.add_job(
                self.check_all_subscriptions,
                "interval",
                minutes=self.interval,
                start_date=start_date_local,
                id="rss_polling",
                replace_existing=True,
            )

            self.scheduler.start()
        except Exception as e:
            logger.error(f"启动调度器失败: {e}")

    async def check_all_subscriptions(self):
        """检查所有启启用用的订阅"""
        logger.info("开始检查所有RSS订阅...")

        enabled_subs = self.sub_manager.list_enabled()
        logger.info(f"启用的订阅数: {len(enabled_subs)}")

        for sub in enabled_subs:
            try:
                await self.check_subscription(sub)
            except Exception as e:
                logger.error(f"检查订阅期内异常 {sub.name}: {e}")
                sub.last_error = str(e)
                self.sub_manager.update_subscription(sub)

    async def check_subscription(self, sub):
        """检查单个订阅"""
        logger.info(f"检查订阅: {sub.name}")

        try:
            # 获取RSS内容
            feed_data = await self.fetcher.fetch_with_retry(sub.url)

            if not feed_data:
                logger.warning(f"获取RSS失败: {sub.url}")
                return

            # 解析条目
            import asyncio
            loop = asyncio.get_event_loop()
            entries = await loop.run_in_executor(None, self.parser.parse_entries, feed_data)

            if not entries:
                return

            # 过滤掉没有发布时间的条目
            valid_entries = [e for e in entries if e.get("pubDate")]
            if not valid_entries:
                return

            # 按发布时间由旧到新排序
            valid_entries.sort(key=lambda x: x["pubDate"])

            to_push = []
            
            # 确定基准线 (Baseline)
            baseline = sub.last_pub_date

            if baseline is None:
                # 冷启动: 仅推送最新一条
                latest_entry = valid_entries[-1]
                logger.info(f"冷启动: {sub.name} -> {latest_entry.get('title')}")
                to_push = [latest_entry]
            else:
                # 增量推送
                for entry in valid_entries:
                    if entry["pubDate"] > baseline:
                        guid = entry.get("guid")
                        if guid and not self.storage.is_pushed(str(guid), sub.id):
                            to_push.append(entry)
                
                if to_push:
                    logger.info(f"发现新动态: {sub.name} ({len(to_push)}条)")

            if not to_push:
                return

            # 限制单次推送数量
            max_limit = 10
            if len(to_push) > max_limit:
                to_push = to_push[-max_limit:]

            # 执行推送
            await self.pusher.push(sub, to_push)

            # 推送成功后更新状态
            sub.last_pub_date = to_push[-1]["pubDate"]
            sub.last_error = None  # 运行成功，清除错误信息
            
            for entry in to_push:
                self.storage.mark_pushed(entry["guid"], sub.id, entry["pubDate"])

            self.sub_manager.update_subscription(sub)

        except Exception as e:
            logger.error(f"检查订阅异常 {sub.name}: {e}")
            sub.last_error = str(e)
            self.sub_manager.update_subscription(sub)

    def stop(self):
        """停止调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("RSS调度器已停止")
