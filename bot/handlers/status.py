"""下载状态查看 — /status, /cancel 命令 + 删除确认回调"""

from __future__ import annotations

import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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


def _build_delete_buttons(visible_tasks: List[dict], user_id: int) -> InlineKeyboardMarkup:
    """为可见任务构建删除按钮，每行一个 ❌ 按钮。"""
    rows = []
    for i, task in enumerate(visible_tasks, 1):
        task_id = task.get("id", "")
        if task_id:
            name = task.get("title") or task.get("name") or "未知"
            if len(name) > 25:
                name = name[:24] + "\u2026"
            rows.append([
                InlineKeyboardButton(
                    f"❌ {i}. {name}",
                    callback_data=f"cdel:{user_id}:{task_id}",
                )
            ])
    return InlineKeyboardMarkup(rows) if rows else None


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

    # 用户过滤
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
    # 收集所有可见任务（用于删除按钮）
    visible_tasks = []

    if downloading:
        lines.append(f"📥 下载中 ({len(downloading)} 个):\n")
        for i, task in enumerate(downloading[:20], 1):
            lines.append(_format_task_detail(task, i))
            visible_tasks.append(task)
        if len(downloading) > 20:
            lines.append(f"... 还有 {len(downloading) - 20} 个未显示")

    if paused:
        if lines:
            lines.append("")
        lines.append(f"⏸ 暂停 ({len(paused)} 个):\n")
        offset = len(visible_tasks)
        for i, task in enumerate(paused[:10], offset + 1):
            lines.append(_format_task_detail(task, i))
            visible_tasks.append(task)

    if seeding:
        if lines:
            lines.append("")
        lines.append(f"🌱 做种 {len(seeding)} 个")
        offset = len(visible_tasks)
        for i, task in enumerate(seeding[:10], offset + 1):
            visible_tasks.append(task)

    if not lines:
        await update.message.reply_text("当前没有下载任务。")
        return

    # 构建删除按钮
    keyboard = _build_delete_buttons(visible_tasks, user_id)
    await update.message.reply_text("\n".join(lines), reply_markup=keyboard)


@require_auth
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /cancel <序号> — 删除指定序号的下载任务（需二次确认）。"""
    if not context.args:
        await update.message.reply_text("用法: /cancel <序号>（序号来自 /status）")
        return

    try:
        index = int(context.args[0])
        if index < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text("请输入有效的数字序号。")
        return

    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await update.message.reply_text("下载客户端尚未配置。")
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    is_owner = db.is_owner(user_id)

    try:
        tasks = await dl_client.get_tasks()
    except Exception:
        await update.message.reply_text("获取任务列表失败。")
        return

    # 用户过滤（和 /status 逻辑一致）
    show_mine = not is_owner or (context.args and len(context.args) > 1 and context.args[1].lower() == "mine")
    if not is_owner:
        user_task_ids = set(db.get_user_task_ids(user_id))
        tasks = [t for t in tasks if t.get("id") in user_task_ids]

    # 构建可见列表（和 /status 排序一致）
    downloading, paused, seeding = _group_tasks(tasks)
    visible = list(downloading[:20]) + list(paused[:10]) + list(seeding[:10])

    if index > len(visible):
        await update.message.reply_text(
            f"序号超出范围，请输入 1 ~ {len(visible)} 之间的数字。"
        )
        return

    task = visible[index - 1]
    task_id = task.get("id", "")
    if not task_id:
        await update.message.reply_text("该任务无法删除（缺少任务 ID）。")
        return

    name = task.get("title") or task.get("name") or "未知"
    if len(name) > 50:
        name = name[:49] + "\u2026"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("确认移除", callback_data=f"delok:{user_id}:{task_id}"),
            InlineKeyboardButton("取消", callback_data=f"delno:{user_id}"),
        ]
    ])
    await update.message.reply_text(
        f"确认移除任务？\n\n{name}",
        reply_markup=keyboard,
    )


async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理删除前的二次确认弹窗：cdel:用户ID:task_id"""
    query = update.callback_query
    await query.answer()

    db = context.bot_data["db"]
    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("无效请求。", show_alert=True)
        return

    _, uid_str, task_id = parts
    try:
        user_id = int(uid_str)
    except ValueError:
        await query.answer("无效请求。", show_alert=True)
        return

    if query.from_user.id != user_id:
        await query.answer("这不是你的任务。", show_alert=True)
        return

    # 查找任务名称用于显示
    dl_client = context.bot_data.get("dl_client")
    name = task_id
    if dl_client:
        try:
            tasks = await dl_client.get_tasks()
            for t in tasks:
                if t.get("id") == task_id:
                    name = t.get("title") or t.get("name") or task_id
                    if len(name) > 50:
                        name = name[:49] + "\u2026"
                    break
        except Exception:
            pass

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("确认移除", callback_data=f"delok:{user_id}:{task_id}"),
            InlineKeyboardButton("取消", callback_data=f"delno:{user_id}"),
        ]
    ])
    await query.edit_message_text(
        f"确认移除任务？\n\n{name}",
        reply_markup=keyboard,
    )


async def delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理确认删除：delok:用户ID:task_id"""
    query = update.callback_query
    await query.answer()

    db = context.bot_data["db"]
    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("无效请求。", show_alert=True)
        return

    _, uid_str, task_id = parts
    try:
        user_id = int(uid_str)
    except ValueError:
        await query.answer("无效请求。", show_alert=True)
        return

    # 权限校验：本人或 Owner
    is_owner = db.is_owner(query.from_user.id)
    if query.from_user.id != user_id and not is_owner:
        await query.answer("无权限删除此任务。", show_alert=True)
        return

    # 非 Owner 需校验任务归属
    if not is_owner:
        user_task_ids = set(db.get_user_task_ids(query.from_user.id))
        if task_id not in user_task_ids:
            await query.answer("无权限删除此任务。", show_alert=True)
            return

    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await query.edit_message_text("下载客户端未配置。")
        return

    ok = await dl_client.delete_task(task_id)
    if ok:
        await query.edit_message_text("✅ 任务已移除。发送 /status 查看最新状态。")
    else:
        await query.edit_message_text("❌ 移除失败，请稍后重试。")


async def delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理取消删除：delno:用户ID"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("已取消。")
