"""订阅管理器模块"""

from astrbot.api import logger

from .storage import Storage
from .subscription import Subscription, Target


class SubscriptionManager:
    """订阅管理器"""

    def __init__(self, storage: Storage, plugin_config=None):
        self.storage = storage
        self.plugin_config = plugin_config
        self.subscriptions: list[Subscription] = []
        # 不在这里加载，等待 plugin_config 准备好

    def initialize(self):
        """初始化并加载订阅（在 plugin_config 准备好后调用）"""
        self.load()

    def load(self):
        """加载订阅（从配置系统）"""
        if not self.plugin_config:
            logger.warning("配置对象未准备好，从旧存储加载")
            self.subscriptions = self.storage.load_subscriptions()
            return

        # 从配置系统加载
        config_subs = self.plugin_config.get("subscriptions", [])

        if config_subs:
            self.subscriptions = []
            for config_sub in config_subs:
                try:
                    sub = self._config_to_subscription(config_sub)
                    self.subscriptions.append(sub)
                except Exception as e:
                    logger.error(f"加载订阅失败: {e}")
            logger.info(f"✅ 从配置系统加载了 {len(self.subscriptions)} 个订阅")
        else:
            # 迁移旧数据
            legacy_subs = self.storage.load_subscriptions()
            if legacy_subs:
                self.subscriptions = legacy_subs
                logger.info(f"🔄 检测到旧数据，自动迁移 {len(legacy_subs)} 个订阅")
                self.save()  # 立即保存到配置系统
            else:
                self.subscriptions = []
                logger.info("没有找到订阅")

    def save(self):
        """保存订阅（到配置系统）"""
        if not self.plugin_config:
            logger.warning("配置对象未准备好，保存到旧存储")
            self.storage.save_subscriptions(self.subscriptions)
            return

        # 转换为配置格式
        config_subs = []
        for sub in self.subscriptions:
            config_sub = self._subscription_to_config(sub)
            config_subs.append(config_sub)

        # 更新配置
        self.plugin_config["subscriptions"] = config_subs

        # 保存配置文件
        if hasattr(self.plugin_config, "save_config"):
            self.plugin_config.save_config()
            logger.info(f"✅ 已保存 {len(self.subscriptions)} 个订阅到配置系统")
        else:
            logger.error("配置对象没有 save_config 方法")

    def _config_to_subscription(self, config: dict) -> Subscription:
        """从配置格式转换为订阅对象"""
        from datetime import datetime

        # 解析targets
        targets = []
        for t_data in config.get("targets", []):
            target = Target.from_dict(t_data)
            targets.append(target)

        # 创建订阅
        sub = Subscription(
            id=config.get("id"),
            name=config.get("name", ""),
            url=config.get("url", ""),
            enabled=config.get("enabled", True),
            targets=targets,
            template=config.get("custom_template"),
            max_items=config.get("max_items", 1),
        )

        # 恢复时间戳
        if config.get("created_at"):
            try:
                sub.created_at = datetime.fromisoformat(config["created_at"])
            except Exception:
                pass
        if config.get("last_check"):
            try:
                sub.last_check = datetime.fromisoformat(config["last_check"])
            except Exception:
                pass
        if config.get("last_push"):
            try:
                sub.last_push = datetime.fromisoformat(config["last_push"])
            except Exception:
                pass

        return sub

    def _subscription_to_config(self, sub: Subscription) -> dict:
        """从订阅对象转换为配置格式"""
        return {
            "id": sub.id,
            "name": sub.name,
            "url": sub.url,
            "enabled": sub.enabled,
            "max_items": sub.max_items,
            "custom_template": sub.template or "",
            "targets": [t.to_dict() for t in sub.targets],
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "last_check": sub.last_check.isoformat() if sub.last_check else None,
            "last_push": sub.last_push.isoformat() if sub.last_push else None,
        }

    def add(self, name: str, url: str, targets: list[Target]) -> Subscription:
        """添加订阅

        Args:
            name: 订阅名称
            url: RSS地址
            targets: 推送目标列表

        Returns:
            新创建的订阅对象
        """
        sub = Subscription(name=name, url=url, targets=targets)
        self.subscriptions.append(sub)
        self.save()
        logger.info(f"添加订阅: {name} ({url})")
        return sub

    def delete(self, sub_id: str) -> bool:
        """删除订阅

        Args:
            sub_id: 订阅ID（支持部分匹配）

        Returns:
            是否删除成功
        """
        # 查找订阅（支持部分ID匹配）
        sub = self.get(sub_id)
        if not sub:
            return False

        self.subscriptions = [s for s in self.subscriptions if s.id != sub.id]
        self.save()
        logger.info(f"删除订阅: {sub.name} ({sub.id})")
        return True

    def get(self, sub_id: str) -> Subscription | None:
        """获取订阅（支持部分ID匹配）

        Args:
            sub_id: 订阅ID或ID前缀

        Returns:
            订阅对象，未找到返回None
        """
        # 精确匹配
        for sub in self.subscriptions:
            if sub.id == sub_id:
                return sub

        # 前缀匹配
        matches = [sub for sub in self.subscriptions if sub.id.startswith(sub_id)]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            logger.warning(f"ID前缀 {sub_id} 匹配到多个订阅")

        return None

    def get_by_name(self, name: str) -> Subscription | None:
        """根据名称获取订阅

        Args:
            name: 订阅名称

        Returns:
            订阅对象，未找到返回None
        """
        for sub in self.subscriptions:
            if sub.name == name:
                return sub
        return None

    def list_all(self) -> list[Subscription]:
        """列出所有订阅

        Returns:
            订阅列表
        """
        return self.subscriptions

    def list_enabled(self) -> list[Subscription]:
        """列出所有启用的订阅

        Returns:
            已启用的订阅列表
        """
        return [sub for sub in self.subscriptions if sub.enabled]

    def enable(self, sub_id: str) -> bool:
        """启用订阅

        Args:
            sub_id: 订阅ID

        Returns:
            是否操作成功
        """
        sub = self.get(sub_id)
        if sub:
            sub.enabled = True
            self.save()
            logger.info(f"启用订阅: {sub.name}")
            return True
        return False

    def disable(self, sub_id: str) -> bool:
        """禁用订阅

        Args:
            sub_id: 订阅ID

        Returns:
            是否操作成功
        """
        sub = self.get(sub_id)
        if sub:
            sub.enabled = False
            self.save()
            logger.info(f"禁用订阅: {sub.name}")
            return True
        return False

    def update_subscription(self, sub: Subscription):
        """更新订阅信息

        Args:
            sub: 订阅对象
        """
        for i, s in enumerate(self.subscriptions):
            if s.id == sub.id:
                self.subscriptions[i] = sub
                self.save()
                return

    def add_target(self, sub_id: str, target: Target) -> bool:
        """为订阅添加推送目标

        Args:
            sub_id: 订阅ID
            target: 推送目标

        Returns:
            是否操作成功
        """
        sub = self.get(sub_id)
        if sub:
            # 检查是否已存在
            for t in sub.targets:
                if (
                    t.type == target.type
                    and t.platform == target.platform
                    and t.id == target.id
                ):
                    logger.warning("推送目标已存在")
                    return False

            sub.targets.append(target)
            self.save()
            logger.info(f"为订阅 {sub.name} 添加推送目标")
            return True
        return False

    def remove_target(self, sub_id: str, target_id: str) -> bool:
        """从订阅中移除推送目标

        Args:
            sub_id: 订阅ID
            target_id: 目标ID

        Returns:
            是否操作成功
        """
        sub = self.get(sub_id)
        if sub:
            original_len = len(sub.targets)
            sub.targets = [t for t in sub.targets if t.id != target_id]
            if len(sub.targets) < original_len:
                self.save()
                logger.info(f"从订阅 {sub.name} 移除推送目标")
                return True
        return False
