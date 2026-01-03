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
class Subscription:
    """RSS订阅"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    url: str = ""
    enabled: bool = True
    targets: list[Target] = field(default_factory=list)
    last_pub_date: datetime | None = None  # 最后一条推送动态的真实发布时间 (基准线)
    last_error: str | None = None  # 最后一次运行错误信息

    # 功能字段
    template: str | None = None
    filters: dict = field(default_factory=dict)
    max_items: int = 1

    def to_dict(self) -> dict:
        """转换为字典"""
        data = {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "enabled": self.enabled,
            "targets": [t.to_dict() for t in self.targets],
            "last_pub_date": self.last_pub_date.isoformat() if self.last_pub_date else None,
            "last_error": self.last_error,
            "template": self.template,
            "filters": self.filters,
            "max_items": self.max_items,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Subscription":
        """从字典创建"""
        targets = [Target.from_dict(t) for t in data.get("targets", [])]

        last_pub_date = None
        if data.get("last_pub_date"):
            last_pub_date = datetime.fromisoformat(data["last_pub_date"])

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            url=data.get("url", ""),
            enabled=data.get("enabled", True),
            targets=targets,
            last_pub_date=last_pub_date,
            last_error=data.get("last_error"),
            template=data.get("template"),
            filters=data.get("filters", {}),
            max_items=data.get("max_items", 1),
        )
