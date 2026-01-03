"""RSS推送插件主入口"""

import json
import os

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context

from .core.pusher import Pusher
from .core.rss_fetcher import RSSFetcher
from .core.scheduler import RSSScheduler
from .core.storage import Storage
from .core.subscription import Target
from .core.subscription_manager import SubscriptionManager


class RSSPushPlugin(star.Star):
    """RSS推送插件主类"""

    def __init__(self, context: Context, **kwargs):
        super().__init__(context)

        # 数据目录
        from pathlib import Path
        data_dir = Path(__file__).parent / "data"

        # 初始化存储和管理器
        self.storage = Storage(str(data_dir))
        self.sub_manager = SubscriptionManager(self.storage)

        # 这些将在 initialize 中初始化
        self.plugin_config = {}
        self.scheduler = None
        self.fetcher = None
        self.pusher = None

    async def initialize(self):
        """插件初始化"""
        logger.info("RSS推送插件初始化...")

        # 如果调度器已存在，先停止它（配置更新时可能需要重启）
        if self.scheduler:
            logger.info("检测到已有调度器，正在停止...")
            self.scheduler.stop()
            self.scheduler = None

        # 获取插件配置（用于全局设置）
        # 直接从配置文件读取，确保使用最新配置
        from pathlib import Path
        plugin_dir = Path(__file__).parent
        # WebUI 保存的配置文件路径：data/config/rsspush_config.json
        # 从 data/plugins/rsspush/ 到 data/config/
        config_file = plugin_dir.parent.parent / "config" / "rsspush_config.json"
        
        try:
            if config_file.exists():
                # 使用 utf-8-sig 编码读取，支持 UTF-8 BOM
                with open(config_file, encoding="utf-8-sig") as f:
                    self.plugin_config = json.load(f)
                logger.info(f"已从配置文件加载配置: {config_file}")
            else:
                logger.warning(f"配置文件不存在: {config_file}，使用默认配置")
                self.plugin_config = {}
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            self.plugin_config = {}

        logger.info(f"当前订阅数: {len(self.sub_manager.list_all())}")

        # 初始化RSS获取器（如果已存在则关闭旧的）
        if self.fetcher:
            await self.fetcher.close()
        self.fetcher = RSSFetcher()

        # 初始化推送器（传入配置）
        self.pusher = Pusher(self.context, self.plugin_config)

        # 初始化调度器
        # 从配置文件读取轮询配置
        polling_config = self.plugin_config.get("polling", {})
        polling_enabled = polling_config.get("enabled", True)
        polling_interval = polling_config.get("interval", 30)
        
        logger.info(f"读取配置: 轮询启用={polling_enabled}, 轮询间隔={polling_interval} 分钟")

        if polling_enabled:
            self.scheduler = RSSScheduler(
                self.sub_manager,
                self.fetcher,
                self.pusher,
                self.storage,
                polling_interval,
            )
            await self.scheduler.start()
            logger.info(f"RSS调度器已启动，轮询间隔: {polling_interval} 分钟")
        else:
            logger.info("RSS轮询已禁用（可在WebUI配置中启用）")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss target")
    async def rss_target(
        self, event: AstrMessageEvent, action: str = "", sub_id_or_name: str = "", remote_target_id: str = ""
    ):
        """管理订阅的推送目标

        使用方法:
        /rss sub <订阅ID或名称> [远程ID]    - 将当前会话(或指定远程ID)添加为推送目标
        /rss sub all                      - 将当前会话添加到所有订阅
        /rss unsub <订阅ID或名称> [远程ID] - 从订阅中移除当前会话(或指定远程ID)
        /rss unsub all                    - 从所有订阅中移除当前会话
        /rss targets <订阅ID或名称>        - 查看订阅的所有推送目标(及远程ID)
        """
        if not action:
            yield event.plain_result(
                "📝 推送目标管理\n\n"
                "使用方法：\n"
                "/rss sub <ID> [远程ID] - 将当前(或指定远程)会话添加为目标\n"
                "/rss sub all - 添加到所有订阅\n"
                "/rss unsub <ID> [远程ID] - 移除当前(或指定远程)会话\n"
                "/rss unsub all - 从所有订阅移除\n"
                "/rss targets <ID> - 查看推送目标及其完整远程ID\n\n"
                "💡 提示：你可以通过 /rss targets 获取其他群组 ID 进行远程管理。"
            )
            return

        if remote_target_id:
            # 远程管理模式：人工构造 Target 对象
            parts = remote_target_id.split(":")
            target = Target(
                type="group" if len(parts) > 1 and "Group" in parts[1] else "private",
                platform=parts[0] if len(parts) > 0 else "unknown",
                id=remote_target_id,
            )
            is_remote = True
        else:
            # 当前会话模式
            target = Target(
                type="group" if not event.is_private_chat() else "private",
                platform=event.get_platform_name(),
                id=event.unified_msg_origin,
            )
            is_remote = False
        
        target_name_desc = f"目标({target.id if is_remote else '当前会话'})"

        if action == "add":
            if not sub_id_or_name:
                yield event.plain_result("❌ 请指定订阅ID/名称或使用 'all'")
                return

            if sub_id_or_name.lower() == "all":
                # 添加到所有订阅
                count = 0
                for sub in self.sub_manager.list_all():
                    if self.sub_manager.add_target(sub.id, target):
                        count += 1
                yield event.plain_result(f"✅ 已将 {target_name_desc} 添加到 {count} 个订阅")
            else:
                # 添加到指定订阅
                sub = self.sub_manager.get(
                    sub_id_or_name
                ) or self.sub_manager.get_by_name(sub_id_or_name)
                if not sub:
                    yield event.plain_result(f"❌ 未找到订阅: {sub_id_or_name}")
                    return

                if self.sub_manager.add_target(sub.id, target):
                    yield event.plain_result(f"✅ 已将 {target_name_desc} 添加到订阅: {sub.name}")
                else:
                    yield event.plain_result(
                        f"ℹ️ {target_name_desc} 已经是订阅 {sub.name} 的推送目标"
                    )

        elif action == "remove":
            if not sub_id_or_name:
                yield event.plain_result("❌ 请指定订阅ID/名称或使用 'all'")
                return

            if sub_id_or_name.lower() == "all":
                # 从所有订阅中移除
                count = 0
                for sub in self.sub_manager.list_all():
                    if self.sub_manager.remove_target(sub.id, target.id):
                        count += 1
                if count > 0:
                    yield event.plain_result(f"✅ 已从 {count} 个订阅中移除 {target_name_desc}")
                else:
                    yield event.plain_result(f"ℹ️ {target_name_desc} 不是任何订阅的推送目标")
            else:
                # 从指定订阅移除
                sub = self.sub_manager.get(
                    sub_id_or_name
                ) or self.sub_manager.get_by_name(sub_id_or_name)
                if not sub:
                    yield event.plain_result(f"❌ 未找到订阅: {sub_id_or_name}")
                    return

                if self.sub_manager.remove_target(sub.id, target.id):
                    yield event.plain_result(f"✅ 已从订阅 {sub.name} 移除 {target_name_desc}")
                else:
                    yield event.plain_result(
                        f"ℹ️ {target_name_desc} 不是订阅 {sub.name} 的推送目标"
                    )

        elif action == "list":
            if not sub_id_or_name:
                yield event.plain_result("❌ 请指定订阅ID或名称")
                return

            sub = self.sub_manager.get(sub_id_or_name) or self.sub_manager.get_by_name(
                sub_id_or_name
            )
            if not sub:
                yield event.plain_result(f"❌ 未找到订阅: {sub_id_or_name}")
                return

            if not sub.targets:
                yield event.plain_result(
                    f"📋 订阅 {sub.name} 暂无推送目标\n\n使用 /rss sub {sub.id[:8]} 添加当前会话"
                )
                return

            msg = f"📋 订阅推送目标: {sub.name}\n\n"
            for i, t in enumerate(sub.targets, 1):
                msg += f"{i}. {t.type} @ {t.platform}\n   ID: {t.id}\n"
            yield event.plain_result(msg)
        else:
            yield event.plain_result(f"❌ 未知操作: {action}\n\n使用 /rss target 查看帮助")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss sub")
    async def rss_sub(self, event: AstrMessageEvent, sub_id_or_name: str = "", remote_target_id: str = ""):
        """快捷订阅命令 (支持远程 ID)"""
        async for res in self.rss_target(event, "add", sub_id_or_name, remote_target_id):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss unsub")
    async def rss_unsub(self, event: AstrMessageEvent, sub_id_or_name: str = "", remote_target_id: str = ""):
        """快捷退订命令 (支持远程 ID)"""
        async for res in self.rss_target(event, "remove", sub_id_or_name, remote_target_id):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss targets")
    async def rss_targets(self, event: AstrMessageEvent, sub_id_or_name: str = ""):
        """快捷查看目标命令"""
        async for res in self.rss_target(event, "list", sub_id_or_name):
            yield res

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss add")
    async def rss_add(
        self, event: AstrMessageEvent,
        url1: str = "", url2: str = "", url3: str = "", url4: str = "", url5: str = "",
        url6: str = "", url7: str = "", url8: str = "", url9: str = "", url10: str = ""
    ):
        """添加RSS订阅（通过命令，推荐使用WebUI配置）

        使用方法: /rss add <RSS地址> [订阅名称]
        批量添加: /rss add <URL1> <URL2> <URL3> ... (最多10个)
        如果不提供名称，会自动从RSS feed中获取
        """
        # 收集所有非空URL参数
        all_urls = [url1, url2, url3, url4, url5, url6, url7, url8, url9, url10]
        urls = [u for u in all_urls if u]
        
        if not urls:
            yield event.plain_result(
                "📝 使用方法：\n"
                "/rss add <RSS地址> [订阅名称]\n\n"
                "示例：\n"
                "/rss add https://rsshub.app/bilibili/user/video/2\n"
                "/rss add https://rsshub.app/bilibili/user/video/2 B站UP主\n\n"
                "📦 批量添加：\n"
                "/rss add <URL1> <URL2> <URL3> ... (最多10个)\n\n"
                "示例：\n"
                "/rss add https://rsshub.app/bilibili/user/video/1 https://rsshub.app/bilibili/user/video/2\n\n"
                "💡 提示：\n"
                "- 批量添加时，多个URL用空格分隔\n"
                "- 批量添加会自动从RSS获取名称\n"
                "- 添加后请使用 /rss sub <ID> 设置推送目标"
            )
            return

        # 如果只有一个URL，检查第二个参数是否为自定义名称
        # （第二个参数不是URL时，视为名称）
        if len(urls) == 1:
            custom_name = ""
            # 检查url2是否为名称而不是URL
            if url2 and not (url2.startswith('http://') or url2.startswith('https://') or url2.startswith('/')):
                custom_name = url2
            
            if custom_name:
                # 单个添加，带自定义名称
                url_to_add = urls[0]
                
                # 处理RSSHub路由快捷方式
                if url_to_add.startswith("/"):
                    rsshub_config = self.plugin_config.get("rsshub", {})
                    rsshub_instance = rsshub_config.get(
                        "default_instance", "https://rsshub.app"
                    )
                    url_to_add = rsshub_instance + url_to_add
                    logger.info(f"RSSHub路由转换为完整URL: {url_to_add}")
                
                # 默认推送到当前会话
                target = Target(
                    type="group" if not event.is_private_chat() else "private",
                    platform=event.get_platform_name(),
                    id=event.unified_msg_origin,
                )
                
                try:
                    sub = self.sub_manager.add(custom_name, url_to_add, [target])
                    msg = "✅ 订阅添加成功！\n\n"
                    msg += "📋 订阅信息：\n"
                    msg += f"  ID: {sub.id[:8]}...\n"
                    msg += f"  名称: {sub.name}\n"
                    msg += f"  地址: {sub.url}\n"
                    msg += "  推送到: 当前会话\n"
                    msg += f"  状态: {'✅ 已启用' if sub.enabled else '❌ 已禁用'}"
                    yield event.plain_result(msg)
                except Exception as e:
                    logger.error(f"添加订阅失败: {e}")
                    yield event.plain_result(f"❌ 添加订阅失败: {str(e)}")
                return
        
        # 批量添加模式
        yield event.plain_result(f"🔄 开始批量添加 {len(urls)} 个订阅...")
        
        success_count = 0
        fail_count = 0
        results = []
        
        for idx, url_to_add in enumerate(urls, 1):
            try:
                # 处理RSSHub路由快捷方式
                if url_to_add.startswith("/"):
                    rsshub_config = self.plugin_config.get("rsshub", {})
                    rsshub_instance = rsshub_config.get(
                        "default_instance", "https://rsshub.app"
                    )
                    url_to_add = rsshub_instance + url_to_add
                    logger.info(f"RSSHub路由转换为完整URL: {url_to_add}")
                
                # 获取RSS名称
                feed_name = url_to_add
                if self.fetcher:
                    try:
                        feed = await self.fetcher.fetch(url_to_add)
                        if feed and hasattr(feed, 'feed') and hasattr(feed.feed, 'get'):  # type: ignore
                            feed_info = feed.feed  # type: ignore
                            feed_name = (
                                feed_info.get('title') or 
                                feed_info.get('subtitle') or 
                                url_to_add
                            )
                            logger.info(f"[{idx}/{len(urls)}] 自动获取订阅名称: {feed_name}")
                    except Exception as e:
                        logger.warning(f"[{idx}/{len(urls)}] 无法获取RSS标题: {e}")
                
                # 默认推送到当前会话
                target = Target(
                    type="group" if not event.is_private_chat() else "private",
                    platform=event.get_platform_name(),
                    id=event.unified_msg_origin,
                )
                
                # 添加订阅
                sub = self.sub_manager.add(feed_name, url_to_add, [target])
                results.append(f"✅ [{idx}] {sub.name[:30]}...")
                success_count += 1
                logger.info(f"[{idx}/{len(urls)}] 订阅添加成功: {sub.name}")
                
            except Exception as e:
                results.append(f"❌ [{idx}] {url_to_add[:40]}... - {str(e)[:30]}")
                fail_count += 1
                logger.error(f"[{idx}/{len(urls)}] 添加订阅失败: {e}")
        
        # 输出结果
        msg = f"📦 批量添加完成\n\n"
        msg += f"✅ 成功: {success_count} 个\n"
        msg += f"❌ 失败: {fail_count} 个\n\n"
        msg += "详细结果：\n"
        msg += "\n".join(results)
        
        yield event.plain_result(msg)



    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss list")
    async def rss_list(self, event: AstrMessageEvent):
        """查看所有订阅"""
        subs = self.sub_manager.list_all()

        if not subs:
            yield event.plain_result("📋 暂无订阅\n\n💡 使用 /rss add 添加订阅")
            return

        msg = f"📋 RSS订阅列表（共 {len(subs)} 个）\n\n"

        for i, sub in enumerate(subs, 1):
            status = "✅" if sub.enabled else "❌"
            target_count = len(sub.targets)
            msg += f"{i}. {status} {sub.name}\n"
            msg += f"   ID: {sub.id[:8]}...\n"
            msg += f"   目标: {target_count} 个会话\n"
            msg += "\n"

        msg += "💡 使用 /rss info <ID> 查看详情"
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss info")
    async def rss_info(self, event: AstrMessageEvent, sub_id: str = ""):
        """查看订阅详情"""
        if not sub_id:
            yield event.plain_result("请指定订阅ID\n\n使用 /rss list 查看所有订阅")
            return

        sub = self.sub_manager.get(sub_id) or self.sub_manager.get_by_name(sub_id)
        if not sub:
            yield event.plain_result(f"❌ 未找到订阅: {sub_id}")
            return

        msg = f"📋 订阅详情: {sub.name}\n\n"
        msg += f"ID: {sub.id}\n"
        msg += f"状态: {'✅ 已开启' if sub.enabled else '❌ 已关闭'}\n"
        msg += f"地址: {sub.url}\n"
        
        if sub.last_pub_date:
            msg += f"动态基准: {sub.last_pub_date.strftime('%Y-%m-%d %H:%M')}\n"

        if sub.last_error:
            msg += f"\n⚠️ 最后错误: {sub.last_error}\n"

        msg += f"\n🎯 推送目标 ({len(sub.targets)} 个):\n"
        for i, target in enumerate(sub.targets, 1):
            msg += f"  {i}. {target.type} @ {target.platform}: {target.id}\n"

        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss del")
    async def rss_del(
        self, event: AstrMessageEvent, 
        id1: str = "", id2: str = "", id3: str = "", id4: str = "", id5: str = "",
        id6: str = "", id7: str = "", id8: str = "", id9: str = "", id10: str = ""
    ):
        """删除订阅

        使用方法: /rss del <订阅ID>
        批量删除: /rss del <ID1> <ID2> <ID3> ... (最多10个)
        ID支持前缀匹配，例如：/rss del 6b8a 会匹配 6b8a1234...
        """
        # 收集所有非空ID参数
        all_ids = [id1, id2, id3, id4, id5, id6, id7, id8, id9, id10]
        id_list = [id_str for id_str in all_ids if id_str]
        
        if not id_list:
            yield event.plain_result("请指定订阅ID\n\n使用 /rss list 查看所有订阅")
            return
        
        if len(id_list) == 1:
            # 单个删除（原逻辑）
            target_id = id_list[0]
            
            # 尝试前缀匹配
            matched_sub = None
            if len(target_id) < 36:  # 不是完整UUID，尝试前缀匹配
                all_subs = self.sub_manager.list_all()
                matches = [s for s in all_subs if s.id.startswith(target_id)]
                
                if len(matches) == 0:
                    yield event.plain_result(f"❌ 未找到匹配的订阅: {target_id}")
                    return
                elif len(matches) > 1:
                    msg = f"⚠️ 找到多个匹配的订阅，请使用更长的ID前缀：\n\n"
                    for s in matches[:5]:  # 最多显示5个
                        msg += f"  {s.id[:8]}... - {s.name}\n"
                    if len(matches) > 5:
                        msg += f"  ... 还有 {len(matches) - 5} 个匹配项"
                    yield event.plain_result(msg)
                    return
                else:
                    matched_sub = matches[0]
            else:
                # 完整ID，直接查询
                matched_sub = self.sub_manager.get(target_id)
            
            if not matched_sub:
                yield event.plain_result(f"❌ 未找到订阅: {target_id}")
                return

            if self.sub_manager.delete(matched_sub.id):
                yield event.plain_result(f"✅ 订阅已删除\n\n{matched_sub.name} ({matched_sub.id[:8]}...)")
            else:
                yield event.plain_result("❌ 删除失败")
            return
        
        # 批量删除模式
        yield event.plain_result(f"🔄 开始批量删除 {len(id_list)} 个订阅...")
        
        success_count = 0
        fail_count = 0
        results = []
        all_subs = self.sub_manager.list_all()  # 获取所有订阅用于前缀匹配
        
        for idx, target_id in enumerate(id_list, 1):
            try:
                # 尝试前缀匹配
                matched_sub = None
                if len(target_id) < 36:  # 不是完整UUID
                    matches = [s for s in all_subs if s.id.startswith(target_id)]
                    
                    if len(matches) == 0:
                        results.append(f"❌ [{idx}] {target_id} - 未找到匹配")
                        fail_count += 1
                        continue
                    elif len(matches) > 1:
                        results.append(f"⚠️ [{idx}] {target_id} - 匹配到{len(matches)}个，跳过")
                        fail_count += 1
                        continue
                    else:
                        matched_sub = matches[0]
                else:
                    matched_sub = self.sub_manager.get(target_id)
                
                if not matched_sub:
                    results.append(f"❌ [{idx}] {target_id} - 未找到")
                    fail_count += 1
                    continue
                
                # 删除订阅
                if self.sub_manager.delete(matched_sub.id):
                    results.append(f"✅ [{idx}] {matched_sub.name[:30]}...")
                    success_count += 1
                    # 从列表中移除已删除的订阅，避免后续匹配到
                    all_subs = [s for s in all_subs if s.id != matched_sub.id]
                    logger.info(f"[{idx}/{len(id_list)}] 订阅删除成功: {matched_sub.name}")
                else:
                    results.append(f"❌ [{idx}] {matched_sub.name[:30]}... - 删除失败")
                    fail_count += 1
                    
            except Exception as e:
                results.append(f"❌ [{idx}] {target_id} - {str(e)[:30]}")
                fail_count += 1
                logger.error(f"[{idx}/{len(id_list)}] 删除订阅失败: {e}")
        
        # 输出结果
        msg = f"📦 批量删除完成\n\n"
        msg += f"✅ 成功: {success_count} 个\n"
        msg += f"❌ 失败: {fail_count} 个\n\n"
        msg += "详细结果：\n"
        msg += "\n".join(results)
        
        yield event.plain_result(msg)


    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss enable")
    async def rss_enable(self, event: AstrMessageEvent, sub_id: str = ""):
        """启用订阅

        使用方法: /rss enable <订阅ID>
        """
        if not sub_id:
            yield event.plain_result("请指定订阅ID\n\n使用 /rss list 查看所有订阅")
            return

        if self.sub_manager.enable(sub_id):
            sub = self.sub_manager.get(sub_id)
            if sub:
                yield event.plain_result(f"✅ 订阅已启用\n\n{sub.name}")
            else:
                yield event.plain_result(f"✅ 订阅已启用 (ID: {sub_id})")
        else:
            yield event.plain_result(f"❌ 未找到订阅: {sub_id}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss disable")
    async def rss_disable(self, event: AstrMessageEvent, sub_id: str = ""):
        """禁用订阅

        使用方法: /rss disable <订阅ID>
        """
        if not sub_id:
            yield event.plain_result("请指定订阅ID\n\n使用 /rss list 查看所有订阅")
            return

        if self.sub_manager.disable(sub_id):
            sub = self.sub_manager.get(sub_id)
            if sub:
                yield event.plain_result(f"⏸️ 订阅已禁用\n\n{sub.name}")
            else:
                yield event.plain_result(f"⏸️ 订阅已禁用 (ID: {sub_id})")
        else:
            yield event.plain_result(f"❌ 未找到订阅: {sub_id}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss test")
    async def rss_test(self, event: AstrMessageEvent, sub_id: str = ""):
        """测试订阅推送（强制推送最新1条，不记录）

        使用方法: /rss test <订阅ID>
        """
        if not sub_id:
            yield event.plain_result("❌ 请指定订阅ID")
            return

        sub = self.sub_manager.get(sub_id)
        if not sub:
            yield event.plain_result(f"❌ 未找到订阅: {sub_id}")
            return

        if not sub.enabled:
            yield event.plain_result(
                f"⚠️ 订阅 {sub.name} 已禁用\n\n使用 /rss enable {sub.id[:8]} 启用"
            )
            return

        if not sub.targets:
            yield event.plain_result(
                f"⚠️ 订阅 {sub.name} 没有推送目标\n\n"
                f"使用 /rss target add {sub.id[:8]} 添加当前会话"
            )
            return

        yield event.plain_result(
            f"🔄 正在测试订阅: {sub.name}\n请稍候…"
        )

        try:
            # 检查fetcher是否初始化
            if not self.fetcher:
                yield event.plain_result(f"❌ RSS获取器未初始化")
                return
                
            # 获取RSS内容
            feed = await self.fetcher.fetch(sub.url)
            if not feed or not hasattr(feed, "entries") or not feed.entries:  # type: ignore
                yield event.plain_result(f"❌ 无法获取RSS内容或内容为空")
                return

            # 解析最新的1条
            from .utils.parser import RSSParser
            entries = RSSParser.parse_entries({"entries": feed.entries[:1]})  # type: ignore
            
            if not entries or not entries[0].get("guid"):
                yield event.plain_result(f"❌ RSS内容解析失败")
                return

            # 检查pusher是否初始化
            if not self.pusher:
                yield event.plain_result(f"❌ 推送器未初始化")
                return

            # 直接推送，不检查是否已推送，也不记录
            await self.pusher.push(sub, entries)
            
            yield event.plain_result(
                f"✅ 测试推送完成\n\n"
                f"📰 推送内容：{entries[0].get('title', '无标题')}\n"
                f"🔗 链接：{entries[0].get('link', '')}\n\n"
                f"💡 提示：测试推送不会记录到数据库"
            )

        except Exception as e:
            logger.error(f"测试推送失败: {e}")
            yield event.plain_result(f"❌ 测试失败: {str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss update")
    async def rss_update(self, event: AstrMessageEvent, sub_id: str = ""):
        """立即检查订阅更新

        使用方法: /rss update <订阅ID>
        """
        if not sub_id:
            yield event.plain_result("请指定订阅ID，或使用 'all' 检查所有订阅")
            return

        if sub_id.lower() == "all":
            yield event.plain_result("🔄 正在检查所有订阅...")
            try:
                if self.scheduler:
                    await self.scheduler.check_all_subscriptions()
                    yield event.plain_result("✅ 所有订阅检查完成")
                else:
                    yield event.plain_result("❌ 调度器未启动")
            except Exception as e:
                yield event.plain_result(f"❌ 检查失败: {str(e)}")
        else:
            sub = self.sub_manager.get(sub_id) or self.sub_manager.get_by_name(sub_id)
            if not sub:
                yield event.plain_result(f"❌ 未找到订阅: {sub_id}")
                return

            yield event.plain_result(f"🔄 正在检查: {sub.name}...")
            try:
                if self.scheduler:
                    await self.scheduler.check_subscription(sub)
                    yield event.plain_result(f"✅ {sub.name} 检查完成")
                else:
                    yield event.plain_result("❌ 调度器未启动")
            except Exception as e:
                yield event.plain_result(f"❌ 检查失败: {str(e)}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("rss help")
    async def rss_help(self, event: AstrMessageEvent):
        """查看帮助"""
        msg = """📖 RSS推送插件帮助

📋 订阅管理:
/rss add <url> [名称] - 添加订阅
/rss del <ID> - 删除订阅
/rss list - 查看所有订阅
/rss info <ID> - 查看详情
/rss enable <ID> - 启用订阅
/rss disable <ID> - 禁用订阅

🎯 推送目标管理:
/rss sub <ID> [远程ID] - 添加当前(或指定远程)会话为目标
/rss unsub <ID> [远程ID] - 移除当前(或指定远程)会话
/rss targets <ID> - 查看订阅已有的推送目标

🔧 运行控制:
/rss test <ID> - 手动测试一条推送
/rss update <ID> - 立即检查更新 (all 为检查所有)
/rss help - 显示此帮助内容

💡 提示：订阅ID支持前缀匹配（如前3位）"""
        yield event.plain_result(msg)

    async def terminate(self):
        """插件终止时清理资源"""
        logger.info("RSS推送插件正在停止...")

        # 停止调度器
        if self.scheduler:
            self.scheduler.stop()

        # 关闭获取器
        if self.fetcher:
            await self.fetcher.close()

        logger.info("RSS推送插件已停止")
