"""入口模块 — 初始化所有组件并启动 Bot"""

import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bot.config import load_config
from bot.database import Database
from bot.pt.nexusphp import NexusPHPSite
from bot.clients import create_download_client
from bot.tmdb import TMDBClient
from bot.middleware import require_auth, require_owner
from bot.handlers.search import search_command, more_command
from bot.handlers.download import download_command
from bot.handlers.start import start_command, apply_command, approval_callback, help_command
from bot.handlers.admin import users_command, pending_command, ban_command, unban_command

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 内置命令: /test, /status
# ------------------------------------------------------------------

@require_owner
async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner 专属 — 测试 PT 站和下载客户端连接。"""
    pt_client = context.bot_data["pt_client"]
    dl_client = context.bot_data["dl_client"]

    await update.message.reply_text("正在测试连接...")

    lines = []

    # 测试 PT 站
    try:
        pt_ok = await pt_client.test_connection()
        lines.append(f"PT 站连接: {'正常' if pt_ok else '失败'}")
    except Exception as e:
        lines.append(f"PT 站连接: 异常 ({e})")

    # 测试下载客户端
    try:
        dl_ok = await dl_client.test_connection()
        lines.append(f"下载客户端连接: {'正常' if dl_ok else '失败'}")
    except Exception as e:
        lines.append(f"下载客户端连接: 异常 ({e})")

    await update.message.reply_text("\n".join(lines))


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看下载客户端当前任务列表。"""
    dl_client = context.bot_data["dl_client"]

    try:
        tasks = await dl_client.get_tasks()
    except Exception:
        logger.exception("获取下载任务列表失败")
        await update.message.reply_text("获取任务列表失败，请稍后重试。")
        return

    if not tasks:
        await update.message.reply_text("当前没有下载任务。")
        return

    lines = [f"当前下载任务 ({len(tasks)} 个):"]
    for i, task in enumerate(tasks[:20], 1):
        name = task.get("title") or task.get("name") or "未知"
        if len(name) > 60:
            name = name[:59] + "\u2026"
        lines.append(f"{i}. {name}")

    if len(tasks) > 20:
        lines.append(f"... 还有 {len(tasks) - 20} 个任务未显示")

    await update.message.reply_text("\n".join(lines))


# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------

def main():
    # 1. 加载配置
    telegram_cfg, pt_cfg, dl_cfg, tmdb_api_key = load_config()

    # 2. 初始化数据库，写入 Owner
    import os
    db_path = os.environ.get("DB_PATH", "data/bot.db")
    db = Database(db_path)
    db.init_owner(telegram_cfg.owner_id)

    # 3. 初始化 PT 站客户端
    pt_client = NexusPHPSite(pt_cfg.site_url, pt_cfg.passkey, cookie=pt_cfg.cookie)

    # 4. 初始化下载客户端
    dl_client = create_download_client(dl_cfg)

    # 5. 初始化 TMDB 客户端（可选）
    tmdb_client = None
    if tmdb_api_key:
        tmdb_client = TMDBClient(tmdb_api_key)
        logger.info("TMDB 翻译已启用")

    # 6. 构建 Telegram Application
    app = ApplicationBuilder().token(telegram_cfg.bot_token).build()

    # 6. 将共享对象存入 bot_data
    app.bot_data["db"] = db
    app.bot_data["pt_client"] = pt_client
    app.bot_data["dl_client"] = dl_client
    app.bot_data["tmdb_client"] = tmdb_client
    app.bot_data["owner_id"] = telegram_cfg.owner_id
    app.bot_data["page_size"] = pt_cfg.page_size

    # 7. 注册所有 handler
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
    app.add_handler(CallbackQueryHandler(approval_callback, pattern=r"^(approve|reject):"))

    # 8. 启动轮询
    logger.info("Bot 启动中...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
