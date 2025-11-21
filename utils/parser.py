"""RSS解析器模块"""

import html
from datetime import datetime

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from astrbot.api import logger


class RSSParser:
    """RSS内容解析器"""

    @staticmethod
    def parse_entries(feed_data: dict) -> list[dict]:
        """解析RSS条目

        Args:
            feed_data: feedparser解析的feed数据

        Returns:
            解析后的条目列表
        """
        entries = []

        for entry in feed_data.get("entries", []):
            try:
                parsed = {
                    "guid": RSSParser._extract_guid(entry),
                    "title": RSSParser._clean_text(entry.get("title", "")),
                    "link": entry.get("link", ""),
                    "description": RSSParser._extract_description(entry),
                    "author": entry.get("author", ""),
                    "pubDate": RSSParser._parse_date(entry),
                    "images": RSSParser._extract_images(entry),
                }
                entries.append(parsed)
            except Exception as e:
                logger.error(f"解析RSS条目失败: {e}")
                continue

        logger.info(f"解析了 {len(entries)} 个RSS条目")
        return entries

    @staticmethod
    def _extract_guid(entry: dict) -> str:
        """提取条目唯一标识"""
        from urllib.parse import urlparse, urlunparse
        
        # 优先使用id，其次使用guid
        guid = entry.get("id") or entry.get("guid")
        if guid:
            return str(guid)
        
        # 如果都没有，使用link，但要去除查询参数确保一致性
        link = entry.get("link", "")
        if link:
            try:
                # 解析URL并去除查询参数和片段
                parsed = urlparse(link)
                # 重新构建URL，只保留 scheme, netloc, path
                clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
                return clean_url
            except Exception:
                # 如果解析失败，返回原始link
                return str(link)
        
        return ""

    @staticmethod
    def _extract_description(entry: dict) -> str:
        """提取并清理描述内容"""
        # 尝试多个字段
        description = (
            entry.get("summary", "")
            or entry.get("description", "")
            or entry.get("content", [{}])[0].get("value", "")
        )

        # 清理HTML标签
        if description:
            soup = BeautifulSoup(description, "html.parser")
            text = soup.get_text(separator=" ", strip=True)
            # 解码HTML实体
            text = html.unescape(text)
            return text

        return ""

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理文本"""
        if not text:
            return ""
        # 解码HTML实体
        text = html.unescape(text)
        # 去除多余空白
        text = " ".join(text.split())
        return text

    @staticmethod
    def _parse_date(entry: dict) -> datetime | None:
        """解析发布日期

        Args:
            entry: RSS条目

        Returns:
            解析后的日期，失败返回None
        """
        # 尝试多个日期字段
        date_str = (
            entry.get("published") or entry.get("updated") or entry.get("created")
        )

        if date_str:
            try:
                return date_parser.parse(date_str)
            except Exception as e:
                logger.debug(f"解析日期失败 {date_str}: {e}")

        return None

    @staticmethod
    def _extract_images(entry: dict) -> list[str]:
        """提取条目中的图片URL

        Args:
            entry: RSS条目

        Returns:
            图片URL列表
        """
        images = []

        # 1. 检查 media_content (Media RSS扩展)
        if "media_content" in entry:
            for media in entry.get("media_content", []):
                if media.get("medium") == "image" or "image" in media.get("type", ""):
                    url = media.get("url", "")
                    if url and url not in images:
                        images.append(url)

        # 2. 检查 enclosures (RSS 2.0标准)
        if "enclosures" in entry:
            for enc in entry.get("enclosures", []):
                enc_type = enc.get("type", "")
                if enc_type.startswith("image/"):
                    url = enc.get("href", "")
                    if url and url not in images:
                        images.append(url)

        # 3. 从 description/summary 中提取图片
        summary = entry.get("summary", "") or entry.get("description", "")
        if summary:
            try:
                soup = BeautifulSoup(summary, "html.parser")
                # 提取 <img> 标签
                for img in soup.find_all("img"):
                    src = img.get("src", "")
                    if src and src.startswith("http") and src not in images:
                        images.append(src)
                # 提取 <video> 标签的 poster 属性
                for video in soup.find_all("video"):
                    poster = video.get("poster", "")
                    if poster and poster.startswith("http") and poster not in images:
                        images.append(poster)
            except Exception as e:
                logger.debug(f"从summary提取图片失败: {e}")

        # 4. 从 content 中提取
        if "content" in entry:
            for content in entry.get("content", []):
                value = content.get("value", "")
                if value:
                    try:
                        soup = BeautifulSoup(value, "html.parser")
                        # 提取 <img> 标签
                        for img in soup.find_all("img"):
                            src = img.get("src", "")
                            if src and src.startswith("http") and src not in images:
                                images.append(src)
                        # 提取 <video> 标签的 poster 属性
                        for video in soup.find_all("video"):
                            poster = video.get("poster", "")
                            if (
                                poster
                                and poster.startswith("http")
                                and poster not in images
                            ):
                                images.append(poster)
                    except Exception as e:
                        logger.debug(f"从content提取图片失败: {e}")

        if images:
            logger.debug(f"提取到 {len(images)} 张图片")

        return images

    @staticmethod
    def extract_feed_info(feed_data: dict) -> dict:
        """提取feed信息

        Args:
            feed_data: feedparser解析的feed数据

        Returns:
            feed信息字典
        """
        feed = feed_data.get("feed", {})
        return {
            "title": feed.get("title", ""),
            "link": feed.get("link", ""),
            "description": feed.get("description", ""),
            "updated": feed.get("updated", ""),
        }
