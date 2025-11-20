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
                    "default": "🔔 {name}\n\n📰 {title}\n🕐 {pubDate}\n\n📝 {description}\n\n🔗 {link}"
                },
            }
        )

    async def push(self, sub: Subscription, items: list[dict]):
        """推送内容到目标

        Args:
            sub: 订阅对象
            items: 要推送的条目列表
        """
        if not items:
            return

        # 获取批量推送间隔
        batch_interval = self.config.get("push", {}).get("batch_interval", 3)
        # 获取最大图片数
        max_images = self.config.get("push", {}).get("max_images_per_push", 1)
        logger.info(f"📊 配置: 批量间隔={batch_interval}秒, 最大图片数={max_images}")

        # 推送每个条目
        for i, item in enumerate(items):
            try:
                message = self._format_message(sub, item)

                # 提取图片URL
                all_images = item.get("images", [])
                logger.info(f"🖼️ RSS条目包含 {len(all_images)} 张图片")

                images = all_images[:max_images] if max_images > 0 else []
                if images:
                    logger.info(f"🖼️ 准备推送 {len(images)} 张图片 (限制: {max_images})")
                    for idx, img_url in enumerate(images, 1):
                        logger.debug(f"  图片{idx}: {img_url[:80]}...")

                # 推送到所有目标
                for target in sub.targets:
                    await self._send_to_target(target, message, images)

                # 更新统计
                sub.stats.total_pushes += 1
                sub.stats.success_pushes += 1
                sub.last_push = datetime.now()

                logger.info(f"推送成功: {sub.name} - {item['title'][:30]}")

                # 批量推送时添加间隔
                if i < len(items) - 1:
                    await asyncio.sleep(batch_interval)

            except Exception as e:
                logger.error(f"推送失败: {sub.name} - {e}")
                sub.stats.last_error = str(e)

    def _format_message(self, sub: Subscription, item: dict) -> str:
        """格式化消息（优化版）

        Args:
            sub: 订阅对象
            item: RSS条目

        Returns:
            格式化后的消息
        """
        # 获取配置
        push_config = self.config.get("push", {})
        max_len = push_config.get("max_length", 200)
        show_images = push_config.get("show_images", True)
        
        # 准备数据
        title = item.get("title", "").strip()
        link = item.get("link", "")
        author = item.get("author", "")
        
        # 处理时间
        pub_date_str = ""
        if item.get("pubDate") and isinstance(item["pubDate"], datetime):
            pub_date_str = item["pubDate"].strftime("%Y-%m-%d %H:%M")

        # 处理描述
        desc = item.get("description", "").strip()
        
        # 如果描述以标题开头，去掉标题部分避免重复
        if desc and title and desc.startswith(title):
            desc = desc[len(title) :].strip()
            # 去掉开头的标点符号
            if desc and desc[0] in ["，", "。", "：", ":", ",", ".", " "]:
                desc = desc[1:].strip()

        # 智能截断
        if desc:
            # 移除多余空行
            desc = "\n".join([line.strip() for line in desc.splitlines() if line.strip()])
            if len(desc) > max_len:
                desc = desc[:max_len] + "..."
        else:
            # 如果没有描述，使用替代文本
            desc = "📷 包含图片" if item.get("images") else "点击链接查看详情"

        # 构建消息 (使用默认模板，暂不支持自定义模板以保证样式统一，后续可加回)
        msg = f"📢 {sub.name}\n"
        msg += "═══════════════\n"
        msg += f"📰 {title}\n"
        msg += "═══════════════\n"
        msg += f"{desc}\n\n"
        
        if pub_date_str:
            msg += f"⏱️ {pub_date_str}\n"
        
        if author:
            msg += f"👤 {author}\n"
            
        msg += f"🔗 {link}"

        return msg

    async def _send_to_target(
        self, target: Target, message: str, images: list[str] = []
    ):
        """发送消息到目标

        Args:
            target: 推送目标
            message: 消息内容
            images: 图片URL列表
        """
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Image

            # 构造消息链（使用 .message() 方法）
            message_chain = MessageChain().message(message)

            # 添加图片
            if images:
                for img_url in images:
                    try:
                        # 使用 Image.fromURL 创建图片组件
                        img_component = Image.fromURL(img_url)
                        message_chain.chain.append(img_component)
                        logger.info(f"🖼️ 添加图片: {img_url[:50]}...")
                    except Exception as e:
                        logger.warning(f"⚠️ 添加图片失败: {e}")

            # target.id 已经是完整的 session 字符串（platform:MessageType:id）
            # 例如: aiocqhttp:GroupMessage:123456
            session_str = target.id

            logger.info(f"📤 发送消息到 {session_str}")

            # 使用context发送消息
            success = await self.context.send_message(session_str, message_chain)

            if success:
                logger.info("✅ 消息发送成功")
            else:
                logger.warning("⚠️ 未找到匹配的平台或会话")
                raise Exception("消息发送失败：未找到匹配的平台")

        except Exception as e:
            logger.error(f"❌ 发送消息失败: {e}")
            raise
