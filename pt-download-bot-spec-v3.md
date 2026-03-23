# PT 影片下载 Telegram Bot — 完整实施方案 v3（多用户版）

## 一、项目概述

构建一个开源的 Telegram 聊天机器人，用户通过对话即可搜索 PT 站上的影片资源，选择版本后自动推送到 NAS 下载工具进行下载。支持多用户共享使用，Owner 部署后可邀请朋友通过审批使用。下载的影片通过 Plex/Jellyfin 等流媒体服务提供给所有授权用户观看。

### 核心使用场景

```
Owner（你）：拥有 PT 站账号 + 群晖 NAS + Plex/Jellyfin
  ↓ 部署 Bot、审批用户
Friends（朋友）：通过 Telegram Bot 搜索 → 下载到 NAS → Plex 观看
```

### 用户交互流程

**朋友申请加入：**
```
朋友: /start
Bot:  👋 欢迎！你目前没有使用权限。
      发送 /apply 申请使用。

朋友: /apply
Bot:  ✅ 申请已发送，请等待管理员审批。

Bot → Owner:  📩 新用户申请
              昵称: 小明
              用户名: @xiaoming
              ID: 987654321
              [✅ 通过]  [❌ 拒绝]

Owner 点击 [✅ 通过]
Bot → Owner:  已通过小明的申请
Bot → 朋友:   🎉 你的申请已通过！发送 /help 查看使用说明。
```

**搜索和下载（私聊或群组均可）：**
```
朋友: /s 星际穿越
Bot:  🔍 搜索中...

Bot:  📋 搜索结果 (38 个，第 1/4 页)

      1. Interstellar 2014 IMAX 1080p BluRay x265 DTS
         📦 14.37 GB

      2. Interstellar 2014 V2 2160p DTS-HDMA5.1 HDR x265
         📦 35.66 GB

      3. Interstellar 2014 2160p UHD REMUX HEVC HDR DTS-HDMA5.1
         📦 70.22 GB
      ...

      💡 /dl 序号 — 下载 | /more — 下一页

朋友: /dl 1
Bot:  ✅ 已添加下载任务:
      Interstellar 2014 IMAX 1080p BluRay x265 10bit DTS-WiKi
      📦 大小: 14.37 GB
      👤 请求者: 小明
```

---

## 二、用户角色与权限

### 2.1 角色定义

| 角色 | 权限 | 说明 |
|------|------|------|
| **Owner** | 全部权限 | 部署者，通过 .env 中 `OWNER_TELEGRAM_ID` 指定 |
| **User** | 搜索、下载、查看状态 | 经 Owner 审批通过的朋友 |
| **Pending** | 仅申请 | 已申请但未审批的用户 |
| **Banned** | 无权限 | 被 Owner 移除的用户 |
| **未注册** | 仅 /start 和 /apply | 未申请的陌生人 |

### 2.2 命令权限矩阵

| 命令 | 未注册 | Pending | User | Owner |
|------|--------|---------|------|-------|
| `/start` | ✅ | ✅ | ✅ | ✅ |
| `/apply` | ✅ | ❌(已申请) | ❌(已通过) | ❌ |
| `/help` | ✅ | ✅ | ✅ | ✅ |
| `/search` `/s` | ❌ | ❌ | ✅ | ✅ |
| `/dl` | ❌ | ❌ | ✅ | ✅ |
| `/more` | ❌ | ❌ | ✅ | ✅ |
| `/status` | ❌ | ❌ | ✅ | ✅ |
| `/test` | ❌ | ❌ | ❌ | ✅ |
| `/users` | ❌ | ❌ | ❌ | ✅ |
| `/ban` | ❌ | ❌ | ❌ | ✅ |
| `/pending` | ❌ | ❌ | ❌ | ✅ |

---

## 三、技术架构

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────────┐
│  Telegram     │────▶│  Bot Service          │────▶│  PT 站            │
│  (私聊/群组)  │◀────│  (Python / Docker)    │◀────│  (RSS搜索+下载)   │
└──────────────┘     │                        │     └──────────────────┘
                     │  ┌──────────────┐      │
                     │  │ SQLite DB    │      │────▶┌──────────────────┐
                     │  │ 用户/日志    │      │     │  Download Station │
                     │  └──────────────┘      │     └──────────────────┘
                     │  群晖 NAS Docker       │              │
                     └──────────────────────┘              ▼
                                                    ┌──────────────────┐
                                                    │  Plex / Jellyfin  │
                                                    │  (朋友观看影片)   │
                                                    └──────────────────┘
```

### 部署环境

- **运行位置**: 群晖 NAS Docker 容器
- **网络**: 路由器翻墙，Docker 容器通过路由器出网
- **存储**: SQLite 数据库通过 Docker volume 持久化
- **使用方式**: 私聊 + 群组均支持

---

## 四、数据持久化 — SQLite

### 4.1 数据库表设计

**users 表 — 用户管理**
```sql
CREATE TABLE users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,          -- @username，可为空
    display_name  TEXT,          -- 显示昵称
    role          TEXT NOT NULL DEFAULT 'pending',  -- owner/user/pending/banned
    applied_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at   TIMESTAMP,
    approved_by   INTEGER,      -- 审批人的 telegram_id
    FOREIGN KEY (approved_by) REFERENCES users(telegram_id)
);
```

**download_logs 表 — 下载记录（可选，用于统计）**
```sql
CREATE TABLE download_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id   INTEGER NOT NULL,
    torrent_title TEXT,
    torrent_size  TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);
```

### 4.2 为什么用 SQLite

- 零依赖，无需额外数据库服务
- 单文件存储，Docker volume 挂载即可持久化
- 对几十个用户、几千条记录的规模绰绰有余
- Python 标准库内置 `sqlite3`，无需额外安装

---

## 五、项目结构

```
pt-download-bot/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example                # 配置模板
├── .gitignore
├── README.md                   # 中文文档
├── LICENSE                     # MIT
├── data/                       # Docker volume 挂载点（不提交）
│   └── bot.db                  # SQLite 数据库（运行时生成）
├── bot/
│   ├── __init__.py
│   ├── main.py                 # 入口
│   ├── config.py               # 配置加载
│   ├── database.py             # SQLite 数据层
│   ├── middleware.py            # 权限检查中间件
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── start.py            # /start, /apply, /help
│   │   ├── search.py           # /search, /s, /more
│   │   ├── download.py         # /dl
│   │   ├── status.py           # /status
│   │   └── admin.py            # /users, /ban, /pending（Owner 专属）
│   ├── pt/
│   │   ├── __init__.py
│   │   ├── base.py             # PT 站抽象基类
│   │   └── nexusphp.py         # NexusPHP 通用实现
│   ├── clients/
│   │   ├── __init__.py         # 工厂函数
│   │   ├── base.py             # 下载客户端基类
│   │   ├── download_station.py
│   │   ├── qbittorrent.py
│   │   └── transmission.py
│   └── utils.py
└── tests/
    ├── test_pt.py
    └── test_clients.py
```

---

## 六、配置文件

### .env.example
```env
# === Telegram 配置 ===
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Owner 的 Telegram User ID（必填，部署者本人）
# 获取方法：Telegram 搜索 @userinfobot 发送消息获取
OWNER_TELEGRAM_ID=123456789

# === PT 站配置 ===
PT_SITE_URL=https://ptchdbits.co
PT_PASSKEY=your_passkey_here

# === 下载客户端配置 ===
# 可选值: download_station, qbittorrent, transmission
DOWNLOAD_CLIENT=download_station

# --- Download Station ---
DS_HOST=http://localhost:5000
DS_USERNAME=your_nas_username
DS_PASSWORD=your_nas_password

# --- qBittorrent (如选用) ---
QB_HOST=http://localhost:8080
QB_USERNAME=admin
QB_PASSWORD=adminadmin

# --- Transmission (如选用) ---
TR_HOST=http://localhost:9091
TR_USERNAME=admin
TR_PASSWORD=admin

# === 数据库 ===
DB_PATH=/app/data/bot.db
```

---

## 七、核心模块实现

### 7.1 bot/database.py — 数据层

```python
import sqlite3
import os
from typing import Optional, List
from dataclasses import dataclass
from datetime import datetime

@dataclass
class User:
    telegram_id: int
    username: Optional[str]
    display_name: str
    role: str           # owner, user, pending, banned
    applied_at: Optional[str]
    approved_at: Optional[str]
    approved_by: Optional[int]

class Database:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
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
        """)
        self.conn.commit()

    def init_owner(self, owner_id: int):
        """初始化 Owner 账号（首次启动时调用）"""
        existing = self.get_user(owner_id)
        if not existing:
            self.conn.execute(
                "INSERT INTO users (telegram_id, display_name, role, approved_at) VALUES (?, ?, 'owner', ?)",
                (owner_id, "Owner", datetime.now().isoformat())
            )
            self.conn.commit()
        elif existing.role != "owner":
            self.conn.execute("UPDATE users SET role = 'owner' WHERE telegram_id = ?", (owner_id,))
            self.conn.commit()

    def get_user(self, telegram_id: int) -> Optional[User]:
        row = self.conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if row:
            return User(**dict(row))
        return None

    def apply_user(self, telegram_id: int, username: str, display_name: str) -> bool:
        """用户申请，返回是否成功（已存在则失败）"""
        existing = self.get_user(telegram_id)
        if existing:
            return False
        self.conn.execute(
            "INSERT INTO users (telegram_id, username, display_name, role) VALUES (?, ?, ?, 'pending')",
            (telegram_id, username, display_name)
        )
        self.conn.commit()
        return True

    def approve_user(self, telegram_id: int, approved_by: int) -> bool:
        """审批通过用户"""
        user = self.get_user(telegram_id)
        if not user or user.role != "pending":
            return False
        self.conn.execute(
            "UPDATE users SET role = 'user', approved_at = ?, approved_by = ? WHERE telegram_id = ?",
            (datetime.now().isoformat(), approved_by, telegram_id)
        )
        self.conn.commit()
        return True

    def reject_user(self, telegram_id: int) -> bool:
        """拒绝用户申请（直接删除）"""
        self.conn.execute("DELETE FROM users WHERE telegram_id = ? AND role = 'pending'", (telegram_id,))
        self.conn.commit()
        return True

    def ban_user(self, telegram_id: int) -> bool:
        """封禁用户"""
        user = self.get_user(telegram_id)
        if not user or user.role == "owner":
            return False
        self.conn.execute("UPDATE users SET role = 'banned' WHERE telegram_id = ?", (telegram_id,))
        self.conn.commit()
        return True

    def unban_user(self, telegram_id: int) -> bool:
        """解封用户"""
        user = self.get_user(telegram_id)
        if not user or user.role != "banned":
            return False
        self.conn.execute("UPDATE users SET role = 'user' WHERE telegram_id = ?", (telegram_id,))
        self.conn.commit()
        return True

    def get_pending_users(self) -> List[User]:
        rows = self.conn.execute("SELECT * FROM users WHERE role = 'pending' ORDER BY applied_at").fetchall()
        return [User(**dict(r)) for r in rows]

    def get_approved_users(self) -> List[User]:
        rows = self.conn.execute("SELECT * FROM users WHERE role IN ('user', 'owner') ORDER BY approved_at").fetchall()
        return [User(**dict(r)) for r in rows]

    def get_all_users(self) -> List[User]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY role, applied_at").fetchall()
        return [User(**dict(r)) for r in rows]

    def log_download(self, telegram_id: int, title: str, size: str):
        """记录下载日志"""
        self.conn.execute(
            "INSERT INTO download_logs (telegram_id, torrent_title, torrent_size) VALUES (?, ?, ?)",
            (telegram_id, title, size)
        )
        self.conn.commit()

    def is_authorized(self, telegram_id: int) -> bool:
        """检查用户是否有使用权限（user 或 owner）"""
        user = self.get_user(telegram_id)
        return user is not None and user.role in ("user", "owner")

    def is_owner(self, telegram_id: int) -> bool:
        user = self.get_user(telegram_id)
        return user is not None and user.role == "owner"
```

### 7.2 bot/middleware.py — 权限检查装饰器

```python
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes

def require_auth(func):
    """要求用户已授权（user 或 owner）"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = context.bot_data["db"]
        user_id = update.effective_user.id

        if not db.is_authorized(user_id):
            user = db.get_user(user_id)
            if user and user.role == "pending":
                await update.effective_message.reply_text("⏳ 你的申请正在等待审批，请耐心等待。")
            elif user and user.role == "banned":
                await update.effective_message.reply_text("⛔ 你的使用权限已被移除。")
            else:
                await update.effective_message.reply_text(
                    "👋 你还没有使用权限。\n发送 /apply 申请使用。"
                )
            return

        return await func(update, context)
    return wrapper


def require_owner(func):
    """要求用户是 Owner"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        db = context.bot_data["db"]
        user_id = update.effective_user.id

        if not db.is_owner(user_id):
            await update.effective_message.reply_text("⛔ 此命令仅限管理员使用。")
            return

        return await func(update, context)
    return wrapper
```

### 7.3 bot/handlers/start.py — 入口与申请

```python
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start"""
    db = context.bot_data["db"]
    user_id = update.effective_user.id
    user = db.get_user(user_id)

    if user and user.role in ("user", "owner"):
        role_label = "管理员" if user.role == "owner" else "用户"
        await update.effective_message.reply_text(
            f"👋 欢迎回来，{user.display_name}！（{role_label}）\n\n"
            f"发送 /help 查看使用说明。"
        )
    elif user and user.role == "pending":
        await update.effective_message.reply_text(
            "⏳ 你的申请正在等待管理员审批，请耐心等待。"
        )
    elif user and user.role == "banned":
        await update.effective_message.reply_text("⛔ 你的使用权限已被移除。")
    else:
        await update.effective_message.reply_text(
            "👋 欢迎使用 PT Download Bot！\n\n"
            "这是一个影片搜索和下载机器人。\n"
            "发送 /apply 申请使用权限。"
        )


async def apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /apply — 用户申请"""
    db = context.bot_data["db"]
    user = update.effective_user
    existing = db.get_user(user.id)

    if existing:
        status_map = {
            "owner": "你是管理员，无需申请 😎",
            "user": "你已经有使用权限了，发送 /help 查看说明。",
            "pending": "⏳ 你已经申请过了，请等待管理员审批。",
            "banned": "⛔ 你的权限已被移除，无法重新申请。"
        }
        await update.effective_message.reply_text(status_map.get(existing.role, "未知状态"))
        return

    # 创建申请
    display_name = user.full_name or user.username or str(user.id)
    db.apply_user(user.id, user.username, display_name)

    await update.effective_message.reply_text(
        "✅ 申请已发送！请等待管理员审批。\n"
        "审批通过后你会收到通知。"
    )

    # 通知 Owner
    owner_id = context.bot_data["owner_id"]
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ 通过", callback_data=f"approve:{user.id}"),
            InlineKeyboardButton("❌ 拒绝", callback_data=f"reject:{user.id}")
        ]
    ])

    username_text = f"@{user.username}" if user.username else "无"
    await context.bot.send_message(
        chat_id=owner_id,
        text=(
            f"📩 <b>新用户申请</b>\n\n"
            f"昵称: {display_name}\n"
            f"用户名: {username_text}\n"
            f"ID: <code>{user.id}</code>"
        ),
        parse_mode="HTML",
        reply_markup=keyboard
    )


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理审批按钮回调"""
    query = update.callback_query
    await query.answer()

    db = context.bot_data["db"]
    owner_id = context.bot_data["owner_id"]

    # 只有 Owner 可以审批
    if query.from_user.id != owner_id:
        await query.answer("⛔ 只有管理员可以审批", show_alert=True)
        return

    action, user_id_str = query.data.split(":")
    user_id = int(user_id_str)
    user = db.get_user(user_id)

    if not user:
        await query.edit_message_text("❌ 用户不存在")
        return

    if user.role != "pending":
        await query.edit_message_text(f"该用户状态已变更为: {user.role}")
        return

    if action == "approve":
        db.approve_user(user_id, owner_id)
        await query.edit_message_text(f"✅ 已通过 {user.display_name} 的申请")

        # 通知用户
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🎉 你的申请已通过！现在可以使用以下命令：\n\n"
                     "/s 影片名 — 搜索影片\n"
                     "/dl 序号 — 下载\n"
                     "/help — 查看全部命令"
            )
        except Exception:
            pass  # 用户可能已屏蔽 Bot

    elif action == "reject":
        db.reject_user(user_id)
        await query.edit_message_text(f"❌ 已拒绝 {user.display_name} 的申请")

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="😔 你的申请未通过。如有疑问请联系管理员。"
            )
        except Exception:
            pass


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help"""
    db = context.bot_data["db"]
    user_id = update.effective_user.id
    is_owner = db.is_owner(user_id)
    is_authorized = db.is_authorized(user_id)

    lines = ["🤖 <b>PT Download Bot</b>\n"]

    if is_authorized:
        lines.append("📖 <b>搜索和下载:</b>")
        lines.append("/s 关键词 — 搜索影片")
        lines.append("/dl 序号 — 下载指定种子")
        lines.append("/more — 显示更多搜索结果")
        lines.append("/status — 查看当前下载任务")

    if is_owner:
        lines.append("\n🔧 <b>管理命令:</b>")
        lines.append("/users — 查看所有用户")
        lines.append("/pending — 查看待审批申请")
        lines.append("/ban 用户ID — 移除用户权限")
        lines.append("/unban 用户ID — 恢复用户权限")
        lines.append("/test — 测试连接")

    if not is_authorized:
        lines.append("\n发送 /apply 申请使用权限")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
```

### 7.4 bot/handlers/admin.py — Owner 管理命令

```python
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import require_owner

@require_owner
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有用户"""
    db = context.bot_data["db"]
    users = db.get_all_users()

    if not users:
        await update.effective_message.reply_text("暂无用户")
        return

    role_emoji = {"owner": "👑", "user": "✅", "pending": "⏳", "banned": "⛔"}
    lines = [f"👥 <b>用户列表</b> ({len(users)} 人)\n"]

    for u in users:
        emoji = role_emoji.get(u.role, "❓")
        username = f"@{u.username}" if u.username else ""
        lines.append(f"{emoji} {u.display_name} {username} <code>{u.telegram_id}</code>")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


@require_owner
async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看待审批申请"""
    db = context.bot_data["db"]
    pending = db.get_pending_users()

    if not pending:
        await update.effective_message.reply_text("📭 没有待审批的申请")
        return

    lines = [f"⏳ <b>待审批申请</b> ({len(pending)} 人)\n"]
    for u in pending:
        username = f"@{u.username}" if u.username else ""
        lines.append(f"• {u.display_name} {username} — <code>{u.telegram_id}</code>")

    lines.append("\n💡 用户申请时会自动弹出审批按钮")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


@require_owner
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """封禁用户: /ban 用户ID"""
    db = context.bot_data["db"]

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("用法: /ban 用户ID\n例如: /ban 987654321")
        return

    target_id = int(context.args[0])
    user = db.get_user(target_id)

    if not user:
        await update.effective_message.reply_text("❌ 用户不存在")
        return

    if user.role == "owner":
        await update.effective_message.reply_text("❌ 不能封禁管理员")
        return

    db.ban_user(target_id)
    await update.effective_message.reply_text(f"⛔ 已移除 {user.display_name} 的权限")

    try:
        await context.bot.send_message(chat_id=target_id, text="⛔ 你的使用权限已被管理员移除。")
    except Exception:
        pass


@require_owner
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """解封用户: /unban 用户ID"""
    db = context.bot_data["db"]

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("用法: /unban 用户ID")
        return

    target_id = int(context.args[0])
    success = db.unban_user(target_id)

    if success:
        user = db.get_user(target_id)
        await update.effective_message.reply_text(f"✅ 已恢复 {user.display_name} 的权限")
        try:
            await context.bot.send_message(chat_id=target_id, text="🎉 你的使用权限已恢复！")
        except Exception:
            pass
    else:
        await update.effective_message.reply_text("❌ 操作失败，用户不存在或不是被封禁状态")
```

### 7.5 bot/handlers/search.py — 搜索（带权限检查）

```python
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import require_auth

logger = logging.getLogger(__name__)

# 每个用户的搜索缓存
user_cache: dict = {}

@require_auth
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /search 或 /s"""
    user_id = update.effective_user.id

    if not context.args:
        await update.effective_message.reply_text(
            "📖 用法: /s 影片名称\n例如: /s 星际穿越"
        )
        return

    keyword = " ".join(context.args)
    msg = await update.effective_message.reply_text(f"🔍 正在搜索: {keyword} ...")

    try:
        pt_client = context.bot_data["pt_client"]
        results = await pt_client.search(keyword)
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        await msg.edit_text(f"❌ 搜索失败: {e}")
        return

    if not results:
        await msg.edit_text("😕 未找到相关种子，请尝试英文名或其他关键词")
        return

    page_size = context.bot_data.get("page_size", 10)
    user_cache[user_id] = {"results": results, "page": 0, "page_size": page_size}

    text = _format_results(results, page=0, page_size=page_size)
    await msg.edit_text(text, parse_mode="HTML")


@require_auth
async def more_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /more"""
    user_id = update.effective_user.id

    if user_id not in user_cache:
        await update.effective_message.reply_text("请先使用 /s 搜索影片")
        return

    cache = user_cache[user_id]
    cache["page"] += 1
    results = cache["results"]
    page = cache["page"]
    page_size = cache["page_size"]

    if page * page_size >= len(results):
        await update.effective_message.reply_text("已经是最后一页了")
        cache["page"] -= 1
        return

    text = _format_results(results, page=page, page_size=page_size)
    await update.effective_message.reply_text(text, parse_mode="HTML")


def _format_results(results: list, page: int, page_size: int) -> str:
    start = page * page_size
    end = min(start + page_size, len(results))
    total_pages = (len(results) + page_size - 1) // page_size

    lines = [f"📋 <b>搜索结果</b> ({len(results)} 个，第 {page+1}/{total_pages} 页)\n"]

    for i, r in enumerate(results[start:end], start=start + 1):
        title_short = r.title[:55] + "..." if len(r.title) > 55 else r.title
        lines.append(f"<b>{i}.</b> {title_short}\n    📦 {r.size}\n")

    lines.append(f"\n💡 /dl 序号 — 下载 | /more — 下一页")
    return "\n".join(lines)
```

### 7.6 bot/handlers/download.py — 下载（带日志记录）

```python
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bot.middleware import require_auth
from bot.handlers.search import user_cache

logger = logging.getLogger(__name__)

@require_auth
async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /dl"""
    user_id = update.effective_user.id
    user = update.effective_user

    if user_id not in user_cache:
        await update.effective_message.reply_text("请先使用 /s 搜索影片")
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("📖 用法: /dl 序号\n例如: /dl 3")
        return

    index = int(context.args[0]) - 1
    results = user_cache[user_id]["results"]

    if index < 0 or index >= len(results):
        await update.effective_message.reply_text(f"❌ 序号无效，请输入 1-{len(results)}")
        return

    selected = results[index]
    msg = await update.effective_message.reply_text("⬇️ 正在添加下载任务...")

    dl_client = context.bot_data["dl_client"]

    # 方式1: URL
    try:
        success = await dl_client.add_torrent_url(selected.torrent_url)
    except Exception as e:
        logger.warning(f"URL 方式添加失败: {e}")
        success = False

    # 方式2: 文件上传
    if not success:
        try:
            pt_client = context.bot_data["pt_client"]
            torrent_bytes = await pt_client.download_torrent(selected.torrent_url)
            safe_name = "".join(c for c in selected.title[:50] if c.isalnum() or c in " .-_")
            success = await dl_client.add_torrent_file(torrent_bytes, f"{safe_name}.torrent")
        except Exception as e:
            logger.error(f"文件方式也失败: {e}")
            success = False

    if success:
        # 记录下载日志
        db = context.bot_data["db"]
        db.log_download(user_id, selected.title, selected.size)

        display_name = user.full_name or user.username or str(user.id)
        await msg.edit_text(
            f"✅ 已添加下载任务:\n\n"
            f"<b>{selected.title}</b>\n"
            f"📦 {selected.size}\n"
            f"👤 请求者: {display_name}",
            parse_mode="HTML"
        )

        # 通知 Owner（如果不是 Owner 本人操作）
        owner_id = context.bot_data["owner_id"]
        if user_id != owner_id:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"📥 <b>新下载任务</b>\n\n"
                         f"👤 {display_name}\n"
                         f"🎬 {selected.title[:60]}\n"
                         f"📦 {selected.size}",
                    parse_mode="HTML"
                )
            except Exception:
                pass
    else:
        await msg.edit_text(
            "❌ 添加下载任务失败\n\n"
            "请联系管理员检查下载客户端设置。"
        )
```

### 7.7 bot/main.py — 入口（完整版）

```python
import logging
import os
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from bot.config import load_config
from bot.database import Database
from bot.pt.nexusphp import NexusPHPSite
from bot.clients import create_download_client
from bot.handlers.start import start_command, apply_command, approval_callback, help_command
from bot.handlers.search import search_command, more_command
from bot.handlers.download import download_command
from bot.handlers.admin import users_command, pending_command, ban_command, unban_command

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def test_command(update, context):
    """测试连接"""
    from bot.middleware import require_owner
    db = context.bot_data["db"]
    if not db.is_owner(update.effective_user.id):
        await update.effective_message.reply_text("⛔ 此命令仅限管理员使用。")
        return

    pt_client = context.bot_data["pt_client"]
    dl_client = context.bot_data["dl_client"]

    msg = await update.effective_message.reply_text("🔄 测试连接中...")

    pt_ok = await pt_client.test_connection()
    dl_ok = await dl_client.test_connection()

    text = (
        f"🔗 <b>连接测试结果:</b>\n\n"
        f"PT 站: {'✅ 正常' if pt_ok else '❌ 失败'}\n"
        f"下载客户端: {'✅ 正常' if dl_ok else '❌ 失败'}"
    )
    await msg.edit_text(text, parse_mode="HTML")


async def status_command(update, context):
    """查看下载状态"""
    from bot.middleware import require_auth
    db = context.bot_data["db"]
    if not db.is_authorized(update.effective_user.id):
        await update.effective_message.reply_text("👋 发送 /apply 申请使用权限")
        return

    dl_client = context.bot_data["dl_client"]
    try:
        tasks = await dl_client.get_tasks()
    except Exception as e:
        await update.effective_message.reply_text(f"❌ 获取任务列表失败: {e}")
        return

    if not tasks:
        await update.effective_message.reply_text("📭 当前没有下载任务")
        return

    lines = [f"📥 <b>当前下载任务</b> ({len(tasks)} 个)\n"]
    for t in tasks[:10]:
        name = t.get("title") or t.get("name", "未知")
        name_short = name[:40] + "..." if len(name) > 40 else name
        lines.append(f"• {name_short}")

    if len(tasks) > 10:
        lines.append(f"\n... 还有 {len(tasks) - 10} 个任务")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


def main():
    telegram_config, pt_config, dl_config = load_config()

    # 初始化数据库
    db_path = os.environ.get("DB_PATH", "/app/data/bot.db")
    db = Database(db_path)
    db.init_owner(telegram_config.owner_id)

    logger.info(f"Owner ID: {telegram_config.owner_id}")
    logger.info(f"PT 站: {pt_config.site_url}")
    logger.info(f"下载客户端: {dl_config.client_type}")

    # 初始化组件
    pt_client = NexusPHPSite(base_url=pt_config.site_url, passkey=pt_config.passkey)
    dl_client = create_download_client(dl_config)

    # 构建 Bot
    app = ApplicationBuilder().token(telegram_config.bot_token).build()

    # 共享数据
    app.bot_data["db"] = db
    app.bot_data["pt_client"] = pt_client
    app.bot_data["dl_client"] = dl_client
    app.bot_data["owner_id"] = telegram_config.owner_id
    app.bot_data["page_size"] = pt_config.page_size

    # 注册命令
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("apply", apply_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("s", search_command))
    app.add_handler(CommandHandler("dl", download_command))
    app.add_handler(CommandHandler("download", download_command))
    app.add_handler(CommandHandler("more", more_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("users", users_command))
    app.add_handler(CommandHandler("pending", pending_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))

    # 审批按钮回调
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^(approve|reject):"))

    logger.info("Bot is running...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
```

### 7.8 bot/config.py — 更新版（增加 owner_id）

```python
import os
from dataclasses import dataclass, field
from typing import List

@dataclass
class TelegramConfig:
    bot_token: str
    owner_id: int       # Owner 的 Telegram User ID

@dataclass
class PTConfig:
    site_url: str
    passkey: str
    max_results: int = 50
    page_size: int = 10

@dataclass
class DownloadClientConfig:
    client_type: str
    ds_host: str = ""
    ds_username: str = ""
    ds_password: str = ""
    qb_host: str = ""
    qb_username: str = ""
    qb_password: str = ""
    tr_host: str = ""
    tr_username: str = ""
    tr_password: str = ""

def load_config():
    telegram = TelegramConfig(
        bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        owner_id=int(os.environ["OWNER_TELEGRAM_ID"]),
    )

    pt = PTConfig(
        site_url=os.environ.get("PT_SITE_URL", "https://ptchdbits.co"),
        passkey=os.environ["PT_PASSKEY"],
        max_results=int(os.environ.get("PT_MAX_RESULTS", "50")),
        page_size=int(os.environ.get("PT_PAGE_SIZE", "10")),
    )

    client_type = os.environ.get("DOWNLOAD_CLIENT", "download_station")
    download = DownloadClientConfig(
        client_type=client_type,
        ds_host=os.environ.get("DS_HOST", "http://localhost:5000"),
        ds_username=os.environ.get("DS_USERNAME", ""),
        ds_password=os.environ.get("DS_PASSWORD", ""),
        qb_host=os.environ.get("QB_HOST", "http://localhost:8080"),
        qb_username=os.environ.get("QB_USERNAME", "admin"),
        qb_password=os.environ.get("QB_PASSWORD", "adminadmin"),
        tr_host=os.environ.get("TR_HOST", "http://localhost:9091"),
        tr_username=os.environ.get("TR_USERNAME", ""),
        tr_password=os.environ.get("TR_PASSWORD", ""),
    )

    return telegram, pt, download
```

---

## 八、PT 站和下载客户端模块

PT 站模块（`bot/pt/`）和下载客户端模块（`bot/clients/`）与 v2 版本相同，此处不再重复。包括：

- `bot/pt/base.py` — PT 站抽象基类
- `bot/pt/nexusphp.py` — NexusPHP 通用实现（CHDBits 等）
- `bot/clients/base.py` — 下载客户端基类
- `bot/clients/download_station.py` — 群晖 Download Station
- `bot/clients/qbittorrent.py` — qBittorrent
- `bot/clients/transmission.py` — Transmission
- `bot/clients/__init__.py` — 工厂函数 `create_download_client()`

请参考 v2 方案中的对应代码。

---

## 九、Docker 部署

### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# 数据目录
RUN mkdir -p /app/data

CMD ["python", "-m", "bot.main"]
```

### docker-compose.yml
```yaml
version: "3.8"

services:
  pt-bot:
    build: .
    container_name: pt-download-bot
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./data:/app/data        # SQLite 数据库持久化
    network_mode: host
    environment:
      - TZ=Asia/Shanghai
```

### requirements.txt
```
python-telegram-bot>=20.0
httpx>=0.25.0
feedparser>=6.0
```

### .gitignore
```
.env
data/
*.torrent
__pycache__/
*.pyc
.DS_Store
```

---

## 十、部署步骤

### 10.1 前置准备

1. **创建 Telegram Bot** — @BotFather → `/newbot` → 获取 Token
2. **获取你的 Telegram ID** — @userinfobot → 发消息 → 获取 ID
3. **获取 PT 站 Passkey** — PT 站控制面板
4. **确认群晖信息** — NAS IP、账号密码

### 10.2 部署

```bash
git clone https://github.com/yourname/pt-download-bot.git
cd pt-download-bot
cp .env.example .env
nano .env                    # 填入配置
docker-compose up -d         # 启动
docker-compose logs -f       # 查看日志
```

### 10.3 验证

1. Telegram 私聊 Bot，发送 `/start`
2. 发送 `/test` 检查连接
3. 发送 `/s 星际穿越` 搜索
4. 发送 `/dl 1` 下载
5. 让朋友搜索 Bot 并发送 `/apply`
6. 你收到通知，点击 ✅ 通过

### 10.4 群组使用

1. 将 Bot 添加到 Telegram 群组
2. 群组中所有已授权用户都可以使用命令
3. 未授权用户在群组中使用命令会被提示申请

---

## 十一、命令清单

### 所有用户
| 命令 | 说明 |
|------|------|
| `/start` | 查看状态 |
| `/apply` | 申请使用权限 |
| `/help` | 帮助信息 |

### 已授权用户 (User + Owner)
| 命令 | 说明 | 示例 |
|------|------|------|
| `/s` 或 `/search` | 搜索影片 | `/s 星际穿越` |
| `/dl` | 下载 | `/dl 3` |
| `/more` | 下一页 | `/more` |
| `/status` | 查看下载任务 | `/status` |

### 管理员 (Owner)
| 命令 | 说明 | 示例 |
|------|------|------|
| `/users` | 查看所有用户 | `/users` |
| `/pending` | 查看待审批 | `/pending` |
| `/ban` | 移除用户 | `/ban 987654321` |
| `/unban` | 恢复用户 | `/unban 987654321` |
| `/test` | 测试连接 | `/test` |

---

## 十二、已验证的技术细节

（与 v2 相同，2026年3月23日实际验证）

- CHDBits RSS 搜索接口 ✅
- Passkey 认证 ✅
- Download Station API ✅
- 搜索 "Interstellar" 返回 38 条结果 ✅

---

## 十三、安全注意事项

1. **Passkey 保密** — `.env` 不提交 Git
2. **Owner 唯一** — 通过 `OWNER_TELEGRAM_ID` 硬性指定
3. **审批机制** — 陌生人无法直接使用
4. **下载通知** — 朋友的每次下载 Owner 都会收到通知
5. **封禁能力** — 可随时移除问题用户
6. **群晖安全** — 建议使用只有 Download Station 权限的专用账号
7. **PT 站风险** — Owner 需自行评估多人使用对分享率的影响

---

## 十四、后续可扩展功能

1. **下载完成通知** — 轮询 Download Station，完成后推送消息
2. **下载统计** — 基于 download_logs 表展示每人的下载统计
3. **多站点搜索** — 支持配置多个 PT 站，聚合结果
4. **Inline 模式** — 任意聊天中 @bot 搜索
5. **影片信息增强** — TMDB/豆瓣海报和评分
6. **自动分类存储** — 按影片类型存入不同目录
7. **Web 管理面板** — 可视化配置和用户管理
