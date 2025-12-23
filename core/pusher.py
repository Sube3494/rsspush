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
        """格式化消息（支持自定义模板）

        Args:
            sub: 订阅对象
            item: RSS条目

        Returns:
            格式化后的消息
        """
        # 优先使用订阅的自定义模板，其次使用配置的默认模板
        template = sub.template
        if not template:
            template_config = self.config.get("template", {})
            template = template_config.get("default")
        
        # 如果有模板配置，使用模板格式化
        if template:
            try:
                from ..utils.formatter import MessageFormatter
                # 处理时间（已经是本地时间）
                pub_date_str = ""
                if item.get("pubDate") and isinstance(item["pubDate"], datetime):
                    pub_date_str = item["pubDate"].strftime("%Y-%m-%d %H:%M")
                
                # 准备模板参数
                template_item = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "description": item.get("description", ""),
                    "author": item.get("author", ""),
                    "pubDate": pub_date_str,
                    "guid": item.get("guid", ""),
                }
                
                formatter = MessageFormatter(template)
                return formatter.format(sub.name, template_item)
            except Exception as e:
                logger.warning(f"使用模板格式化失败: {e}，降级为默认格式")
        
        # 没有模板配置或格式化失败，使用内置简化格式
        return self._format_message_builtin(sub, item)
    
    def _format_message_builtin(self, sub: Subscription, item: dict) -> str:
        """内置简化格式（无需模板配置）
        
        Args:
            sub: 订阅对象
            item: RSS条目
        
        Returns:
            格式化后的消息
        """
        # 获取配置
        push_config = self.config.get("push", {})
        max_len = push_config.get("max_description_length", 200)
        
        # 准备数据
        title = item.get("title", "").strip()
        link = item.get("link", "").strip()
        author = item.get("author", "").strip()
        
        # 处理时间（已经是本地时间）
        pub_date_str = ""
        if item.get("pubDate") and isinstance(item["pubDate"], datetime):
            pub_date_str = item["pubDate"].strftime("%Y-%m-%d %H:%M")

        # 处理描述
        desc = item.get("description", "").strip()
        
        # 改进的去重逻辑 - 处理标题重复
        if desc and title:
            import re
            
            # 转义标题中的特殊正则字符
            escaped_title = re.escape(title)
            
            # 移除描述开头的标题（可能带引号）
            # 匹配: 标题, "标题", '标题' 等，可能重复多次
            pattern = rf'^[\s"\'"]*({escaped_title}[\s"\'"]*)+[\s\-—:：]*'
            desc = re.sub(pattern, '', desc, flags=re.IGNORECASE).strip()
            
            # 如果描述中还有标题重复（不在开头），也尝试移除
            # 例如: "标题" "标题" 其他内容
            pattern2 = rf'({escaped_title}[\s"\'"]*)+[\s\-—:：]*'
            # 只在开头100个字符内查找并替换一次，避免误删
            if len(desc) > 0:
                first_part = desc[:100]
                if re.search(pattern2, first_part, flags=re.IGNORECASE):
                    desc = re.sub(pattern2, '', desc, count=1, flags=re.IGNORECASE).strip()

        # 清理描述：移除多余空行和空格
        if desc:
            # 移除多个连续空格
            desc = re.sub(r' +', ' ', desc)
            # 移除多个连续换行
            desc = re.sub(r'\n+', '\n', desc)
            # 截断
            if len(desc) > max_len:
                desc = desc[:max_len] + "..."
            
            # 如果去重后描述太短（少于3个字符），可能是无意义内容，不显示
            if len(desc) < 3:
                desc = ""
        
        # 构建消息（优化格式，使用空行分隔）
        msg_parts = []
        
        # 订阅名称（使用方括号）
        msg_parts.append(f"【{sub.name}】")
        
        # 标题
        if title:
            msg_parts.append(f"📰 {title}")
        
        # 空行分隔（如果有描述或元信息）
        if desc or pub_date_str or author:
            msg_parts.append("")
        
        # 描述（只在有实际内容时显示）
        if desc:
            msg_parts.append(f"📝 {desc}")
            msg_parts.append("")  # 描述后加空行
        
        # 时间和作者（紧凑显示在一行）
        meta_parts = []
        if pub_date_str:
            meta_parts.append(f"⏱️ {pub_date_str}")
        if author:
            meta_parts.append(f"👤 {author}")
        if meta_parts:
            msg_parts.append(" | ".join(meta_parts))
        
        # 链接
        if link:
            msg_parts.append(f"🔗 动态地址：{link}")
        
        # 组合消息
        msg = "\n".join(msg_parts)
        
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
