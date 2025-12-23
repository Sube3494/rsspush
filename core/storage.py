"""数据持久化模块"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger

from .subscription import Subscription, SubscriptionStats, Target


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
        
        # 创建推送记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pushed_items (
                guid TEXT,
                subscription_id TEXT,
                pub_date TIMESTAMP,
                targets TEXT,
                PRIMARY KEY (guid, subscription_id)
            )
        """)
        
        # 创建订阅表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP,
                last_check TIMESTAMP,
                last_push TIMESTAMP,
                total_checks INTEGER DEFAULT 0,
                success_checks INTEGER DEFAULT 0,
                total_pushes INTEGER DEFAULT 0,
                success_pushes INTEGER DEFAULT 0,
                last_error TEXT,
                template TEXT,
                filters TEXT,
                max_items INTEGER DEFAULT 1,
                priority INTEGER DEFAULT 0,
                cron TEXT
            )
        """)
        
        # 创建推送目标表
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
        
        # 检查并迁移旧的 pushed_items 表结构
        cursor.execute("PRAGMA table_info(pushed_items)")
        columns = {col[1] for col in cursor.fetchall()}
        
        if 'pushed_at' in columns and 'pub_date' not in columns:
            logger.info("检测到旧 pushed_items 表结构，正在升级...")
            cursor.execute("ALTER TABLE pushed_items RENAME TO pushed_items_old")
            cursor.execute("""
                CREATE TABLE pushed_items (
                    guid TEXT,
                    subscription_id TEXT,
                    pub_date TIMESTAMP,
                    targets TEXT,
                    PRIMARY KEY (guid, subscription_id)
                )
            """)
            cursor.execute("""
                INSERT INTO pushed_items (guid, subscription_id, pub_date, targets)
                SELECT guid, subscription_id, NULL, targets FROM pushed_items_old
            """)
            cursor.execute("DROP TABLE pushed_items_old")
            logger.info("pushed_items 表升级完成")
        
        # 迁移 JSON 数据到数据库
        if self.subs_file.exists():
            # 检查数据库是否已有数据
            cursor.execute("SELECT COUNT(*) FROM subscriptions")
            count = cursor.fetchone()[0]
            
            if count == 0:
                # 数据库为空，迁移 JSON 数据
                logger.info("检测到 subscriptions.json，开始迁移到数据库...")
                try:
                    import json
                    with open(self.subs_file, encoding="utf-8") as f:
                        subs_data = json.load(f)
                    
                    for sub_data in subs_data:
                        # 插入订阅
                        cursor.execute("""
                            INSERT INTO subscriptions 
                            (id, name, url, enabled, created_at, last_check, last_push,
                             total_checks, success_checks, total_pushes, success_pushes, last_error,
                             template, filters, max_items, priority, cron)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            sub_data.get("id"),
                            sub_data.get("name"),
                            sub_data.get("url"),
                            1 if sub_data.get("enabled", True) else 0,
                            sub_data.get("created_at"),
                            sub_data.get("last_check"),
                            sub_data.get("last_push"),
                            sub_data.get("stats", {}).get("total_checks", 0),
                            sub_data.get("stats", {}).get("success_checks", 0),
                            sub_data.get("stats", {}).get("total_pushes", 0),
                            sub_data.get("stats", {}).get("success_pushes", 0),
                            sub_data.get("stats", {}).get("last_error"),
                            sub_data.get("template"),
                            json.dumps(sub_data.get("filters", {})),
                            sub_data.get("max_items", 1),
                            sub_data.get("priority", 0),
                            sub_data.get("cron")
                        ))
                        
                        # 插入推送目标
                        for target in sub_data.get("targets", []):
                            cursor.execute("""
                                INSERT INTO targets (subscription_id, type, platform, target_id)
                                VALUES (?, ?, ?, ?)
                            """, (
                                sub_data.get("id"),
                                target.get("type"),
                                target.get("platform"),
                                target.get("id")
                            ))
                    
                    logger.info(f"成功迁移 {len(subs_data)} 个订阅到数据库")
                    
                    # 备份并删除 JSON 文件
                    import shutil
                    backup_file = self.subs_file.with_suffix('.json.bak')
                    shutil.copy(self.subs_file, backup_file)
                    logger.info(f"已备份 JSON 文件到: {backup_file}")
                    
                except Exception as e:
                    logger.error(f"迁移 JSON 数据失败: {e}")
                    conn.rollback()
                    raise
        
        conn.commit()
        conn.close()
        logger.info(f"数据库初始化完成: {self.db_file}")

    def load_subscriptions(self) -> list[Subscription]:
        """加载所有订阅（从数据库）"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            
            # 加载所有订阅
            cursor.execute("""
                SELECT id, name, url, enabled, created_at, last_check, last_push,
                       total_checks, success_checks, total_pushes, success_pushes, last_error,
                       template, filters, max_items, priority, cron
                FROM subscriptions
            """)
            
            subscriptions = []
            for row in cursor.fetchall():
                # 解析时间
                created_at = datetime.fromisoformat(row[4]) if row[4] else datetime.now()
                last_check = datetime.fromisoformat(row[5]) if row[5] else None
                last_push = datetime.fromisoformat(row[6]) if row[6] else None
                
                # 解析 filters（JSON 字符串）
                filters = {}
                if row[13]:
                    try:
                        filters = json.loads(row[13])
                    except:
                        pass
                
                # 创建统计对象
                stats = SubscriptionStats(
                    total_checks=row[7],
                    success_checks=row[8],
                    total_pushes=row[9],
                    success_pushes=row[10],
                    last_error=row[11]
                )
                
                # 加载推送目标
                cursor.execute("""
                    SELECT type, platform, target_id FROM targets
                    WHERE subscription_id = ?
                """, (row[0],))
                
                targets = [
                    Target(type=t[0], platform=t[1], id=t[2])
                    for t in cursor.fetchall()
                ]
                
                # 创建订阅对象
                sub = Subscription(
                    id=row[0],
                    name=row[1],
                    url=row[2],
                    enabled=bool(row[3]),
                    created_at=created_at,
                    last_check=last_check,
                    last_push=last_push,
                    stats=stats,
                    targets=targets,
                    template=row[12],
                    filters=filters,
                    max_items=row[14],
                    priority=row[15],
                    cron=row[16]
                )
                subscriptions.append(sub)
            
            conn.close()
            logger.info(f"从数据库加载了 {len(subscriptions)} 个订阅")
            return subscriptions
            
        except Exception as e:
            logger.error(f"加载订阅失败: {e}")
            return []

    def save_subscriptions(self, subs: list[Subscription]):
        """保存订阅列表（到数据库）"""
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            
            # 使用事务
            cursor.execute("BEGIN TRANSACTION")
            
            # 清空旧数据
            cursor.execute("DELETE FROM subscriptions")
            cursor.execute("DELETE FROM targets")
            
            # 插入新数据
            for sub in subs:
                # 插入订阅
                cursor.execute("""
                    INSERT INTO subscriptions 
                    (id, name, url, enabled, created_at, last_check, last_push,
                     total_checks, success_checks, total_pushes, success_pushes, last_error,
                     template, filters, max_items, priority, cron)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sub.id,
                    sub.name,
                    sub.url,
                    1 if sub.enabled else 0,
                    sub.created_at.isoformat() if sub.created_at else None,
                    sub.last_check.isoformat() if sub.last_check else None,
                    sub.last_push.isoformat() if sub.last_push else None,
                    sub.stats.total_checks,
                    sub.stats.success_checks,
                    sub.stats.total_pushes,
                    sub.stats.success_pushes,
                    sub.stats.last_error,
                    sub.template,
                    json.dumps(sub.filters) if sub.filters else None,
                    sub.max_items,
                    sub.priority,
                    sub.cron
                ))
                
                # 插入推送目标
                for target in sub.targets:
                    cursor.execute("""
                        INSERT INTO targets (subscription_id, type, platform, target_id)
                        VALUES (?, ?, ?, ?)
                    """, (sub.id, target.type, target.platform, target.id))
            
            conn.commit()
            conn.close()
            logger.info(f"保存了 {len(subs)} 个订阅到数据库")
        except Exception as e:
            logger.error(f"保存订阅失败: {e}")
            if conn:
                conn.rollback()
                conn.close()

    def is_pushed(self, guid: str, sub_id: str) -> bool:
        """检查是否已推送"""
        if not guid or not sub_id:
            logger.warning(f"GUID或订阅ID为空: guid={guid}, sub_id={sub_id}")
            return False
            
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM pushed_items WHERE guid = ? AND subscription_id = ?",
                (guid, sub_id),
            )
            result = cursor.fetchone()
            conn.close()
            is_pushed = result is not None
            if is_pushed:
                logger.debug(f"条目已推送: guid={guid[:50]}..., sub_id={sub_id[:8]}...")
            return is_pushed
        except sqlite3.Error as e:
            logger.error(f"数据库查询失败 (guid={guid[:50]}..., sub_id={sub_id[:8]}...): {e}")
            # 数据库错误时返回False，但记录详细错误以便排查
            return False
        except Exception as e:
            logger.error(f"检查推送状态失败 (guid={guid[:50]}..., sub_id={sub_id[:8]}...): {e}", exc_info=True)
            return False

    def mark_pushed(self, guid: str, sub_id: str, targets: list[str], pub_date: datetime | None = None):
        """标记为已推送
        
        Args:
            guid: 条目唯一标识
            sub_id: 订阅ID
            targets: 推送目标列表
            pub_date: 动态的发布时间
        """
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO pushed_items 
                   (guid, subscription_id, pub_date, targets) 
                   VALUES (?, ?, ?, ?)""",
                (guid, sub_id, 
                 pub_date.isoformat() if pub_date else None,
                 ",".join(targets)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"标记推送状态失败: {e}")

    def get_last_pushed_pub_date(self, sub_id: str) -> datetime | None:
        """获取最后推送的动态的发布时间
        
        Args:
            sub_id: 订阅ID
            
        Returns:
            最后推送动态的发布时间，如果没有则返回None
        """
        try:
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            # 获取最后推送的记录（按发布时间排序）
            cursor.execute(
                """SELECT pub_date FROM pushed_items 
                   WHERE subscription_id = ? AND pub_date IS NOT NULL
                   ORDER BY pub_date DESC LIMIT 1""",
                (sub_id,),
            )
            result = cursor.fetchone()
            conn.close()
            
            if result and result[0]:
                # 解析 ISO 格式的时间字符串
                return datetime.fromisoformat(result[0])
            return None
        except Exception as e:
            logger.error(f"获取最后推送发布时间失败: {e}")
            return None

    def cleanup_old_records(self, days: int = 7):
        """清理旧记录（基于发布时间）"""
        try:
            cutoff = datetime.now() - timedelta(days=days)
            conn = sqlite3.connect(str(self.db_file))
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM pushed_items WHERE pub_date < ?", (cutoff.isoformat(),)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            if deleted > 0:
                logger.info(f"清理了 {deleted} 条旧记录")
        except Exception as e:
            logger.error(f"清理旧记录失败: {e}")
