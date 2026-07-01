"""站外资源下载 — 直接处理 magnet/http(s) 链接与 .torrent 文件。"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import require_auth

logger = logging.getLogger(__name__)

MAX_TORRENT_FILE_SIZE = 5 * 1024 * 1024  # 5 MiB，正常 .torrent 文件远小于此值


def _classify_external_url(text: str) -> tuple[str, str] | None:
    """识别用户直接发送的站外下载 URL。

    返回 (url, source_label)，无法识别则返回 None。
    为避免误触发，要求整条消息去掉首尾空白后就是一个 URL，且中间不能有空白。
    """
    url = (text or "").strip()
    if not url or re.search(r"\s", url):
        return None

    lower = url.lower()
    if lower.startswith("magnet:?"):
        return url, "磁力链接"

    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url, "HTTP/HTTPS 链接"

    return None


def _display_external_url(url: str) -> str:
    """生成写入下载日志/通知的站外资源标题。"""
    parsed = urlparse(url)
    if parsed.scheme == "magnet":
        query = parse_qs(parsed.query)
        dn = query.get("dn", [""])[0].strip()
        if dn:
            return f"站外资源: {dn}"
        xt = query.get("xt", [""])[0].strip()
        if xt:
            return f"站外磁力: {xt[-16:]}"
        return "站外磁力链接"

    path_name = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
    if path_name:
        return f"站外资源: {path_name}"
    return f"站外资源: {parsed.netloc}"


def _safe_torrent_filename(filename: str | None) -> str:
    """去掉路径成分，保留用于上传下载客户端的文件名。"""
    name = (filename or "external.torrent").strip() or "external.torrent"
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name or "external.torrent"


async def _notify_owner_if_needed(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    source_label: str,
    title: str,
) -> None:
    """普通用户添加站外下载后通知 Owner。"""
    user_id = update.effective_user.id
    owner_id = context.bot_data["owner_id"]
    if user_id == owner_id:
        return

    try:
        user = update.effective_user
        display = user.full_name or user.username or str(user_id)
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                f"用户 {display} 添加了站外下载:\n"
                f"来源: {source_label}\n"
                f"内容: {title[:120]}"
            ),
        )
    except Exception:
        logger.exception("通知 Owner 失败")


def _log_external_download(context: ContextTypes.DEFAULT_TYPE, user_id: int, title: str,
                           source_label: str, task_id: str) -> None:
    """记录站外下载日志，失败不影响用户流程。"""
    try:
        db = context.bot_data["db"]
        db.log_download(user_id, title, source_label, task_id=task_id)
    except Exception:
        logger.exception("记录站外下载日志失败")


@require_auth
async def external_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户直接发送的 magnet/http(s) 下载链接。"""
    if not update.message or update.message.reply_to_message:
        return

    classified = _classify_external_url(update.message.text or "")
    if not classified:
        return

    url, source_label = classified
    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await update.message.reply_text(
            "下载客户端尚未配置。\n管理员请先使用 /setds、/setqb 或 /settr 完成配置。"
        )
        return

    status_msg = await update.message.reply_text("正在添加站外下载任务...")
    task_id = None
    try:
        task_id = await dl_client.add_torrent_url(url)
    except Exception:
        logger.exception("站外链接添加失败")

    if task_id is None:
        await status_msg.edit_text(
            "下载任务添加失败。请确认链接格式或下载客户端是否支持该资源。"
        )
        return

    user_id = update.effective_user.id
    title = _display_external_url(url)
    _log_external_download(context, user_id, title, source_label, task_id)

    await status_msg.edit_text(f"下载任务添加成功！\n来源：{source_label}")
    await _notify_owner_if_needed(
        update, context, source_label=source_label, title=title,
    )


@require_auth
async def external_torrent_file_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户上传的 .torrent 文件。"""
    if not update.message or not update.message.document:
        return

    document = update.message.document
    filename = _safe_torrent_filename(getattr(document, "file_name", None))
    mime_type = getattr(document, "mime_type", "") or ""
    is_torrent = filename.lower().endswith(".torrent") or mime_type == "application/x-bittorrent"
    if not is_torrent:
        return

    file_size = getattr(document, "file_size", 0) or 0
    if file_size > MAX_TORRENT_FILE_SIZE:
        await update.message.reply_text("种子文件过大，请确认上传的是 .torrent 文件。")
        return

    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await update.message.reply_text(
            "下载客户端尚未配置。\n管理员请先使用 /setds、/setqb 或 /settr 完成配置。"
        )
        return

    status_msg = await update.message.reply_text("正在添加站外 .torrent 文件...")
    task_id = None
    try:
        tg_file = await document.get_file()
        torrent_bytes = bytes(await tg_file.download_as_bytearray())
        task_id = await dl_client.add_torrent_file(torrent_bytes, filename)
    except Exception:
        logger.exception("站外 .torrent 文件添加失败")

    if task_id is None:
        await status_msg.edit_text(
            "下载任务添加失败。请确认种子文件有效或下载客户端是否支持该资源。"
        )
        return

    user_id = update.effective_user.id
    title = f"站外种子文件: {filename}"
    _log_external_download(context, user_id, title, ".torrent 文件", task_id)

    await status_msg.edit_text("下载任务添加成功！\n来源：.torrent 文件")
    await _notify_owner_if_needed(
        update, context, source_label=".torrent 文件", title=title,
    )
