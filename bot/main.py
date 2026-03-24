"""入口模块 — 初始化所有组件并启动 Bot"""

from __future__ import annotations

import logging
import os
from typing import Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

from bot.config import load_config
from bot.database import Database
from bot.pt.nexusphp import NexusPHPSite
from bot.clients import create_download_client
from bot.clients.base import DownloadClientBase
from bot.tmdb import TMDBClient
from bot.middleware import require_auth, require_owner
from bot.handlers.search import search_command, more_command
from bot.handlers.download import download_command
from bot.handlers.start import start_command, apply_command, approval_callback, help_command
from bot.handlers.admin import (
    users_command, pending_command, ban_command, unban_command,
    setcookie_command, cookiestatus_command,
)
from bot.handlers.settings import (
    setsite_command, setpasskey_command, settmdb_command,
    setds_command, setqb_command, settr_command,
    settings_command,
)

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# 视为占位符的值，不迁移到数据库
_PLACEHOLDERS = {"", "placeholder", "your_passkey_here", "your_token_here"}


# ------------------------------------------------------------------
# 环境变量 → 数据库迁移（向后兼容）
# ------------------------------------------------------------------

def _migrate_env_to_db(db: Database) -> None:
    """如果 .env 中有配置但数据库中没有，同步过去（只在首次生效）。"""
    simple_mappings = {
        "PT_SITE_URL": "pt_site_url",
        "PT_PASSKEY": "pt_passkey",
        "PT_COOKIE": "pt_cookie",
        "TMDB_API_KEY": "tmdb_api_key",
    }
    for env_key, db_key in simple_mappings.items():
        val = os.environ.get(env_key, "")
        if val and val not in _PLACEHOLDERS and not db.get_setting(db_key):
            db.set_setting(db_key, val)
            logger.info("已从 %s 同步到数据库 (%s)", env_key, db_key)

    # 下载客户端迁移
    dl_type = os.environ.get("DOWNLOAD_CLIENT", "")
    if dl_type and dl_type not in _PLACEHOLDERS and not db.get_setting("dl_client_type"):
        db.set_setting("dl_client_type", dl_type)
        prefix_map = {
            "download_station": "DS",
            "qbittorrent": "QB",
            "transmission": "TR",
        }
        prefix = prefix_map.get(dl_type.lower(), "")
        if prefix:
            for field, db_key in [("HOST", "dl_client_host"),
                                  ("USERNAME", "dl_client_username"),
                                  ("PASSWORD", "dl_client_password")]:
                val = os.environ.get(f"{prefix}_{field}", "")
                if val and val not in _PLACEHOLDERS:
                    db.set_setting(db_key, val)
        logger.info("已从环境变量同步下载客户端配置到数据库")


# ------------------------------------------------------------------
# 从数据库初始化组件（可能返回 None）
# ------------------------------------------------------------------

def init_pt_client(db: Database) -> Optional[NexusPHPSite]:
    """从数据库加载 PT 站配置，返回 NexusPHPSite 或 None。"""
    site_url = db.get_setting("pt_site_url")
    passkey = db.get_setting("pt_passkey")
    if site_url and passkey:
        return NexusPHPSite(site_url, passkey)
    return None


def init_dl_client(db: Database) -> Optional[DownloadClientBase]:
    """从数据库加载下载客户端配置，返回客户端实例或 None。"""
    from dataclasses import dataclass

    dl_type = db.get_setting("dl_client_type")
    host = db.get_setting("dl_client_host")
    username = db.get_setting("dl_client_username")
    password = db.get_setting("dl_client_password")
    if not all([dl_type, host]):
        return None

    @dataclass
    class _DlCfg:
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

    cfg = _DlCfg(client_type=dl_type)
    norm = dl_type.lower()
    if norm == "download_station":
        cfg.ds_host = host
        cfg.ds_username = username or ""
        cfg.ds_password = password or ""
    elif norm == "qbittorrent":
        cfg.qb_host = host
        cfg.qb_username = username or ""
        cfg.qb_password = password or ""
    elif norm == "transmission":
        cfg.tr_host = host
        cfg.tr_username = username or ""
        cfg.tr_password = password or ""
    else:
        logger.warning("不支持的下载客户端类型: %s", dl_type)
        return None

    try:
        return create_download_client(cfg)
    except ValueError:
        logger.exception("创建下载客户端失败")
        return None


def init_tmdb_client(db: Database) -> Optional[TMDBClient]:
    """从数据库加载 TMDB 配置。"""
    api_key = db.get_setting("tmdb_api_key")
    if api_key:
        return TMDBClient(api_key)
    return None


# ------------------------------------------------------------------
# 内置命令: /test, /status
# ------------------------------------------------------------------

@require_owner
async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner 专属 — 测试 PT 站和下载客户端连接。"""
    pt_client = context.bot_data.get("pt_client")
    dl_client = context.bot_data.get("dl_client")

    if not pt_client and not dl_client:
        await update.message.reply_text(
            "PT 站和下载客户端均未配置。\n请使用 /settings 查看配置状态。"
        )
        return

    await update.message.reply_text("正在测试连接...")

    lines = []

    if pt_client:
        try:
            pt_ok = await pt_client.test_connection()
            lines.append(f"PT 站连接: {'正常' if pt_ok else '失败'}")
        except Exception as e:
            lines.append(f"PT 站连接: 异常 ({e})")
    else:
        lines.append("PT 站: 未配置")

    if dl_client:
        try:
            dl_ok = await dl_client.test_connection()
            lines.append(f"下载客户端连接: {'正常' if dl_ok else '失败'}")
        except Exception as e:
            lines.append(f"下载客户端连接: 异常 ({e})")
    else:
        lines.append("下载客户端: 未配置")

    await update.message.reply_text("\n".join(lines))


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看下载客户端当前任务列表。"""
    dl_client = context.bot_data.get("dl_client")

    if not dl_client:
        await update.message.reply_text(
            "下载客户端尚未配置。\n管理员请先使用 /setds、/setqb 或 /settr 完成配置。"
        )
        return

    try:
        tasks = await dl_client.get_tasks()
    except Exception:
        logger.exception("获取下载任务列表失败")
        await update.message.reply_text("获取任务列表失败，请稍后重试。")
        return

    if not tasks:
        await update.message.reply_text("当前没有下载任务。")
        return

    # 过滤：只显示下载中的任务（排除已完成/做种 status=8）
    # DS v2: 2=下载中, 5=暂停, 8=做种; qBittorrent/Transmission 无此字段则全部显示
    active = [t for t in tasks if t.get("status") not in (8,)]
    if not active:
        await update.message.reply_text(f"所有任务已完成（共 {len(tasks)} 个做种中）。")
        return

    lines = [f"下载中 ({len(active)} 个):"]
    for i, task in enumerate(active[:20], 1):
        name = task.get("title") or task.get("name") or "未知"
        if len(name) > 60:
            name = name[:59] + "\u2026"
        lines.append(f"{i}. {name}")

    if len(active) > 20:
        lines.append(f"... 还有 {len(active) - 20} 个任务未显示")
    if len(tasks) > len(active):
        lines.append(f"（另有 {len(tasks) - len(active)} 个已完成/做种）")

    await update.message.reply_text("\n".join(lines))


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def main():
    # 1. 只加载启动必需的环境变量
    bot_token, owner_id = load_config()

    # 2. 初始化数据库
    db_path = os.environ.get("DB_PATH", "data/bot.db")
    db = Database(db_path)
    db.init_owner(owner_id)

    # 3. 向后兼容：从 .env 迁移到数据库
    _migrate_env_to_db(db)

    # 4. 从数据库初始化组件（均可能为 None）
    pt_client = init_pt_client(db)
    dl_client = init_dl_client(db)
    tmdb_client = init_tmdb_client(db)

    if pt_client:
        logger.info("PT 站客户端已初始化")
    else:
        logger.info("PT 站未配置，搜索功能不可用")

    if dl_client:
        logger.info("下载客户端已初始化")
    else:
        logger.info("下载客户端未配置，下载功能不可用")

    if tmdb_client:
        logger.info("TMDB 翻译已启用")

    page_size = int(os.environ.get("PT_PAGE_SIZE", "10"))

    # 5. 构建 Telegram Application（增大超时，适配代理/高延迟网络）
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
    get_updates_request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0)
    app = (
        ApplicationBuilder()
        .token(bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    app.bot_data["db"] = db
    app.bot_data["pt_client"] = pt_client
    app.bot_data["dl_client"] = dl_client
    app.bot_data["tmdb_client"] = tmdb_client
    app.bot_data["owner_id"] = owner_id
    app.bot_data["page_size"] = page_size

    # 6. 注册所有 handler
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
    app.add_handler(CommandHandler("setcookie", setcookie_command))
    app.add_handler(CommandHandler("cookiestatus", cookiestatus_command))
    # 新增设置命令
    app.add_handler(CommandHandler("setsite", setsite_command))
    app.add_handler(CommandHandler("setpasskey", setpasskey_command))
    app.add_handler(CommandHandler("settmdb", settmdb_command))
    app.add_handler(CommandHandler("setds", setds_command))
    app.add_handler(CommandHandler("setqb", setqb_command))
    app.add_handler(CommandHandler("settr", settr_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^(approve|reject):"))

    # 7. 启动轮询
    logger.info("Bot 启动中...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
