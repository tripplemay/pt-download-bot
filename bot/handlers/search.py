"""搜索命令处理 — /search, /s, /more"""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import require_auth
from bot.pt.nexusphp import CookieExpiredError
from bot.utils import truncate

logger = logging.getLogger(__name__)

# 每个用户的搜索结果与分页状态
# {user_id: {"results": [TorrentResult, ...], "page": int, "page_size": int}}
user_cache: dict = {}


def _contains_chinese(text: str) -> bool:
    """检测文本是否包含中文字符。"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


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
    tmdb_client = context.bot_data.get("tmdb_client")
    db = context.bot_data["db"]
    page_size = context.bot_data.get("page_size", 10)

    msg = await update.message.reply_text(f"正在搜索: {keyword} ...")

    # Step 1: TMDB 翻译中文 → 英文
    translated_keyword = None
    if _contains_chinese(keyword) and tmdb_client:
        try:
            translated_keyword = await tmdb_client.translate(keyword)
        except Exception:
            logger.warning("TMDB 翻译异常", exc_info=True)

        if translated_keyword:
            logger.info("TMDB 翻译: %s → %s", keyword, translated_keyword)
            try:
                await msg.edit_text(f"正在搜索: {keyword} → {translated_keyword} ...")
            except Exception:
                pass

    # Step 2: 尝试网页版搜索（结果完整）
    results = []
    cookie = db.get_setting("pt_cookie")
    cookie_expired = False

    if cookie:
        try:
            # 中文关键词搜简介
            if _contains_chinese(keyword):
                results = await pt_client.search_web(keyword, cookie=cookie, search_area=1)

            # 英文关键词（翻译后或原始）搜标题
            en_keyword = translated_keyword or keyword
            if not _contains_chinese(en_keyword):
                en_results = await pt_client.search_web(en_keyword, cookie=cookie, search_area=0)
                # 合并去重（按下载链接去重）
                existing_urls = {r.torrent_url for r in results}
                for r in en_results:
                    if r.torrent_url not in existing_urls:
                        results.append(r)

            if results:
                logger.info("网页版搜索返回 %d 条结果", len(results))
        except CookieExpiredError:
            logger.warning("PT Cookie 已失效")
            cookie_expired = True
            db.delete_setting("pt_cookie")
            results = []
            # 异步通知 Owner
            try:
                owner_id = context.bot_data["owner_id"]
                await context.bot.send_message(
                    chat_id=owner_id,
                    text="PT 站 Cookie 已失效，已自动降级到 RSS 搜索（结果有限）。\n"
                         "请使用 /setcookie 更新 Cookie。",
                )
            except Exception:
                pass
        except Exception:
            logger.warning("网页版搜索异常，降级到 RSS", exc_info=True)

    # Step 3: 降级到 RSS（没有 Cookie 或 Cookie 失效或网页搜索无结果）
    if not results:
        rss_keyword = translated_keyword or keyword
        logger.info("RSS 搜索关键词: %s", rss_keyword)
        try:
            results = await pt_client.search(rss_keyword)
            logger.info("RSS 搜索返回 %d 条结果", len(results))
        except Exception:
            logger.exception("RSS 搜索失败")

    # 展示结果
    user_id = update.effective_user.id
    if not results:
        await msg.edit_text("未找到相关种子，请尝试其他关键词。")
        user_cache[user_id] = {"results": [], "page": 0, "page_size": page_size}
        return

    user_cache[user_id] = {
        "results": results,
        "page": 0,
        "page_size": page_size,
    }

    text = _format_results(results, 0, page_size)
    await msg.edit_text(text)


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
