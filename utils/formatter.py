"""消息格式化器模块"""

import html
import re
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
            # 准备格式化参数（使用传入的所有变量）
            params = {
                "name": sub_name,
            }
            # 添加 item 中的所有键值对
            params.update(item)
            
            # 格式化时间（如果存在）
            if "pubDate" in params and not isinstance(params["pubDate"], str):
                params["pubDate"] = self._format_date(params["pubDate"])

            # 格式化模板
            message = self.template.format(**params)
            
            # 后处理：清理包含空值的行
            lines = message.split('\n')
            cleaned_lines = []
            
            for line in lines:
                # 跳过只包含空白的行
                if not line.strip():
                    cleaned_lines.append('')
                    continue
                
                # 检查是否包含emoji后面紧跟空白（说明变量是空的）
                # 例如: "🎬 " 或 "🎬  " 或 "🎬"
                if re.match(r'^[\U0001F300-\U0001F9FF]\s*$', line.strip()):
                    continue  # 跳过这一行
                
                cleaned_lines.append(line)
            
            # 清理连续的多个空行，最多保留一个
            final_lines = []
            prev_empty = False
            for line in cleaned_lines:
                is_empty = not line.strip()
                if is_empty and prev_empty:
                    continue  # 跳过连续的空行
                final_lines.append(line)
                prev_empty = is_empty
            
            # 移除开头和结尾的空行
            while final_lines and not final_lines[0].strip():
                final_lines.pop(0)
            while final_lines and not final_lines[-1].strip():
                final_lines.pop()
            
            return '\n'.join(final_lines)

        except KeyError as e:
            logger.error(f"格式化消息失败，缺少变量: {e}")
            # 降级为简单格式
            return f"{sub_name}\n\n{item.get('title', '')}\n\n{item.get('link', '')}"
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
