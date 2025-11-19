"""内容过滤器模块"""

import re

from astrbot.api import logger


class ContentFilter:
    """内容过滤器"""

    def __init__(self, filters: dict):
        """初始化过滤器

        Args:
            filters: 过滤规则字典
                {
                    'whitelist': [],  # 白名单关键词
                    'blacklist': [],  # 黑名单关键词
                    'use_regex': False  # 是否使用正则表达式
                }
        """
        self.whitelist = filters.get("whitelist", [])
        self.blacklist = filters.get("blacklist", [])
        self.use_regex = filters.get("use_regex", False)

    def should_push(self, item: dict) -> bool:
        """判断是否应该推送此条目

        Args:
            item: RSS条目

        Returns:
            True表示应该推送，False表示应过滤掉
        """
        # 如果没有设置任何过滤规则，默认推送
        if not self.whitelist and not self.blacklist:
            return True

        # 获取用于过滤的内容（标题+描述）
        content = self._get_filter_content(item)

        # 黑名单检查（优先级最高）
        if self.blacklist:
            for keyword in self.blacklist:
                if self._match(keyword, content):
                    logger.info(f"黑名单过滤: {keyword}")
                    return False

        # 白名单检查
        if self.whitelist:
            for keyword in self.whitelist:
                if self._match(keyword, content):
                    logger.info(f"白名单匹配: {keyword}")
                    return True
            # 如果设置了白名单但都不匹配，则不推送
            logger.info("未匹配任何白名单关键词")
            return False

        # 没有白名单或通过了黑名单检查
        return True

    def _get_filter_content(self, item: dict) -> str:
        """获取用于过滤的内容

        Args:
            item: RSS条目

        Returns:
            合并的标题和描述文本
        """
        title = item.get("title", "")
        description = item.get("description", "")
        content = f"{title} {description}".strip()
        return content

    def _match(self, pattern: str, text: str) -> bool:
        """匹配模式

        Args:
            pattern: 匹配模式（关键词或正则表达式）
            text: 待匹配文本

        Returns:
            是否匹配成功
        """
        if not pattern or not text:
            return False

        try:
            if self.use_regex:
                # 正则表达式匹配
                return bool(re.search(pattern, text, re.IGNORECASE))
            else:
                # 普通关键词匹配（不区分大小写）
                return pattern.lower() in text.lower()
        except re.error as e:
            logger.error(f"正则表达式错误 '{pattern}': {e}")
            return False
        except Exception as e:
            logger.error(f"匹配失败: {e}")
            return False

    @staticmethod
    def create_filter(
        whitelist: list[str] = None,
        blacklist: list[str] = None,
        use_regex: bool = False,
    ) -> "ContentFilter":
        """创建过滤器

        Args:
            whitelist: 白名单关键词列表
            blacklist: 黑名单关键词列表
            use_regex: 是否使用正则表达式

        Returns:
            内容过滤器实例
        """
        filters = {
            "whitelist": whitelist or [],
            "blacklist": blacklist or [],
            "use_regex": use_regex,
        }
        return ContentFilter(filters)
