"""数据持久化模块"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger

from .subscription import Subscription


class Storage:
    """数据存储管理器"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.subs_file = self.data_dir / "subscriptions.json"
        self.db_file = self.data_dir / "pushed_items.db"
        self._init_db()

    def _init_db(self):
        """初始化SQLite数据库"""
        conn = sqlite3.connect(str(self.db_file))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pushed_items (
                guid TEXT,
                subscription_id TEXT,
                pushed_at TIMESTAMP,
                targets TEXT,
                PRIMARY KEY (guid, subscription_id)
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"数据库初始化完成: {self.db_file}")

    def load_subscriptions(self) -> list[Subscription]:
        """加载所有订阅"""
        if not self.subs_file.exists():
            logger.info("订阅文件不存在，返回空列表")
            return []

        try:
            with open(self.subs_file, encoding="utf-8") as f:
                data = json.load(f)
                subscriptions = [Subscription.from_dict(sub) for sub in data]
                logger.info(f"加载了 {len(subscriptions)} 个订阅")
                return subscriptions
        except Exception as e:
            logger.error(f"加载订阅失败: {e}")
            return []

    def save_subscriptions(self, subs: list[Subscription]):
        """保存订阅列表"""
        try:
            data = [sub.to_dict() for sub in subs]
            with open(self.subs_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"保存了 {len(subs)} 个订阅")
        except Exception as e:
            logger.error(f"保存订阅失败: {e}")

    def is_pushed(self, guid: str, sub_id: str) -> bool:
        """检查是否已推送"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM pushed_items WHERE guid = ? AND subscription_id = ?",
                (guid, sub_id),
            )
            result = cursor.fetchone()
            conn.close()
            return result is not None
        except Exception as e:
            logger.error(f"检查推送状态失败: {e}")
            return False

    def mark_pushed(self, guid: str, sub_id: str, targets: list[str]):
        """标记为已推送"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO pushed_items (guid, subscription_id, pushed_at, targets) VALUES (?, ?, ?, ?)",
                (guid, sub_id, datetime.now().isoformat(), ",".join(targets)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"标记推送状态失败: {e}")

    def cleanup_old_records(self, days: int = 7):
        """清理旧记录"""
        try:
            cutoff = datetime.now() - timedelta(days=days)
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM pushed_items WHERE pushed_at < ?", (cutoff.isoformat(),)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                logger.info(f"清理了 {deleted} 条旧记录")
        except Exception as e:
            logger.error(f"清理旧记录失败: {e}")
