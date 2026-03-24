"""下载状态查看 — /status 命令"""

from __future__ import annotations

import logging
from typing import List

from telegram import Update
from telegram.ext import ContextTypes

from bot.middleware import require_auth

logger = logging.getLogger(__name__)

# DS v2 状态码
_STATUS_DOWNLOADING = 2
_STATUS_PAUSED = 5
_STATUS_SEEDING = 8


def _format_size(n: int) -> str:
    """字节数转可读格式"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _build_progress_bar(percent: float, width: int = 16) -> str:
    """生成进度条 [████████░░░░░░░░]"""
    filled = int(width * percent / 100)
    filled = max(0, min(filled, width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _format_eta(remaining_bytes: int, speed: int) -> str:
    """预估剩余时间。speed 为 0 时返回空字符串。"""
    if speed <= 0 or remaining_bytes <= 0:
        return ""
    seconds = remaining_bytes / speed
    if seconds < 60:
        return f"预计 {int(seconds)}秒"
    minutes = seconds / 60
    if minutes < 60:
        return f"预计 {int(minutes)}分钟"
    hours = int(minutes / 60)
    mins = int(minutes % 60)
    if hours < 24:
        return f"预计 {hours}小时{mins}分钟" if mins else f"预计 {hours}小时"
    days = int(hours / 24)
    return f"预计 {days}天{hours % 24}小时"


def _get_task_progress(task: dict) -> tuple:
    """提取任务进度信息，返回 (percent, downloaded, total, speed)。"""
    total = task.get("size", 0)
    transfer = task.get("additional", {}).get("transfer", {})
    downloaded = transfer.get("size_downloaded", 0)
    speed = transfer.get("speed_download", 0)

    if not total:
        return (0.0, 0, 0, 0)

    pct = min(downloaded / total * 100, 100.0) if total > 0 else 0.0
    return (pct, downloaded, total, speed)


def _format_task_detail(task: dict, index: int) -> str:
    """格式化单个任务的显示（名称 + 进度条 + 详情）。"""
    name = task.get("title") or task.get("name") or "未知"
    if len(name) > 50:
        name = name[:49] + "\u2026"

    pct, downloaded, total, speed = _get_task_progress(task)

    lines = [f"{index}. {name}"]

    if total > 0:
        bar = _build_progress_bar(pct)
        lines.append(f"   {bar} {pct:.1f}%")
        detail_parts = [f"{_format_size(downloaded)}/{_format_size(total)}"]
        if speed > 0:
            detail_parts.append(f"⬇️ {_format_size(speed)}/s")
            remaining = total - downloaded
            eta = _format_eta(remaining, speed)
            if eta:
                detail_parts.append(eta)
        lines.append(f"   {' | '.join(detail_parts)}")

    return "\n".join(lines)


def _group_tasks(tasks: List[dict]) -> tuple:
    """按状态分组，返回 (downloading, paused, seeding)。"""
    downloading = []
    paused = []
    seeding = []
    for t in tasks:
        status = t.get("status")
        if status == _STATUS_SEEDING:
            seeding.append(t)
        elif status == _STATUS_PAUSED:
            paused.append(t)
        else:
            downloading.append(t)
    return downloading, paused, seeding


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看下载任务状态。普通用户只看自己的，Owner 看全部（/status mine 看自己的）。"""
    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await update.message.reply_text(
            "下载客户端尚未配置。\n管理员请先使用 /setds、/setqb 或 /settr 完成配置。"
        )
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    is_owner = db.is_owner(user_id)
    # Owner 默认看全部，/status mine 看自己的；普通用户始终只看自己的
    show_mine = not is_owner or (context.args and context.args[0].lower() == "mine")

    try:
        tasks = await dl_client.get_tasks()
    except Exception:
        logger.exception("获取下载任务列表失败")
        await update.message.reply_text("获取任务列表失败，请稍后重试。")
        return

    if not tasks:
        await update.message.reply_text("当前没有下载任务。")
        return

    # 用户过滤：通过 task_id 匹配
    if show_mine:
        user_task_ids = set(db.get_user_task_ids(user_id))
        if not user_task_ids:
            await update.message.reply_text("你还没有通过 Bot 添加过下载任务。")
            return
        tasks = [t for t in tasks if t.get("id") in user_task_ids]
        if not tasks:
            await update.message.reply_text("你的下载任务已全部完成或已被删除。")
            return

    # 按状态分组
    downloading, paused, seeding = _group_tasks(tasks)

    lines = []

    # 下载中
    if downloading:
        lines.append(f"📥 下载中 ({len(downloading)} 个):\n")
        for i, task in enumerate(downloading[:20], 1):
            lines.append(_format_task_detail(task, i))
        if len(downloading) > 20:
            lines.append(f"... 还有 {len(downloading) - 20} 个未显示")

    # 暂停
    if paused:
        if lines:
            lines.append("")
        lines.append(f"⏸ 暂停 ({len(paused)} 个):\n")
        offset = len(downloading)
        for i, task in enumerate(paused[:10], offset + 1):
            lines.append(_format_task_detail(task, i))

    # 做种
    if seeding:
        if lines:
            lines.append("")
        lines.append(f"🌱 做种 {len(seeding)} 个")

    if not lines:
        await update.message.reply_text("当前没有下载任务。")
        return

    await update.message.reply_text("\n".join(lines))
