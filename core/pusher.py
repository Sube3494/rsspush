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
                    if all_images:
                        logger.debug(f"🖼️ 条目[{index+1}]包含 {len(all_images)} 张图片")

                    images = all_images[:max_images] if max_images > 0 else []

                    # 并发推送到所有目标
                    target_tasks = []
                    for target in sub.targets:
                        task = self._send_to_target_with_semaphore(
                            target, message, images, targets_semaphore
                        )
                        target_tasks.append(task)

                    # 等待所有目标推送完成
                    results = await asyncio.gather(*target_tasks, return_exceptions=True)
                    
                    # 检查是否有失败
                    failed_count = sum(1 for r in results if isinstance(r, Exception))
                    success_count = len(results) - failed_count

                    if failed_count > 0:
                        logger.warning(
                            f"条目[{index+1}]推送部分失败: "
                            f"成功 {success_count}/{len(results)} 个目标"
                        )
                        # 如果所有目标都失败，才记录为失败
                        if success_count == 0:
                            raise Exception(f"所有目标推送失败")
                    else:
                        logger.info(
                            f"✅ 条目[{index+1}]推送成功: "
                            f"{item['title'][:30]}... ({success_count}个目标)"
                        )

                    # 更新统计
                    sub.stats.total_pushes += 1
                    if success_count > 0:
                        sub.stats.success_pushes += 1
                    sub.last_push = datetime.now()

                    # 如果不是最后一个条目，添加间隔（避免API限流）
                    if index < len(items) - 1:
                        await asyncio.sleep(batch_interval)

                except Exception as e:
                    logger.error(f"❌ 推送条目[{index+1}]失败: {sub.name} - {e}")
                    sub.stats.last_error = str(e)
                    raise

        # 并发推送所有条目
        tasks = [
            push_single_item(item, i) for i, item in enumerate(items)
        ]
        
        # 等待所有推送完成
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 统计结果
        success_count = sum(1 for r in results if not isinstance(r, Exception))
        failed_count = len(results) - success_count
        
        if failed_count > 0:
            logger.warning(
                f"推送完成: 成功 {success_count}/{len(items)} 个条目, "
                f"失败 {failed_count} 个条目"
            )
        else:
            logger.info(f"✅ 所有 {len(items)} 个条目推送完成")

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

        # 构建消息（优化格式和排版，去除不必要的空格和换行）
        msg_parts = []
        
        # 订阅名称（顶部，带分隔线）
        separator_length = min(len(sub.name) + 4, 50)
        msg_parts.append(f"📢 {sub.name}")
        msg_parts.append("─" * separator_length)
        
        # 作者
        if author:
            author = author.strip()
            msg_parts.append(f"👤 {author}")
        
        # 标题
        title = title.strip()
        if title:
            msg_parts.append(f"📰 {title}")
        
        # 描述内容
        if desc:
            # 清理描述：去除多余空格和空行
            desc = desc.strip()
            # 将多个连续空格替换为单个空格
            import re
            desc = re.sub(r' +', ' ', desc)
            # 将多个连续换行替换为单个换行
            desc = re.sub(r'\n+', '\n', desc)
            
            # 智能换行：如果描述较长，在合适的位置换行
            if len(desc) > 120:
                desc_lines = []
                current_line = ""
                for char in desc:
                    current_line += char
                    # 在句号、问号、感叹号处换行
                    if char in ["。", "！", "？", ".", "!", "?"] and len(current_line.strip()) > 60:
                        if current_line.strip():
                            desc_lines.append(current_line.strip())
                        current_line = ""
                if current_line.strip():
                    desc_lines.append(current_line.strip())
                desc = "\n".join(desc_lines) if desc_lines else desc
            
            # 添加描述，第一行带emoji，后续行对齐到文字内容
            desc_lines = [line.strip() for line in desc.split("\n") if line.strip()]
            if desc_lines:
                # 第一行带emoji
                first_line = f"📝 {desc_lines[0]}"
                # 计算对齐所需的空格数（emoji + 空格的长度）
                indent = " " * (len("📝 ") + len(desc_lines[0]) - len(desc_lines[0].lstrip()))
                # 后续行对齐到第一行文字内容的起始位置
                indent_length = len("📝 ")
                other_lines = [f"{' ' * indent_length}{line}" for line in desc_lines[1:]]
                # 组合所有行
                formatted_desc = "\n".join([first_line] + other_lines)
                msg_parts.append(formatted_desc)
        
        # 元信息（时间）- 紧凑显示
        if pub_date_str:
            msg_parts.append(f"⏱️ {pub_date_str.strip()}")
        
        # 链接（底部）
        if link:
            link = link.strip()
            msg_parts.append(f"🔗 {link}")
        
        # 组合消息，去除空行和多余空格
        msg = "\n".join([part for part in msg_parts if part.strip()])
        # 清理连续的空行（最多保留一个）
        while "\n\n\n" in msg:
            msg = msg.replace("\n\n\n", "\n\n")
        
        return msg.strip()

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
