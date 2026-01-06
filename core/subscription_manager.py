"""订阅管理器模块"""

from astrbot.api import logger

from .storage import Storage
from .subscription import Subscription, Target


class SubscriptionManager:
    """订阅管理器"""

    def __init__(self, storage: Storage):
        self.storage = storage
        self.subscriptions: list[Subscription] = []
        self.load()

    def load(self):
        """加载订阅"""
        self.subscriptions = self.storage.load_subscriptions()
        logger.info(f"订阅管理器加载了 {len(self.subscriptions)} 个订阅")

    def save(self):
        """保存订阅"""
        self.storage.save_subscriptions(self.subscriptions)

    def add(self, name: str, url: str, targets: list[Target]) -> Subscription:
        """添加订阅

        Args:
            name: 订阅名称
            url: RSS地址
            targets: 推送目标列表

        Returns:
            新创建的订阅对象
            
        Raises:
            ValueError: 当URL已存在时
        """
        # 检查URL是否已存在
        for existing_sub in self.subscriptions:
            if existing_sub.url == url:
                raise ValueError(f"订阅已存在：{existing_sub.name} ({existing_sub.id[:8]}...)")
        
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
            target_id: 目标ID（支持完整ID或后缀匹配，如 12345678）

        Returns:
            是否操作成功
        """
        sub = self.get(sub_id)
        if sub:
            original_len = len(sub.targets)
            
            # 1. 尝试精确匹配
            sub.targets = [t for t in sub.targets if t.id != target_id]
            if len(sub.targets) < original_len:
                self.save()
                logger.info(f"从订阅 {sub.name} 移除推送目标 (精确匹配: {target_id})")
                return True
            
            # 2. 尝试后缀匹配（针对统一 ID，如 platform:type:id）
            # 匹配 :target_id 结尾的目标
            suffix = ":" + target_id
            sub.targets = [t for t in sub.targets if not t.id.endswith(suffix)]
            if len(sub.targets) < original_len:
                self.save()
                logger.info(f"从订阅 {sub.name} 移除推送目标 (后缀匹配: {target_id})")
                return True
        return False
