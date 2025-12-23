"""消息格式化器模块"""

import html
from datetime import datetime

from bs4 import BeautifulSoup

from astrbot.api import logger


class MessageFormatter:
    """消息格式化器"""

    def __init__(self, template: str):
        """初始化格式化器

        Args:
            template: 消息模板
        """
        self.template = template

    def format(self, sub_name: str, item: dict) -> str:
        """使用模板格式化消息

        Args:
            sub_name: 订阅名称
            item: RSS条目

        Returns:
            格式化后的消息
        """
        try:
            # 准备格式化参数
            params = {
                "name": sub_name,
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "description": self._truncate(
                    self._clean_text(item.get("description", "")), 200
                ),
                "pubDate": self._format_date(item.get("pubDate")),
                "author": item.get("author", ""),
                "guid": item.get("guid", ""),
            }

            # 格式化模板
            message = self.template.format(**params)

            return message

        except Exception as e:
            logger.error(f"格式化消息失败: {e}")
            # 降级为简单格式
            return f"{sub_name}\n\n{item.get('title', '')}\n\n{item.get('link', '')}"

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """截断文本

        Args:
            text: 原始文本
            max_len: 最大长度

        Returns:
            截断后的文本
        """
        if not text:
            return ""

        if len(text) > max_len:
            return text[:max_len] + "...查看更多"

        return text

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理文本，移除HTML标签和多余空白

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        if not text:
            return ""

        # 移除HTML标签
        soup = BeautifulSoup(text, "html.parser")
        clean = soup.get_text(separator=" ", strip=True)

        # 解码HTML实体
        clean = html.unescape(clean)

        # 去除多余空白
        clean = " ".join(clean.split())

        return clean

    @staticmethod
    def _format_date(dt: datetime | None) -> str:
        """格式化日期

        Args:
            dt: 日期时间对象

        Returns:
            格式化后的日期字符串
        """
        if not dt:
            return ""

        if isinstance(dt, datetime):
            return dt.strftime("%Y-%m-%d %H:%M")

        return str(dt)

    @staticmethod
    def format_relative_time(dt: datetime) -> str:
        """格式化相对时间

        Args:
            dt: 日期时间对象

        Returns:
            相对时间字符串（如"2小时前"）
        """
        if not dt or not isinstance(dt, datetime):
            return ""

        now = datetime.now()
        if dt.tzinfo:
            # 如果有时区信息，转换为无时区
            from dateutil import tz

            dt = dt.astimezone(tz.tzlocal()).replace(tzinfo=None)

        diff = now - dt

        seconds = diff.total_seconds()

        if seconds < 60:
            return "刚刚"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}分钟前"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours}小时前"
        elif seconds < 2592000:  # 30天
            days = int(seconds / 86400)
            return f"{days}天前"
        else:
            return dt.strftime("%Y-%m-%d")

    @staticmethod
    def create_default_formatter() -> "MessageFormatter":
        """创建默认格式化器

        Returns:
            使用默认模板的格式化器
        """
        default_template = """【{name}】
📰 {title}

📝 {description}

⏱️ {pubDate} | 👤 {author}
🔗 动态地址：{link}"""

        return MessageFormatter(default_template)
