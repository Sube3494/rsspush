"""图片处理模块"""

import aiohttp
from bs4 import BeautifulSoup

from astrbot.api import logger


class ImageHandler:
    """图片处理器"""

    @staticmethod
    def extract_images(entry: dict) -> list[str]:
        """从RSS条目中提取图片URL

        Args:
            entry: RSS条目

        Returns:
            图片URL列表
        """
        images = []

        # 1. 检查enclosure（RSS 2.0标准）
        if "enclosures" in entry:
            for enc in entry.get("enclosures", []):
                enc_type = enc.get("type", "")
                if enc_type.startswith("image/"):
                    href = enc.get("href", "")
                    if href:
                        images.append(href)
                        logger.debug(f"从enclosure提取图片: {href}")

        # 2. 检查media:content (Media RSS扩展)
        if "media_content" in entry:
            for media in entry.get("media_content", []):
                if media.get("medium") == "image":
                    url = media.get("url", "")
                    if url:
                        images.append(url)
                        logger.debug(f"从media:content提取图片: {url}")

        # 3. 从description/summary中提取图片
        summary = entry.get("summary", "") or entry.get("description", "")
        if summary:
            try:
                soup = BeautifulSoup(summary, "html.parser")
                for img in soup.find_all("img"):
                    src = img.get("src", "")
                    if src:
                        images.append(src)
                        logger.debug(f"从描述中提取图片: {src}")
            except Exception as e:
                logger.error(f"解析HTML提取图片失败: {e}")

        # 4. 从content中提取
        if "content" in entry:
            for content in entry.get("content", []):
                value = content.get("value", "")
                if value:
                    try:
                        soup = BeautifulSoup(value, "html.parser")
                        for img in soup.find_all("img"):
                            src = img.get("src", "")
                            if src:
                                images.append(src)
                                logger.debug(f"从content提取图片: {src}")
                    except Exception as e:
                        logger.error(f"解析content提取图片失败: {e}")

        # 去重
        unique_images = list(dict.fromkeys(images))

        if unique_images:
            logger.info(f"提取到 {len(unique_images)} 张图片")

        return unique_images

    @staticmethod
    async def download_image(url: str, timeout: int = 10) -> bytes | None:
        """下载图片

        Args:
            url: 图片URL
            timeout: 超时时间（秒）

        Returns:
            图片字节数据，失败返回None
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        # 检查内容类型
                        content_type = resp.headers.get("Content-Type", "")
                        if not content_type.startswith("image/"):
                            logger.warning(f"URL不是图片: {url} ({content_type})")
                            return None

                        # 读取内容
                        data = await resp.read()

                        # 检查大小（限制10MB）
                        if len(data) > 10 * 1024 * 1024:
                            logger.warning(f"图片太大: {url} ({len(data)} bytes)")
                            return None

                        logger.info(f"下载图片成功: {url} ({len(data)} bytes)")
                        return data
                    else:
                        logger.warning(f"下载图片失败: {url} (HTTP {resp.status})")
                        return None
        except aiohttp.ClientError as e:
            logger.error(f"下载图片网络错误 {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"下载图片失败 {url}: {e}")
            return None

    @staticmethod
    async def download_images(urls: list[str], max_images: int = 3) -> list[bytes]:
        """批量下载图片

        Args:
            urls: 图片URL列表
            max_images: 最大下载数量

        Returns:
            成功下载的图片数据列表
        """
        images = []

        # 限制下载数量
        urls_to_download = urls[:max_images]

        for url in urls_to_download:
            data = await ImageHandler.download_image(url)
            if data:
                images.append(data)

            # 如果已经下载到足够的图片，停止
            if len(images) >= max_images:
                break

        logger.info(f"成功下载 {len(images)}/{len(urls_to_download)} 张图片")
        return images
