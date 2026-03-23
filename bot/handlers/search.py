"""搜索命令处理 — /search, /s, /more"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import require_auth
from bot.utils import truncate

logger = logging.getLogger(__name__)

# 每个用户的搜索结果与分页状态
# {user_id: {"results": [TorrentResult, ...], "page": int, "page_size": int}}
user_cache: dict = {}


def _format_results(results: list, page: int, page_size: int) -> str:
    """格式化当前页搜索结果。

    返回形如：
        1. 标题截断55字符  [14.37 GB]
        2. ...
        ---
        第 1/3 页 | 共 25 条 | /more 翻页
    """
    total = len(results)
    if total == 0:
        return "未找到相关种子。"

    total_pages = (total + page_size - 1) // page_size
    start = page * page_size
    end = min(start + page_size, total)
    page_results = results[start:end]

    lines = []
    for i, item in enumerate(page_results, start=start + 1):
        title = truncate(item.title, 55)
        lines.append(f"{i}. {title}  [{item.size}]")

    lines.append("---")
    lines.append(f"第 {page + 1}/{total_pages} 页 | 共 {total} 条")
    if page + 1 < total_pages:
        lines.append("发送 /more 查看下一页")

    return "\n".join(lines)


@require_auth
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /search 和 /s 命令 — 搜索 PT 站种子。"""
    if not context.args:
        await update.message.reply_text("用法: /search <关键词>")
        return

    keyword = " ".join(context.args)
    pt_client = context.bot_data["pt_client"]
    page_size = context.bot_data.get("page_size", 10)

    await update.message.reply_text(f"正在搜索: {keyword} ...")

    try:
        results = await pt_client.search(keyword)
    except Exception:
        logger.exception("搜索失败")
        await update.message.reply_text("搜索出错，请稍后重试。")
        return

    user_id = update.effective_user.id
    user_cache[user_id] = {
        "results": results,
        "page": 0,
        "page_size": page_size,
    }

    text = _format_results(results, 0, page_size)
    await update.message.reply_text(text)


@require_auth
async def more_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /more 命令 — 查看搜索结果下一页。"""
    user_id = update.effective_user.id
    cache = user_cache.get(user_id)

    if not cache or not cache["results"]:
        await update.message.reply_text("没有搜索结果，请先使用 /search 搜索。")
        return

    results = cache["results"]
    page_size = cache["page_size"]
    total_pages = (len(results) + page_size - 1) // page_size
    next_page = cache["page"] + 1

    if next_page >= total_pages:
        await update.message.reply_text("已经是最后一页了。")
        return

    cache["page"] = next_page
    text = _format_results(results, next_page, page_size)
    await update.message.reply_text(text)
