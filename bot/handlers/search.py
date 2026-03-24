"""搜索命令处理 — /search, /s, /more"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.middleware import require_auth
from bot.pt.nexusphp import CookieExpiredError
from bot.utils import truncate

logger = logging.getLogger(__name__)

# 每个用户的搜索结果与分页状态
# {user_id: {"results": [TorrentResult, ...], "page": int, "page_size": int}}
user_cache: dict = {}

# 搜索结果短期缓存，避免重复请求 PT 站
# {keyword_normalized: {"results": [...], "expires": timestamp}}
_search_result_cache: dict = {}
_CACHE_TTL = 300  # 5 分钟


def _contains_chinese(text: str) -> bool:
    """检测文本是否包含中文字符。"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


# 渐进式搜索：结果不足此阈值时继续扩大搜索范围
_MIN_RESULTS = 3


def _merge_results(existing: list, new: list) -> list:
    """将 new 中不重复的结果追加到 existing，按 torrent_url 去重。"""
    seen = {r.torrent_url for r in existing}
    merged = list(existing)
    for r in new:
        if r.torrent_url not in seen:
            merged.append(r)
            seen.add(r.torrent_url)
    return merged


async def _search_web_progressive(pt_client, cookie: str, keyword: str, translated: str | None) -> list:
    """按精准度逐级扩大搜索范围，避免一上来就搜简介导致大量无关结果。

    优先级（从精准到宽泛）：
    1. 英文翻译 + 标题搜索  （最精准）
    2. 中文原词 + 标题搜索
    3. 中文原词 + 简介搜索  （最宽泛，最后手段）

    非中文关键词只执行标题搜索。
    每级仅在前一级结果不足 _MIN_RESULTS 条时才触发。
    """
    results: list = []

    # 1) 英文翻译 + 标题搜索（最精准）
    if translated:
        results = await pt_client.search_web(translated, cookie=cookie, search_area=0)
        logger.info("网页搜索 [英文标题] '%s' → %d 条", translated, len(results))

    # 非中文关键词到此结束
    if not _contains_chinese(keyword):
        if not results:
            results = await pt_client.search_web(keyword, cookie=cookie, search_area=0)
            logger.info("网页搜索 [标题] '%s' → %d 条", keyword, len(results))
        return results

    # 2) 中文原词 + 标题搜索
    if len(results) < _MIN_RESULTS:
        await asyncio.sleep(0.5)  # 避免短时间内过多请求
        cn_title = await pt_client.search_web(keyword, cookie=cookie, search_area=0)
        logger.info("网页搜索 [中文标题] '%s' → %d 条", keyword, len(cn_title))
        results = _merge_results(results, cn_title)

    # 3) 中文原词 + 简介搜索（最后手段）
    if len(results) < _MIN_RESULTS:
        await asyncio.sleep(0.5)
        cn_desc = await pt_client.search_web(keyword, cookie=cookie, search_area=1)
        logger.info("网页搜索 [中文简介] '%s' → %d 条", keyword, len(cn_desc))
        results = _merge_results(results, cn_desc)

    return results


def _seeders_icon(seeders: int) -> str:
    """做种数图标：有种子绿色，无种子红色。"""
    if seeders > 0:
        return f"🟢 {seeders}种"
    return "🔴 0种"


def _format_results(results: list, page: int, page_size: int) -> str:
    """格式化当前页搜索结果。

    有副标题时双行显示：
        1. Breaking.Bad.S01.1080p.BluRay
           绝命毒师 第一季 | 14.37 GB | 🟢 12种
    无副标题时单行：
        1. Breaking.Bad.S01.1080p.BluRay  [14.37 GB]
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

        if item.subtitle or item.seeders > 0 or (hasattr(item, 'leechers') and item.leechers > 0):
            # 双行格式（网页版搜索结果）
            lines.append(f"{i}. {title}")
            detail_parts = []
            if item.subtitle:
                detail_parts.append(truncate(item.subtitle, 30))
            detail_parts.append(item.size)
            if item.seeders > 0 or item.subtitle:
                detail_parts.append(_seeders_icon(item.seeders))
            lines.append(f"   {'  |  '.join(detail_parts)}")
        else:
            # 单行格式（RSS 搜索结果）
            lines.append(f"{i}. {title}  [{item.size}]")

    lines.append("---")
    lines.append(f"第 {page + 1}/{total_pages} 页 | 共 {total} 条")

    return "\n".join(lines)


def _build_keyboard(user_id: int, page: int, page_size: int, total: int) -> InlineKeyboardMarkup:
    """构建搜索结果的 Inline Keyboard（下载按钮 + 翻页按钮）。"""
    total_pages = (total + page_size - 1) // page_size
    start = page * page_size + 1
    end = min(start + page_size - 1, total)

    # 下载按钮：每行 5 个
    rows = []
    row = []
    for i in range(start, end + 1):
        row.append(InlineKeyboardButton(str(i), callback_data=f"dl:{user_id}:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # 翻页按钮
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ 上一页", callback_data=f"page:{user_id}:{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton("下一页 ▶", callback_data=f"page:{user_id}:{page + 1}"))
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(rows)


@require_auth
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /search 和 /s 命令 — 搜索 PT 站种子。"""
    if not context.args:
        await update.message.reply_text("用法: /search <关键词>")
        return

    keyword = " ".join(context.args)
    cache_key = keyword.lower().strip()

    # 检查搜索结果缓存（5 分钟内相同关键词不重复请求 PT 站）
    cached = _search_result_cache.get(cache_key)
    if cached and cached["expires"] > time.time():
        results = cached["results"]
        user_id = update.effective_user.id
        page_size = context.bot_data.get("page_size", 10)
        user_cache[user_id] = {"results": results, "page": 0, "page_size": page_size}
        if results:
            text = _format_results(results, 0, page_size)
            await update.message.reply_text(text)
        else:
            await update.message.reply_text("未找到相关种子，请尝试其他关键词。")
        return

    pt_client = context.bot_data.get("pt_client")
    if not pt_client:
        await update.message.reply_text(
            "PT 站尚未配置。\n管理员请先使用 /setsite 和 /setpasskey 完成配置。"
        )
        return
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

    # Step 2: 网页版搜索（有 Cookie 时），按精准度逐级扩大范围
    results = []
    cookie = db.get_setting("pt_cookie")

    if cookie:
        try:
            results = await _search_web_progressive(
                pt_client, cookie, keyword, translated_keyword,
            )
            if results:
                logger.info("网页版搜索返回 %d 条结果", len(results))
        except CookieExpiredError:
            logger.warning("PT Cookie 已失效")
            db.delete_setting("pt_cookie")
            results = []
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

    # Step 3: 降级到 RSS（没有 Cookie 或网页搜索无结果）
    if not results:
        rss_keyword = translated_keyword or keyword
        logger.info("RSS 搜索关键词: %s", rss_keyword)
        try:
            results = await pt_client.search(rss_keyword)
            logger.info("RSS 搜索返回 %d 条结果", len(results))
        except Exception:
            logger.exception("RSS 搜索失败")

    # 缓存搜索结果（无论是否为空）
    _search_result_cache[cache_key] = {
        "results": results,
        "expires": time.time() + _CACHE_TTL,
    }

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
    keyboard = _build_keyboard(user_id, 0, page_size, len(results))
    await msg.edit_text(text, reply_markup=keyboard)


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
    keyboard = _build_keyboard(user_id, next_page, page_size, len(results))
    await update.message.reply_text(text, reply_markup=keyboard)


async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理翻页按钮回调：page:用户ID:页码"""
    query = update.callback_query
    await query.answer()

    _, uid_str, page_str = query.data.split(":")
    user_id = int(uid_str)
    page = int(page_str)

    # 仅允许本人操作
    if query.from_user.id != user_id:
        await query.answer("这不是你的搜索结果。", show_alert=True)
        return

    cache = user_cache.get(user_id)
    if not cache or not cache["results"]:
        await query.answer("搜索结果已过期，请重新搜索。", show_alert=True)
        return

    results = cache["results"]
    page_size = cache["page_size"]
    cache["page"] = page

    text = _format_results(results, page, page_size)
    keyboard = _build_keyboard(user_id, page, page_size, len(results))
    await query.edit_message_text(text, reply_markup=keyboard)


async def dl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理下载按钮回调：dl:用户ID:序号"""
    query = update.callback_query
    await query.answer()

    _, uid_str, index_str = query.data.split(":")
    user_id = int(uid_str)
    index = int(index_str)

    if query.from_user.id != user_id:
        await query.answer("这不是你的搜索结果。", show_alert=True)
        return

    cache = user_cache.get(user_id)
    if not cache or not cache["results"]:
        await query.answer("搜索结果已过期，请重新搜索。", show_alert=True)
        return

    results = cache["results"]
    if index < 1 or index > len(results):
        await query.answer("序号无效。", show_alert=True)
        return

    selected = results[index - 1]
    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="下载客户端尚未配置。",
        )
        return

    pt_client = context.bot_data.get("pt_client")
    db = context.bot_data["db"]
    owner_id = context.bot_data["owner_id"]

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"正在添加下载: {selected.title[:60]} ...",
    )

    # 先 URL 方式，失败再文件方式
    task_id = None
    try:
        task_id = await dl_client.add_torrent_url(selected.torrent_url)
    except Exception:
        logger.warning("URL 方式添加失败，尝试文件方式")

    if task_id is None and pt_client:
        try:
            torrent_bytes = await pt_client.download_torrent(selected.torrent_url)
            task_id = await dl_client.add_torrent_file(
                torrent_bytes, f"{selected.title[:80]}.torrent"
            )
        except Exception:
            logger.exception("文件方式也失败")

    if task_id is not None:
        try:
            db.log_download(user_id, selected.title, selected.size, task_id=task_id)
        except Exception:
            logger.exception("记录下载日志失败")

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="下载任务添加成功!",
        )

        if user_id != owner_id:
            try:
                display = query.from_user.full_name or query.from_user.username or str(user_id)
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=f"用户 {display} 添加了下载:\n标题: {selected.title[:80]}\n大小: {selected.size}",
                )
            except Exception:
                logger.exception("通知 Owner 失败")
    else:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="下载任务添加失败，请稍后重试。",
        )
