"""推送器模块"""

import asyncio
from datetime import datetime

from astrbot.api import logger

from .subscription import Subscription, Target


class Pusher:
    """内容推送器"""

    def __init__(self, context, plugin_config=None):
        self.context = context
        # 使用传入的插件配置，如果没有则使用默认值
        self.config = (
            plugin_config
            if plugin_config
            else {
                "push": {"batch_interval": 3},
                "template": {
                    "default": "【{name}】\n📰 {title}\n\n📝 {description}\n\n⏱️ {pubDate} | 👤 {author}\n🔗 动态地址：{link}"
                },
            }
        )

    async def push(self, sub: Subscription, items: list[dict]):
        """推送内容到目标（支持并发推送）

        Args:
            sub: 订阅对象
            items: 要推送的条目列表
        """
        if not items:
            return

        # 获取配置
        push_config = self.config.get("push", {})
        batch_interval = push_config.get("batch_interval", 3)
        max_images = push_config.get("max_images_per_push", 1)
        # 并发配置：同时推送的条目数，默认3个
        concurrent_items = push_config.get("concurrent_items", 3)
        # 每个条目的目标并发数，默认5个
        concurrent_targets = push_config.get("concurrent_targets", 5)
        
        logger.info(
            f"📊 推送配置: 条目数={len(items)}, "
            f"并发条目数={concurrent_items}, "
            f"并发目标数={concurrent_targets}, "
            f"批量间隔={batch_interval}秒, "
            f"最大图片数={max_images}"
        )

        # 使用信号量控制并发数
        items_semaphore = asyncio.Semaphore(concurrent_items)
        targets_semaphore = asyncio.Semaphore(concurrent_targets)

        async def push_single_item(item: dict, index: int):
            """推送单个条目"""
            async with items_semaphore:
                try:
                    message = self._format_message(sub, item)

                    # 提取图片URL
                    all_images = item.get("images", [])
                    images = all_images[:max_images] if max_images > 0 else []

                    # 并发推送到所有目标
                    target_tasks = []
                    for target in sub.targets:
                        target_tasks.append(
                            self._send_to_target_with_semaphore(
                                target, message, images, targets_semaphore
                            )
                        )

                    # 等待所有目标推送完成
                    results = await asyncio.gather(*target_tasks, return_exceptions=True)
                    
                    # 统计是否有成功
                    failed_count = sum(1 for r in results if isinstance(r, Exception))
                    success_count = len(results) - failed_count

                    if success_count == 0 and len(results) > 0:
                        raise Exception("所有目标推送失败")

                    logger.info(f"✅ 条目[{index+1}]推送完成: {item['title'][:30]}... ({success_count}成功)")

                    # 如果不是最后一个条目，添加间隔（避免API限流）
                    if index < len(items) - 1:
                        await asyncio.sleep(batch_interval)

                except Exception as e:
                    logger.error(f"❌ 推送条目[{index+1}]失败: {sub.name} - {e}")
                    raise

        # 并发推送所有条目
        tasks = [push_single_item(item, i) for i, item in enumerate(items)]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _send_to_target_with_semaphore(
        self,
        target: Target,
        message: str,
        images: list[str],
        semaphore: asyncio.Semaphore,
    ):
        """带信号量控制的发送消息到目标"""
        async with semaphore:
            return await self._send_to_target(target, message, images)

    def _format_message(self, sub: Subscription, item: dict) -> str:
        """格式化消息"""
        from ..utils.content_processor import ContentProcessorFactory
        
        factory = ContentProcessorFactory()
        processor = factory.get_processor(sub.url)
        processed = processor.process(item, self.config)
        
        template = sub.template
        if not template:
            template = self.config.get("template", {}).get("default")
        
        if template:
            try:
                from ..utils.formatter import MessageFormatter
                pub_date_str = ""
                if item.get("pubDate") and isinstance(item["pubDate"], datetime):
                    pub_date_str = item["pubDate"].strftime("%Y-%m-%d %H:%M")
                
                template_item = {
                    "title": item.get("title", "").strip(),
                    "display_title": processed.get("display_title", ""),
                    "link": item.get("link", ""),
                    "description": item.get("description", ""),
                    "clean_description": processed.get("clean_description", ""),
                    "video_url": processed.get("video_url", ""),
                    "extra_links": processed.get("extra_links", {}),
                    "author": item.get("author", ""),
                    "pubDate": pub_date_str,
                    "guid": item.get("guid", ""),
                }
                return MessageFormatter(template).format(sub.name, template_item)
            except Exception as e:
                logger.warning(f"模板格式化失败: {e}，将使用内置格式")
        
        return self._format_message_builtin(sub, item, processed)
    
    def _format_message_builtin(self, sub: Subscription, item: dict, processed: dict) -> str:
        """内置简化格式"""
        title = item.get("title", "").strip()
        link = item.get("link", "").strip()
        author = item.get("author", "").strip()
        pub_date_str = ""
        if item.get("pubDate") and isinstance(item["pubDate"], datetime):
            pub_date_str = item["pubDate"].strftime("%Y-%m-%d %H:%M")

        clean_desc = processed.get("clean_description", "")
        video_url = processed.get("video_url", "")
        extra_links = processed.get("extra_links", {})
        
        msg_parts = [f"【{sub.name}】"]
        if clean_desc:
            msg_parts.append(f"\n📝 {clean_desc}")
        if video_url:
            msg_parts.append(f"\n🎬 视频：{video_url}")
        if extra_links.get('opus'):
            msg_parts.append(f"📄 图文：{extra_links['opus']}")
        
        if pub_date_str or author:
            meta = []
            if pub_date_str: meta.append(f"⏱️ {pub_date_str}")
            if author: meta.append(f"👤 {author}")
            msg_parts.append("\n" + " | ".join(meta))
        
        if link:
            msg_parts.append(f"🔗 地址：{link}")
        
        return "\n".join(msg_parts).strip()

    async def _send_to_target(self, target: Target, message: str, images: list[str] = []):
        """发送消息到远端"""
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Image

            message_chain = MessageChain().message(message)
            if images:
                for img_url in images:
                    try:
                        message_chain.chain.append(Image.fromURL(img_url))
                    except: pass

            session_str = target.id
            success = await self.context.send_message(session_str, message_chain)
            if not success:
                raise Exception("未找到匹配的会话或平台")
        except Exception as e:
            logger.error(f"❌ 发送失败: {e}")
            raise
