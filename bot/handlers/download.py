"""下载命令处理 — /dl, /download"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import require_auth
from bot.handlers.search import user_cache

logger = logging.getLogger(__name__)


@require_auth
async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /dl 和 /download 命令 — 下载指定序号的种子到下载客户端。"""
    user_id = update.effective_user.id
    cache = user_cache.get(user_id)

    if not cache or not cache["results"]:
        await update.message.reply_text("没有搜索结果，请先使用 /search 搜索。")
        return

    if not context.args:
        await update.message.reply_text("用法: /dl <序号>  (序号来自搜索结果)")
        return

    # 解析序号
    try:
        index = int(context.args[0])
    except ValueError:
        await update.message.reply_text("请输入有效的数字序号。")
        return

    results = cache["results"]
    if index < 1 or index > len(results):
        await update.message.reply_text(
            f"序号超出范围，请输入 1 ~ {len(results)} 之间的数字。"
        )
        return

    selected = results[index - 1]
    pt_client = context.bot_data["pt_client"]
    dl_client = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    owner_id = context.bot_data["owner_id"]

    await update.message.reply_text(
        f"正在添加下载: {selected.title[:60]} ..."
    )

    # 先尝试 URL 方式
    success = False
    try:
        success = await dl_client.add_torrent_url(selected.torrent_url)
    except Exception:
        logger.warning("URL 方式添加种子失败，将尝试文件方式")

    # URL 方式失败，改用文件方式
    if not success:
        try:
            torrent_bytes = await pt_client.download_torrent(selected.torrent_url)
            success = await dl_client.add_torrent_file(
                torrent_bytes, f"{selected.title[:80]}.torrent"
            )
        except Exception:
            logger.exception("文件方式添加种子也失败")

    if success:
        # 记录下载日志
        try:
            db.log_download(user_id, selected.title, selected.size)
        except Exception:
            logger.exception("记录下载日志失败")

        await update.message.reply_text("下载任务添加成功!")

        # 通知 Owner（如果操作者不是 Owner 本人）
        if user_id != owner_id:
            try:
                user = update.effective_user
                display = user.full_name or user.username or str(user_id)
                notify_text = (
                    f"用户 {display} 添加了下载:\n"
                    f"标题: {selected.title[:80]}\n"
                    f"大小: {selected.size}"
                )
                await context.bot.send_message(
                    chat_id=owner_id, text=notify_text
                )
            except Exception:
                logger.exception("通知 Owner 失败")
    else:
        await update.message.reply_text("下载任务添加失败，请稍后重试。")
