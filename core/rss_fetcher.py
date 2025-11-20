"""RSS获取器模块"""

import aiohttp
import feedparser

from astrbot.api import logger


class RSSFetcher:
    """RSS内容获取器"""

    def __init__(self, timeout: int = 30):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: aiohttp.ClientSession | None = None

    async def fetch(self, url: str) -> dict | None:
        """异步获取RSS内容

        Args:
            url: RSS地址

        Returns:
            解析后的feed数据，失败返回None
        """
        if not self.session:
            self.session = aiohttp.ClientSession(timeout=self.timeout)

        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    content = await response.text()

                    # 在线程池中运行解析，避免阻塞事件循环
                    import asyncio

                    loop = asyncio.get_event_loop()
                    feed = await loop.run_in_executor(None, feedparser.parse, content)

                    # 检查是否成功解析
                    if hasattr(feed, "bozo") and feed.bozo and feed.bozo_exception:
                        logger.warning(f"RSS解析警告 {url}: {feed.bozo_exception}")

                    return feed
                else:
                    logger.error(f"获取RSS失败 {url}: HTTP {response.status}")
                    return None
        except aiohttp.ClientError as e:
            logger.error(f"获取RSS网络错误 {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"获取RSS失败 {url}: {e}")
            return None

    async def fetch_with_retry(self, url: str, max_retries: int = 3) -> dict | None:
        """带重试的获取

        Args:
            url: RSS地址
            max_retries: 最大重试次数

        Returns:
            解析后的feed数据，失败返回None
        """
        import asyncio

        for attempt in range(max_retries):
            result = await self.fetch(url)
            if result:
                return result

            if attempt < max_retries - 1:
                wait_time = 2**attempt  # 指数退避
                logger.info(f"重试获取RSS {url}，等待 {wait_time} 秒...")
                await asyncio.sleep(wait_time)

        logger.error(f"获取RSS最终失败 {url}，已重试 {max_retries} 次")
        return None

    async def close(self):
        """关闭会话"""
        if self.session:
            await self.session.close()
            self.session = None
            logger.info("RSS获取器已关闭")
