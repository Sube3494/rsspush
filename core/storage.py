"""数据持久化模块"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger

from .subscription import Subscription, Target


class Storage:
    """数据存储管理器"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.subs_file = self.data_dir / "subscriptions.json"
        self.db_file = self.data_dir / "pushed_items.db"
        self._init_db()
        self.cleanup_old_records(30)  # 启动时清理 30 天前的旧记录

    def _init_db(self):
        """初始化SQLite数据库"""
        import os
        
        # 预检查权限 (由于是 Linux 环境，目录权限至关重要)
        if self.db_file.exists() and not os.access(self.db_file, os.W_OK):
            logger.error(f"❌ 数据库文件无写入权限: {self.db_file}")
        if not os.access(self.data_dir, os.W_OK):
            logger.error(f"❌ 数据目录无写入权限: {self.data_dir}。SQLite 迁移需要目录写入权限来创建临时文件。")

        conn = sqlite3.connect(str(self.db_file))
        cursor = conn.cursor()
        
        try:
            # 1. 创建推送记录表 (GUID查重库)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pushed_items (
                    guid TEXT,
                    subscription_id TEXT,
                    pub_date TIMESTAMP,
                    PRIMARY KEY (guid, subscription_id)
                )
            """)
            
            # 2. 创建订阅配置表 (核心配置)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    last_pub_date TIMESTAMP,
                    last_error TEXT,
                    template TEXT,
                    filters TEXT,
                    max_items INTEGER DEFAULT 1
                )
            """)

            # 3. 彻底清理冗余列 (减脂手术：移除所有统计计数器、创建时间、活动时间等)
            cursor.execute("PRAGMA table_info(subscriptions)")
            current_columns = {col[1] for col in cursor.fetchall()}
            core_cols = {'id', 'name', 'url', 'enabled', 'last_pub_date', 'last_error', 'template', 'filters', 'max_items'}
            
            if not all(col in current_columns for col in core_cols) or len(current_columns) > len(core_cols):
                logger.info("检测到数据库存在冗余统计项或缺少核心列，正在执行“深度减脂”迁移...")
                
                # 开启事务
                cursor.execute("BEGIN TRANSACTION")
                try:
                    # 安全重命名原表
                    cursor.execute("ALTER TABLE subscriptions RENAME TO subs_old")
                    
                    # 创建纯净的新表
                    cursor.execute("""
                        CREATE TABLE subscriptions (
                            id TEXT PRIMARY KEY,
                            name TEXT NOT NULL,
                            url TEXT NOT NULL,
                            enabled INTEGER DEFAULT 1,
                            last_pub_date TIMESTAMP,
                            last_error TEXT,
                            template TEXT,
                            filters TEXT,
                            max_items INTEGER DEFAULT 1
                        )
                    """)
                    
                    # 迁移核心数据
                    migrate_cols = [col for col in core_cols if col in current_columns]
                    cols_str = ", ".join(migrate_cols)
                    cursor.execute(f"INSERT INTO subscriptions ({cols_str}) SELECT {cols_str} FROM subs_old")
                    
                    # 删除旧表
                    cursor.execute("DROP TABLE subs_old")
                    conn.commit()
                    logger.info("数据库 subscriptions 表深度清理完成")
                except Exception as e:
                    conn.rollback()
                    logger.error(f"迁移 subscriptions 失败: {e}")
                    raise

            # 4. 初始化推送目标表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE
                )
            """)
            
            # 5. 清理 pushed_items 的冗余列 (targets)
            cursor.execute("PRAGMA table_info(pushed_items)")
            pushed_columns = {col[1] for col in cursor.fetchall()}
            if 'targets' in pushed_columns:
                logger.info("清理 pushed_items 冗余列...")
                cursor.execute("BEGIN TRANSACTION")
                try:
                    cursor.execute("ALTER TABLE pushed_items RENAME TO pushed_old")
                    cursor.execute("""
                        CREATE TABLE pushed_items (
                            guid TEXT,
                            subscription_id TEXT,
                            pub_date TIMESTAMP,
                            PRIMARY KEY (guid, subscription_id)
                        )
                    """)
                    cursor.execute("INSERT INTO pushed_items SELECT guid, subscription_id, pub_date FROM pushed_old")
                    cursor.execute("DROP TABLE pushed_old")
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error(f"清理 pushed_items 失败: {e}")
                    raise

            conn.commit()
        except sqlite3.OperationalError as e:
            if "readonly" in str(e).lower():
                logger.error(f"❌ 数据库由于权限问题无法写入。请检查 {self.data_dir} 及其文件的权限。")
            raise e
        finally:
            conn.close()

        # 6. JSON 迁移 (保留基本的兼容性逻辑)
        if self.subs_file.exists():
            cursor.execute("SELECT COUNT(*) FROM subscriptions")
            if cursor.fetchone()[0] == 0:
                logger.info("迁移 legacy subscriptions.json...")
                try:
                    with open(self.subs_file, encoding="utf-8") as f:
                        subs_data = json.load(f)
                    for sub_data in subs_data:
                        cursor.execute("""
                            INSERT INTO subscriptions (id, name, url, enabled, last_error, template, filters, max_items)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            sub_data.get("id"), sub_data.get("name"), sub_data.get("url"),
                            1 if sub_data.get("enabled", True) else 0,
                            sub_data.get("stats", {}).get("last_error"),
                            sub_data.get("template"),
                            json.dumps(sub_data.get("filters", {})),
                            sub_data.get("max_items", 1)
                        ))
                    logger.info("JSON 数据迁移完成")
                    self.subs_file.rename(self.subs_file.with_suffix('.json.bak'))
                except Exception as e:
                    logger.error(f"JSON 迁移失败: {e}")
                    conn.rollback()

        conn.commit()
        conn.close()

    def load_subscriptions(self) -> list[Subscription]:
        """加载所有订阅"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, url, enabled, last_pub_date, last_error, template, filters, max_items FROM subscriptions")
            
            subscriptions = []
            for row in cursor.fetchall():
                last_pub_date = datetime.fromisoformat(row[4]) if row[4] else None
                filters = {}
                if row[7]:
                    try: filters = json.loads(row[7])
                    except: pass
                
                # 加载目标
                cursor.execute("SELECT type, platform, target_id FROM targets WHERE subscription_id = ?", (row[0],))
                targets = [Target(type=t[0], platform=t[1], id=t[2]) for t in cursor.fetchall()]
                
                sub = Subscription(
                    id=row[0], name=row[1], url=row[2], enabled=bool(row[3]),
                    last_pub_date=last_pub_date, last_error=row[5], targets=targets,
                    template=row[6], filters=filters, max_items=row[8]
                )
                subscriptions.append(sub)
            conn.close()
            return subscriptions
        except Exception as e:
            logger.error(f"加载订阅失败: {e}")
            return []

    def save_subscriptions(self, subs: list[Subscription]):
        """保存订阅列表"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("DELETE FROM subscriptions")
            cursor.execute("DELETE FROM targets")
            
            for sub in subs:
                cursor.execute("""
                    INSERT INTO subscriptions (id, name, url, enabled, last_pub_date, last_error, template, filters, max_items)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sub.id, sub.name, sub.url, 1 if sub.enabled else 0,
                    sub.last_pub_date.isoformat() if sub.last_pub_date else None,
                    sub.last_error, sub.template,
                    json.dumps(sub.filters) if sub.filters else None,
                    sub.max_items
                ))
                for target in sub.targets:
                    cursor.execute("INSERT INTO targets (subscription_id, type, platform, target_id) VALUES (?, ?, ?, ?)",
                                 (sub.id, target.type, target.platform, target.id))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            if conn:
                conn.rollback()
                conn.close()

    def is_pushed(self, guid: str, sub_id: str) -> bool:
        """查重检查"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM pushed_items WHERE guid = ? AND subscription_id = ?", (guid, sub_id))
            res = cursor.fetchone()
            conn.close()
            return res is not None
        except Exception:
            return False

    def mark_pushed(self, guid: str, sub_id: str, pub_date: datetime | None = None):
        """标记推送完成"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO pushed_items (guid, subscription_id, pub_date) VALUES (?, ?, ?)",
                         (guid, sub_id, pub_date.isoformat() if pub_date else None))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"标记推送状态失败: {e}")

    def cleanup_old_records(self, days: int = 30):
        """定期清理旧 GUID 记录"""
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pushed_items WHERE pub_date < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                logger.info(f"清理了 {deleted} 条超过 {days} 天的推送记录")
        except Exception as e:
            logger.error(f"清理失败: {e}")
