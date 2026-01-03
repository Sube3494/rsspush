"""订阅数据模型"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Target:
    """推送目标"""

    type: str  # 'private' or 'group'
    platform: str  # 'qq', 'wechat', 'telegram' etc.
    id: str  # 目标ID

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Target":
        """从字典创建"""
        return cls(**data)


@dataclass
class SubscriptionStats:
    """订阅统计"""

    total_checks: int = 0
    success_checks: int = 0
    total_pushes: int = 0
    success_pushes: int = 0
    last_error: str | None = None

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SubscriptionStats":
        """从字典创建"""
        return cls(**data)


@dataclass
class Subscription:
    """RSS订阅"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    url: str = ""
    enabled: bool = True
    targets: list[Target] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_check: datetime | None = None
    last_push: datetime | None = None
    last_pub_date: datetime | None = None  # 最后一条推送动态的真实发布时间 (基准线)
    stats: SubscriptionStats = field(default_factory=SubscriptionStats)

    # P1功能字段
    template: str | None = None
    filters: dict = field(default_factory=dict)
    max_items: int = 1

    # P2功能字段
    priority: int = 0
    cron: str | None = None

    def to_dict(self) -> dict:
        """转换为字典"""
        data = {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "enabled": self.enabled,
            "targets": [t.to_dict() for t in self.targets],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "last_push": self.last_push.isoformat() if self.last_push else None,
            "last_pub_date": self.last_pub_date.isoformat() if self.last_pub_date else None,
            "stats": self.stats.to_dict(),
            "template": self.template,
            "filters": self.filters,
            "max_items": self.max_items,
            "priority": self.priority,
            "cron": self.cron,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Subscription":
        """从字典创建"""
        targets = [Target.from_dict(t) for t in data.get("targets", [])]
        stats = SubscriptionStats.from_dict(data.get("stats", {}))

        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])

        last_check = None
        if data.get("last_check"):
            last_check = datetime.fromisoformat(data["last_check"])

        last_push = None
        if data.get("last_push"):
            last_push = datetime.fromisoformat(data["last_push"])

        last_pub_date = None
        if data.get("last_pub_date"):
            last_pub_date = datetime.fromisoformat(data["last_pub_date"])

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            url=data.get("url", ""),
            enabled=data.get("enabled", True),
            targets=targets,
            created_at=created_at or datetime.now(),
            last_check=last_check,
            last_push=last_push,
            last_pub_date=last_pub_date,
            stats=stats,
            template=data.get("template"),
            filters=data.get("filters", {}),
            max_items=data.get("max_items", 1),
            priority=data.get("priority", 0),
            cron=data.get("cron"),
        )
