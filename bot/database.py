"""SQLite 数据层 — 用户管理与下载日志"""

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class User:
    telegram_id: int
    username: Optional[str]
    display_name: str
    role: str  # owner, user, pending, banned
    applied_at: Optional[str]
    approved_at: Optional[str]
    approved_by: Optional[int]


class Database:
    def __init__(self, db_path: str):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_tables()
        # 收紧数据库文件权限（仅所有者可读写）
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass

    def _init_tables(self):
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id   INTEGER PRIMARY KEY,
                username      TEXT,
                display_name  TEXT,
                role          TEXT NOT NULL DEFAULT 'pending',
                applied_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at   TIMESTAMP,
                approved_by   INTEGER
            );

            CREATE TABLE IF NOT EXISTS download_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                torrent_title TEXT,
                torrent_size  TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS settings (
                key       TEXT PRIMARY KEY,
                value     TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.conn.commit()
        self._migrate_tables()

    def _migrate_tables(self):
        """增量迁移：为已有表添加新列。"""
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(download_logs)")
        columns = {row[1] for row in cur.fetchall()}
        if "task_id" not in columns:
            cur.execute("ALTER TABLE download_logs ADD COLUMN task_id TEXT")
            self.conn.commit()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            telegram_id=row["telegram_id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            applied_at=row["applied_at"],
            approved_at=row["approved_at"],
            approved_by=row["approved_by"],
        )

    # ------------------------------------------------------------------
    # 用户操作
    # ------------------------------------------------------------------

    def init_owner(self, owner_id: int):
        """首次启动时将 Owner 写入数据库（幂等）。"""
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO users (telegram_id, username, display_name, role)
            VALUES (?, '', 'Owner', 'owner')
            ON CONFLICT(telegram_id) DO UPDATE SET role = 'owner'
            """,
            (owner_id,),
        )
        self.conn.commit()

    def get_user(self, telegram_id: int) -> Optional[User]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        return self._row_to_user(row) if row else None

    def apply_user(
        self, telegram_id: int, username: str, display_name: str
    ) -> bool:
        """提交入群申请。如果已存在记录则返回 False。"""
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO users (telegram_id, username, display_name, role)
                VALUES (?, ?, ?, 'pending')
                """,
                (telegram_id, username, display_name),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def approve_user(self, telegram_id: int, approved_by: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET role = 'user', approved_at = CURRENT_TIMESTAMP, approved_by = ?
            WHERE telegram_id = ? AND role = 'pending'
            """,
            (approved_by, telegram_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def reject_user(self, telegram_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "DELETE FROM users WHERE telegram_id = ? AND role = 'pending'",
            (telegram_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def ban_user(self, telegram_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE users SET role = 'banned'
            WHERE telegram_id = ? AND role != 'owner'
            """,
            (telegram_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def unban_user(self, telegram_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE users SET role = 'user'
            WHERE telegram_id = ? AND role = 'banned'
            """,
            (telegram_id,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_pending_users(self) -> List[User]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE role = 'pending' ORDER BY applied_at")
        return [self._row_to_user(r) for r in cur.fetchall()]

    def get_approved_users(self) -> List[User]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM users WHERE role IN ('user', 'owner') ORDER BY approved_at"
        )
        return [self._row_to_user(r) for r in cur.fetchall()]

    def get_all_users(self) -> List[User]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users ORDER BY applied_at")
        return [self._row_to_user(r) for r in cur.fetchall()]

    def log_download(self, telegram_id: int, title: str, size: str,
                     task_id: str = None):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO download_logs (telegram_id, torrent_title, torrent_size, task_id)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, title, size, task_id),
        )
        self.conn.commit()

    def get_download_by_task_id(self, task_id: str) -> Optional[dict]:
        """根据 task_id 查找下载记录。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT telegram_id, torrent_title, torrent_size, created_at "
            "FROM download_logs WHERE task_id = ?",
            (task_id,),
        )
        row = cur.fetchone()
        if row:
            return {
                "telegram_id": row["telegram_id"],
                "torrent_title": row["torrent_title"],
                "torrent_size": row["torrent_size"],
                "created_at": row["created_at"],
            }
        return None

    def get_user_task_ids(self, telegram_id: int) -> List[str]:
        """获取某用户所有有 task_id 的下载记录的 task_id 列表。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT task_id FROM download_logs "
            "WHERE telegram_id = ? AND task_id IS NOT NULL AND task_id != ''",
            (telegram_id,),
        )
        return [row["task_id"] for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # 权限判断
    # ------------------------------------------------------------------

    def is_authorized(self, telegram_id: int) -> bool:
        """user 或 owner 视为已授权。"""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM users WHERE telegram_id = ? AND role IN ('user', 'owner')",
            (telegram_id,),
        )
        return cur.fetchone() is not None

    def is_owner(self, telegram_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT 1 FROM users WHERE telegram_id = ? AND role = 'owner'",
            (telegram_id,),
        )
        return cur.fetchone() is not None

    # ------------------------------------------------------------------
    # 设置存储
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> Optional[str]:
        """获取设置值。"""
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str):
        """设置或更新值。"""
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        self.conn.commit()

    def delete_setting(self, key: str):
        """删除设置。"""
        cur = self.conn.cursor()
        cur.execute("DELETE FROM settings WHERE key = ?", (key,))
        self.conn.commit()

    def get_setting_updated_at(self, key: str) -> Optional[str]:
        """获取设置的更新时间。"""
        cur = self.conn.cursor()
        cur.execute("SELECT updated_at FROM settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["updated_at"] if row else None
