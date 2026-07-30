"""Download Station 任务状态、分页与安全删除。"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.clients.download_station import (
    DS7FileManifest,
    DownloadStationClient,
    get_ds7_status_info,
)
from bot.handlers.status_tokens import (
    STATUS_TOKEN_REGISTRY_KEY,
    StatusTokenEntry,
    StatusTokenRegistry,
    build_status_callback_data,
)
from bot.middleware import require_auth

logger = logging.getLogger(__name__)

_PAGE_SIZE = 8
_VIEW_ACTIVE = "active"
_VIEW_COMPLETED = "completed"
_MODE_ALL = "all"
_MODE_MINE = "mine"
_ACTION_CANCEL = "cancel"
_ACTION_KEEP = "keep"
_DS7_COMPLETED_STATUSES = (5, 7, 8)
_DS7_PAUSABLE_STATUSES = frozenset({1, 2, 6, 8, 9})
_DS7_RESUMABLE_STATUSES = frozenset({3})

_STATE_PRIORITY = {
    "error": 0,
    "downloading": 1,
    "processing": 2,
    "waiting": 3,
    "paused": 4,
    "unknown": 5,
}


@dataclass(frozen=True)
class _NativeStatusPage:
    tasks: tuple[dict, ...]
    active_count: int
    completed_count: int
    page: int
    total_pages: int
    down_speed: int
    up_speed: int


def _as_number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_size(n: int | float) -> str:
    """字节数转可读格式。"""
    n = _as_number(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _build_progress_bar(percent: float, width: int = 16) -> str:
    """生成固定宽度进度条。"""
    percent = _as_number(percent)
    filled = int(width * percent / 100)
    filled = max(0, min(filled, width))
    return "[" + "█" * filled + "░" * (width - filled) + "]"


def _format_eta(remaining_bytes: int, speed: int) -> str:
    """预估剩余时间。speed 为 0 时返回空字符串。"""
    remaining_bytes = _as_number(remaining_bytes)
    speed = _as_number(speed)
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


def _task_state(task: dict) -> str:
    state = task.get("state")
    if state:
        return state
    return get_ds7_status_info(task.get("status"))[0]


def _task_status_code(task: dict) -> int | None:
    try:
        return int(task.get("status"))
    except (TypeError, ValueError):
        return None


def _task_status_label(task: dict) -> str:
    label = task.get("status_label")
    if label:
        return label
    return get_ds7_status_info(task.get("status"))[1]


def _is_completed_task(task: dict) -> bool:
    return _task_state(task) == "completed"


def _get_task_progress(task: dict) -> tuple:
    """提取 DS7 任务进度，返回 (percent, downloaded, total, down, up)。"""
    total = _as_number(task.get("size", 0))
    additional = task.get("additional") or {}
    transfer = additional.get("transfer") or {}
    downloaded = _as_number(transfer.get("size_downloaded", 0))
    down_speed = _as_number(transfer.get("speed_download", 0))
    up_speed = _as_number(transfer.get("speed_upload", 0))

    if total <= 0:
        return 0.0, 0, 0, down_speed, up_speed

    pct = max(0.0, min(downloaded / total * 100, 100.0))
    return pct, downloaded, total, down_speed, up_speed


def _format_task_detail(task: dict, index: int) -> str:
    """格式化一个 DS7 任务。"""
    name = task.get("title") or task.get("name") or "未知"
    if len(name) > 50:
        name = name[:49] + "\u2026"

    state = _task_state(task)
    label = _task_status_label(task)
    if _task_status_code(task) == 10:
        status_extra = task.get("status_extra") or {}
        extract_progress = _as_number(status_extra.get("extract_progress"), default=-1)
        if extract_progress >= 0:
            label += f" {extract_progress:.0f}%"
    pct, downloaded, total, down_speed, up_speed = _get_task_progress(task)
    lines = [f"{index}. {name}"]

    if state == "completed":
        parts = [label]
        if total > 0:
            parts.append(_format_size(total))
        if up_speed > 0:
            parts.append(f"⬆️ {_format_size(up_speed)}/s")
        lines.append(f"   {' | '.join(parts)}")
        return "\n".join(lines)

    state_icon = {
        "error": "❌",
        "paused": "⏸",
        "waiting": "⏳",
        "processing": "⚙️",
        "unknown": "❔",
    }.get(state)
    if state_icon:
        lines.append(f"   {state_icon} {label}")

    if total > 0:
        bar = _build_progress_bar(pct)
        lines.append(f"   {bar} {pct:.1f}%")
        detail_parts = [f"{_format_size(downloaded)}/{_format_size(total)}"]
        if down_speed > 0:
            detail_parts.append(f"⬇️ {_format_size(down_speed)}/s")
            eta = _format_eta(total - downloaded, down_speed)
            if eta:
                detail_parts.append(eta)
        lines.append(f"   {' | '.join(detail_parts)}")

    return "\n".join(lines)


def _filter_tasks(tasks: List[dict], db, user_id: int, mode: str) -> tuple[List[dict], str | None]:
    """按用户归属过滤任务，返回 (任务, 空状态提示)。"""
    if mode == _MODE_ALL and db.is_owner(user_id):
        return tasks, None

    user_task_ids = {str(task_id) for task_id in db.get_user_task_ids(user_id)}
    if not user_task_ids:
        return [], "你还没有通过 Bot 添加过下载任务。"

    filtered = [task for task in tasks if str(task.get("id")) in user_task_ids]
    if not filtered:
        return [], "你的下载任务已全部完成或已被删除。"
    return filtered, None


def _split_and_sort_tasks(tasks: List[dict]) -> tuple[List[dict], List[dict]]:
    active = [task for task in tasks if not _is_completed_task(task)]
    completed = [task for task in tasks if _is_completed_task(task)]
    active.sort(key=lambda task: _STATE_PRIORITY.get(_task_state(task), 99))
    return active, completed


def _summary_line(tasks: List[dict]) -> str:
    counts = {
        "downloading": 0,
        "processing": 0,
        "waiting": 0,
        "paused": 0,
        "error": 0,
        "completed": 0,
        "unknown": 0,
    }
    total_speed = 0
    for task in tasks:
        state = _task_state(task)
        counts[state if state in counts else "unknown"] += 1
        total_speed += _get_task_progress(task)[3]

    parts = [
        f"下载 {counts['downloading']}",
        f"处理 {counts['processing']}",
        f"等待 {counts['waiting']}",
        f"暂停 {counts['paused']}",
        f"错误 {counts['error']}",
        f"完成/做种 {counts['completed']}",
    ]
    line = " | ".join(parts)
    if total_speed > 0:
        line += f"\n总下载速度: {_format_size(total_speed)}/s"
    return line


def _get_status_token_registry(context) -> StatusTokenRegistry:
    registry = context.bot_data.get(STATUS_TOKEN_REGISTRY_KEY)
    if not isinstance(registry, StatusTokenRegistry):
        registry = StatusTokenRegistry()
        context.bot_data[STATUS_TOKEN_REGISTRY_KEY] = registry
    return registry


def _status_callback_data(
    user_id: int,
    mode: str,
    view: str,
    page: int,
    *,
    refresh: bool = False,
) -> str:
    suffix = ":r" if refresh else ""
    return f"stat:{user_id}:{mode}:{view}:{page}{suffix}"


def _create_task_token(
    context,
    *,
    user_id: int,
    task_id: str,
    binding_id: int | None,
    mode: str,
    view: str,
    page: int,
    action: str = "task",
    metadata: dict | None = None,
) -> str:
    return _get_status_token_registry(context).create(
        viewer_id=user_id,
        task_id=task_id,
        binding_id=binding_id,
        mode=mode,
        view=view,
        page=page,
        action=action,
        metadata=metadata,
    )


def _format_timestamp(value) -> str:
    timestamp = _as_number(value)
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _format_duration(seconds) -> str:
    seconds = max(0, int(_as_number(seconds)))
    if seconds < 60:
        return f"{seconds}秒"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分钟"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}小时{minutes % 60}分钟"
    return f"{hours // 24}天{hours % 24}小时"


def _format_task_info(task: dict) -> str:
    """格式化 DS7 详情；刻意不输出 uri 和解压密码。"""
    name = task.get("title") or task.get("name") or "未知"
    state = _task_state(task)
    label = _task_status_label(task)
    pct, downloaded, total, down_speed, up_speed = _get_task_progress(task)
    additional = task.get("additional") or {}
    detail = additional.get("detail") or {}
    transfer = additional.get("transfer") or {}
    uploaded = max(0, _as_number(transfer.get("size_uploaded")))

    lines = [f"📄 {name}", f"状态: {label}"]
    task_type = str(task.get("type") or "").upper()
    if task_type:
        lines.append(f"类型: {task_type}")
    if total > 0:
        lines.append(f"进度: {pct:.1f}% ({_format_size(downloaded)}/{_format_size(total)})")
    speeds = []
    if down_speed > 0:
        speeds.append(f"⬇️ {_format_size(down_speed)}/s")
    if up_speed > 0:
        speeds.append(f"⬆️ {_format_size(up_speed)}/s")
    if speeds:
        lines.append("速度: " + " | ".join(speeds))
    if uploaded > 0:
        ratio = uploaded / downloaded if downloaded > 0 else 0
        lines.append(f"已上传: {_format_size(uploaded)} | 分享率: {ratio:.2f}")

    destination = str(detail.get("destination") or "").strip()
    if destination:
        lines.append(f"目录: {destination}")
    for fields, title in (
        (("created_time", "create_time"), "创建"),
        (("started_time",), "开始"),
        (("completed_time",), "完成"),
    ):
        value = next((detail.get(field) for field in fields if detail.get(field)), None)
        formatted = _format_timestamp(value)
        if formatted:
            lines.append(f"{title}: {formatted}")

    if state == "waiting" and _as_number(detail.get("waiting_seconds")) > 0:
        lines.append(f"已等待: {_format_duration(detail.get('waiting_seconds'))}")
    if _as_number(detail.get("seed_elapsed")) > 0:
        lines.append(f"做种时长: {_format_duration(detail.get('seed_elapsed'))}")

    connected_seeders = _safe_count(detail.get("connected_seeders"))
    connected_leechers = _safe_count(detail.get("connected_leechers"))
    total_peers = _safe_count(detail.get("total_peers"))
    if connected_seeders or connected_leechers or total_peers:
        lines.append(
            f"连接: {connected_seeders} 做种 / {connected_leechers} 下载 / {total_peers} 节点"
        )
    return "\n".join(lines)


def _safe_count(value) -> int:
    return max(0, int(_as_number(value)))


def _can_pause_task(task: dict) -> bool:
    return _task_status_code(task) in _DS7_PAUSABLE_STATUSES


def _resume_action(task: dict) -> tuple[str, str] | None:
    code = _task_status_code(task)
    if code in _DS7_RESUMABLE_STATUSES:
        return "resume", "▶️ 继续"
    if code is not None and code >= 101:
        return "resume", "🔄 重试"
    if code == 5 and str(task.get("type") or "").lower() == "bt":
        return "resume", "🌱 重新做种"
    return None


async def _get_optional_task_statistics(
    client: DownloadStationClient,
    *,
    force_refresh: bool,
) -> dict:
    try:
        return await client.get_task_statistics(force_refresh=force_refresh)
    except Exception:
        logger.debug("获取 DS7 总速率失败，继续渲染任务列表", exc_info=True)
        return {}


def _task_action_button(task: dict, index: int, user_id: int) -> InlineKeyboardButton | None:
    task_id = str(task.get("id") or "")
    if not task_id:
        return None
    name = task.get("title") or task.get("name") or "未知"
    if len(name) > 24:
        name = name[:23] + "\u2026"
    completed = _is_completed_task(task)
    action = _ACTION_KEEP if completed else _ACTION_CANCEL
    icon = "⏹" if _task_status_code(task) in (7, 8) else ("✅" if completed else "❌")
    callback_data = f"cdel:{user_id}:{task_id}:{action}"
    if len(callback_data.encode("utf-8")) > 64:
        logger.warning("任务 ID 过长，无法生成 Telegram 操作按钮: %s", task_id)
        return None
    return InlineKeyboardButton(f"{icon} {index}. {name}", callback_data=callback_data)


def _build_status_page(
    tasks: List[dict],
    *,
    user_id: int,
    mode: str,
    view: str,
    page: int,
) -> tuple[str, InlineKeyboardMarkup]:
    active, completed = _split_and_sort_tasks(tasks)
    selected = completed if view == _VIEW_COMPLETED else active
    view = _VIEW_COMPLETED if view == _VIEW_COMPLETED else _VIEW_ACTIVE

    total_pages = max(1, math.ceil(len(selected) / _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * _PAGE_SIZE
    page_tasks = selected[start:start + _PAGE_SIZE]

    title = "完成/做种任务" if view == _VIEW_COMPLETED else "进行中的任务"
    lines = ["📊 Download Station", _summary_line(tasks), "", f"{title}:"]
    if page_tasks:
        for offset, task in enumerate(page_tasks, start + 1):
            lines.append(_format_task_detail(task, offset))
    else:
        empty_text = "当前没有完成或做种任务。" if view == _VIEW_COMPLETED else "当前没有进行中的任务。"
        lines.append(empty_text)

    if total_pages > 1:
        lines.extend(["", f"第 {page + 1}/{total_pages} 页"])

    rows = []
    for offset, task in enumerate(page_tasks, start + 1):
        button = _task_action_button(task, offset, user_id)
        if button:
            rows.append([button])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            "◀ 上一页",
            callback_data=_status_callback_data(user_id, mode, view, page - 1),
        ))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton(
            "下一页 ▶",
            callback_data=_status_callback_data(user_id, mode, view, page + 1),
        ))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            f"📥 进行中 ({len(active)})",
            callback_data=_status_callback_data(user_id, mode, _VIEW_ACTIVE, 0),
        ),
        InlineKeyboardButton(
            f"🌱 完成/做种 ({len(completed)})",
            callback_data=_status_callback_data(user_id, mode, _VIEW_COMPLETED, 0),
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            "🔄 刷新",
            callback_data=_status_callback_data(
                user_id, mode, view, page, refresh=True,
            ),
        )
    ])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _build_native_status_page(
    context,
    data: _NativeStatusPage,
    *,
    user_id: int,
    mode: str,
    view: str,
) -> tuple[str, InlineKeyboardMarkup]:
    title = "完成/做种任务" if view == _VIEW_COMPLETED else "进行中的任务"
    lines = [
        "📊 Download Station",
        f"进行中 {data.active_count} | 完成/做种 {data.completed_count}",
    ]
    rates = []
    if data.down_speed > 0:
        rates.append(f"⬇️ {_format_size(data.down_speed)}/s")
    if data.up_speed > 0:
        rates.append(f"⬆️ {_format_size(data.up_speed)}/s")
    if rates:
        lines.append("总速率: " + " | ".join(rates))
    lines.extend(["", f"{title}:"])

    start = data.page * _PAGE_SIZE
    if data.tasks:
        for offset, task in enumerate(data.tasks, start + 1):
            lines.append(_format_task_detail(task, offset))
    else:
        lines.append(
            "当前没有完成或做种任务。"
            if view == _VIEW_COMPLETED
            else "当前没有进行中的任务。"
        )
    if data.total_pages > 1:
        lines.extend(["", f"第 {data.page + 1}/{data.total_pages} 页"])

    rows = []
    for offset, task in enumerate(data.tasks, start + 1):
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        token = _create_task_token(
            context,
            user_id=user_id,
            task_id=task_id,
            binding_id=_current_binding_id(
                context.bot_data["db"], task_id,
            ),
            mode=mode,
            view=view,
            page=data.page,
        )
        name = task.get("title") or task.get("name") or "未知"
        if len(name) > 24:
            name = name[:23] + "\u2026"
        rows.append([InlineKeyboardButton(
            f"ℹ️ {offset}. {name}",
            callback_data=build_status_callback_data("open", token),
        )])

    nav = []
    if data.page > 0:
        nav.append(InlineKeyboardButton(
            "◀ 上一页",
            callback_data=_status_callback_data(
                user_id, mode, view, data.page - 1,
            ),
        ))
    if data.page + 1 < data.total_pages:
        nav.append(InlineKeyboardButton(
            "下一页 ▶",
            callback_data=_status_callback_data(
                user_id, mode, view, data.page + 1,
            ),
        ))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(
            f"📥 进行中 ({data.active_count})",
            callback_data=_status_callback_data(user_id, mode, _VIEW_ACTIVE, 0),
        ),
        InlineKeyboardButton(
            f"🌱 完成/做种 ({data.completed_count})",
            callback_data=_status_callback_data(user_id, mode, _VIEW_COMPLETED, 0),
        ),
    ])
    rows.append([InlineKeyboardButton(
        "🔄 刷新",
        callback_data=_status_callback_data(
            user_id, mode, view, data.page, refresh=True,
        ),
    )])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _load_native_status_page(
    context,
    user_id: int,
    mode: str,
    view: str,
    page: int,
    *,
    force_refresh: bool,
):
    client: DownloadStationClient = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    effective_mode = mode if mode == _MODE_ALL and db.is_owner(user_id) else _MODE_MINE
    view = _VIEW_COMPLETED if view == _VIEW_COMPLETED else _VIEW_ACTIVE
    page = max(0, page)

    if effective_mode == _MODE_MINE:
        task_ids = db.get_user_task_ids(user_id)
        if not task_ids:
            return "你还没有通过 Bot 添加过下载任务。", None
        tasks = await client.get_tasks_by_ids(
            task_ids, force_refresh=force_refresh,
        )
        tasks = [
            task for task in tasks
            if _binding_belongs_to_user(
                db, str(task.get("id") or ""), user_id,
            )
        ]
        active, completed = _split_and_sort_tasks(tasks)
        selected = completed if view == _VIEW_COMPLETED else active
        total_pages = max(1, math.ceil(len(selected) / _PAGE_SIZE))
        page = min(page, total_pages - 1)
        page_tasks = tuple(selected[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE])
        down_speed = sum(_get_task_progress(task)[3] for task in tasks)
        up_speed = sum(_get_task_progress(task)[4] for task in tasks)
        data = _NativeStatusPage(
            tasks=page_tasks,
            active_count=len(active),
            completed_count=len(completed),
            page=page,
            total_pages=total_pages,
            down_speed=int(down_speed),
            up_speed=int(up_speed),
        )
    else:
        completed_view = view == _VIEW_COMPLETED
        selected_call = client.get_tasks_page(
            page * _PAGE_SIZE,
            _PAGE_SIZE,
            statuses=_DS7_COMPLETED_STATUSES,
            status_inverse=not completed_view,
            force_refresh=force_refresh,
        )
        other_call = client.get_tasks_page(
            0,
            1,
            statuses=_DS7_COMPLETED_STATUSES,
            status_inverse=completed_view,
            force_refresh=force_refresh,
        )
        selected_page, other_page, statistics = await asyncio.gather(
            selected_call,
            other_call,
            _get_optional_task_statistics(
                client, force_refresh=force_refresh,
            ),
        )
        total_pages = max(1, math.ceil(selected_page.total / _PAGE_SIZE))
        clamped_page = min(page, total_pages - 1)
        if clamped_page != page:
            selected_page = await client.get_tasks_page(
                clamped_page * _PAGE_SIZE,
                _PAGE_SIZE,
                statuses=_DS7_COMPLETED_STATUSES,
                status_inverse=not completed_view,
                force_refresh=force_refresh,
            )
        active_count = other_page.total if completed_view else selected_page.total
        completed_count = selected_page.total if completed_view else other_page.total
        data = _NativeStatusPage(
            tasks=selected_page.tasks,
            active_count=active_count,
            completed_count=completed_count,
            page=clamped_page,
            total_pages=total_pages,
            down_speed=_safe_count(statistics.get("download_rate")),
            up_speed=_safe_count(statistics.get("upload_rate")),
        )

    return _build_native_status_page(
        context,
        data,
        user_id=user_id,
        mode=effective_mode,
        view=view,
    )


async def _load_status_page(
    context,
    user_id: int,
    mode: str,
    view: str,
    page: int,
    *,
    force_refresh: bool = False,
):
    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        return "下载客户端尚未配置。", None

    if isinstance(dl_client, DownloadStationClient):
        if await dl_client.is_ds7():
            return await _load_native_status_page(
                context,
                user_id,
                mode,
                view,
                page,
                force_refresh=force_refresh,
            )

    tasks = await dl_client.get_tasks()
    db = context.bot_data["db"]
    effective_mode = mode if mode == _MODE_ALL and db.is_owner(user_id) else _MODE_MINE

    if not tasks:
        return "当前没有下载任务。", None
    tasks, empty_message = _filter_tasks(tasks, db, user_id, effective_mode)
    if empty_message:
        return empty_message, None

    return _build_status_page(
        tasks,
        user_id=user_id,
        mode=effective_mode,
        view=view,
        page=page,
    )


async def _send_callback_notice(context, query, text: str) -> None:
    """回调已经应答后，通过新消息报告异步操作失败。"""
    try:
        await context.bot.send_message(chat_id=query.message.chat_id, text=text)
    except Exception:
        logger.exception("发送回调操作提示失败")


@require_auth
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """普通用户只看自己的任务；Owner 默认看全部，支持 /status mine。"""
    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await update.message.reply_text(
            "下载客户端尚未配置。\n管理员请先使用 /setds 完成配置。"
        )
        return

    db = context.bot_data["db"]
    user_id = update.effective_user.id
    requested_mine = bool(context.args and context.args[0].lower() == "mine")
    mode = _MODE_MINE if requested_mine or not db.is_owner(user_id) else _MODE_ALL

    try:
        text, keyboard = await _load_status_page(
            context, user_id, mode, _VIEW_ACTIVE, 0,
        )
    except Exception:
        logger.exception("获取下载任务列表失败")
        await update.message.reply_text("获取任务列表失败，请稍后重试。")
        return

    await update.message.reply_text(text, reply_markup=keyboard)


async def status_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理状态页刷新、视图切换和分页。"""
    query = update.callback_query
    db = context.bot_data["db"]

    parts = (query.data or "").split(":")
    if (
        len(parts) not in (5, 6)
        or parts[0] != "stat"
        or (len(parts) == 6 and parts[5] != "r")
    ):
        await query.answer("无效请求。", show_alert=True)
        return
    _, uid_str, mode, view, page_str = parts[:5]
    force_refresh = len(parts) == 6
    try:
        user_id = int(uid_str)
        page = int(page_str)
        if page < 0:
            raise ValueError
    except ValueError:
        await query.answer("无效请求。", show_alert=True)
        return

    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if query.from_user.id != user_id:
        await query.answer("这不是你的状态页面。", show_alert=True)
        return
    if mode not in (_MODE_ALL, _MODE_MINE) or view not in (_VIEW_ACTIVE, _VIEW_COMPLETED):
        await query.answer("无效请求。", show_alert=True)
        return

    await query.answer()
    try:
        text, keyboard = await _load_status_page(
            context,
            user_id,
            mode,
            view,
            page,
            force_refresh=force_refresh,
        )
    except Exception:
        logger.exception("刷新下载任务列表失败")
        await _send_callback_notice(context, query, "刷新失败，请稍后重试。")
        return

    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            raise


@require_auth
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """旧序号删除入口只保留引导，避免列表重排导致误删。"""
    await update.message.reply_text(
        "为避免任务顺序变化导致误删，请发送 /status，点击对应任务下方的操作按钮。"
    )


def _parse_delete_callback(data: str, prefix: str) -> tuple[int, str, str] | None:
    parts = (data or "").split(":", 3)
    if len(parts) not in (3, 4) or parts[0] != prefix:
        return None
    try:
        user_id = int(parts[1])
    except ValueError:
        return None
    task_id = parts[2]
    action = parts[3] if len(parts) == 4 else ""
    if not task_id or action not in ("", _ACTION_CANCEL, _ACTION_KEEP):
        return None
    return user_id, task_id, action


def _can_manage_task(db, actor_id: int, task_id: str) -> bool:
    if db.is_owner(actor_id):
        return True
    owns_task = getattr(db, "user_owns_active_task", None)
    if callable(owns_task):
        return bool(owns_task(actor_id, task_id))
    return task_id in {str(owned_id) for owned_id in db.get_user_task_ids(actor_id)}


def _active_binding(db, task_id: str) -> dict | None:
    getter = getattr(db, "get_active_task_binding", None)
    if not callable(getter):
        return None
    return getter(task_id)


def _current_binding_id(db, task_id: str) -> int | None:
    binding = _active_binding(db, task_id)
    return binding["id"] if binding else None


def _binding_belongs_to_user(db, task_id: str, user_id: int) -> bool:
    binding = _active_binding(db, task_id)
    return bool(binding and binding["telegram_id"] == user_id)


def _token_binding_is_current(db, entry: StatusTokenEntry) -> bool:
    """验证 token 仍指向创建时的授权绑定代际。"""
    if not db.is_authorized(entry.viewer_id):
        return False

    binding = _active_binding(db, entry.task_id)
    if entry.binding_id is None:
        return db.is_owner(entry.viewer_id) and binding is None
    if not binding or binding["id"] != entry.binding_id:
        return False
    return db.is_owner(entry.viewer_id) or binding["telegram_id"] == entry.viewer_id


def _deactivate_token_binding(db, entry: StatusTokenEntry) -> bool:
    """只失活 token 创建时捕获的绑定，绝不清理后续重绑定。"""
    if entry.binding_id is None:
        return False
    deactivate = getattr(db, "deactivate_task_binding", None)
    if not callable(deactivate):
        return False
    return bool(deactivate(entry.task_id, entry.binding_id))


async def _find_current_task(dl_client, task_id: str) -> dict | None:
    if (
        isinstance(dl_client, DownloadStationClient)
        and await dl_client.is_ds7()
    ):
        return await dl_client.get_task(task_id, force_refresh=True)
    tasks = await dl_client.get_tasks()
    return next((task for task in tasks if str(task.get("id")) == task_id), None)


async def _render_status_after_action(
    context,
    query,
    entry: StatusTokenEntry,
    notice: str,
) -> None:
    text, keyboard = await _load_status_page(
        context,
        entry.viewer_id,
        entry.mode,
        entry.view,
        entry.page,
        force_refresh=True,
    )
    await query.edit_message_text(
        f"{notice}\n\n{text}",
        reply_markup=keyboard,
    )


async def _render_stale_token(
    context,
    query,
    entry: StatusTokenEntry,
    notice: str = "任务归属已变化或操作权限已撤销，未继续执行。",
) -> None:
    db = context.bot_data["db"]
    if db.is_authorized(entry.viewer_id):
        await _render_status_after_action(context, query, entry, notice)
    else:
        await query.edit_message_text(notice)


def _binding_snapshot_is_current(db, task_id: str, snapshot: dict | None) -> bool:
    current = _active_binding(db, task_id)
    if snapshot is None:
        return current is None
    return bool(current and current["id"] == snapshot["id"])


async def _redirect_old_ds_action(query) -> None:
    await query.answer()
    await query.edit_message_text(
        "此旧版 Download Station 操作按钮已停用，请发送 /status 使用当前安全操作。"
    )


async def _handle_old_ds_capability(query, client) -> bool:
    """DS7 旧按钮直接失效；DSM6 返回 False 继续 legacy 流程。"""
    if not isinstance(client, DownloadStationClient):
        return False
    try:
        is_ds7 = await client.is_ds7()
    except Exception:
        logger.exception("确认旧 Download Station 回调的 API 版本失败")
        await query.answer("无法确认 Download Station 版本，请稍后重试。", show_alert=True)
        return True
    if not is_ds7:
        return False
    await _redirect_old_ds_action(query)
    return True


def _task_panel_keyboard(task: dict, token: str) -> InlineKeyboardMarkup:
    rows = []
    if _can_pause_task(task):
        rows.append([InlineKeyboardButton(
            "⏸ 暂停",
            callback_data=build_status_callback_data("pause", token),
        )])
    resume = _resume_action(task)
    if resume:
        operation, label = resume
        rows.append([InlineKeyboardButton(
            label,
            callback_data=build_status_callback_data(operation, token),
        )])
    rows.append([InlineKeyboardButton(
        "🗑 移除任务",
        callback_data=build_status_callback_data("remove", token),
    )])
    rows.append([InlineKeyboardButton(
        "↩ 返回状态页",
        callback_data=build_status_callback_data("back", token),
    )])
    return InlineKeyboardMarkup(rows)


async def _open_task_panel(context, query, entry: StatusTokenEntry, token: str) -> None:
    client: DownloadStationClient = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    task = await client.get_task(entry.task_id, force_refresh=True)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(context, query, entry)
        return
    if not task:
        _deactivate_token_binding(db, entry)
        await _render_status_after_action(
            context, query, entry, "任务已不存在，活动归属已清理。",
        )
        return
    await query.edit_message_text(
        _format_task_info(task),
        reply_markup=_task_panel_keyboard(task, token),
    )


async def _control_task(
    context,
    query,
    entry: StatusTokenEntry,
    operation: str,
) -> None:
    client: DownloadStationClient = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    task = await client.get_task(entry.task_id, force_refresh=True)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(context, query, entry)
        return
    if not task:
        _deactivate_token_binding(db, entry)
        await _render_status_after_action(
            context, query, entry, "任务已不存在，活动归属已清理。",
        )
        return

    if operation == "pause":
        if not _can_pause_task(task):
            await _render_status_after_action(
                context, query, entry, "任务状态已变化，当前不能暂停。",
            )
            return
        ok = await client.pause_task(entry.task_id)
        notice = "✅ 已提交暂停操作。" if ok else "❌ 暂停失败，请稍后重试。"
    else:
        if not _resume_action(task):
            await _render_status_after_action(
                context, query, entry, "任务状态已变化，当前不能继续或重试。",
            )
            return
        ok = await client.resume_task(entry.task_id)
        notice = "✅ 已提交继续操作。" if ok else "❌ 继续或重试失败，请稍后重试。"
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(
            context, query, entry,
            "任务归属或操作权限已变化，请刷新后核对任务状态。",
        )
        return
    await _render_status_after_action(context, query, entry, notice)


async def _show_remove_confirmation(
    context,
    query,
    entry: StatusTokenEntry,
    page_token: str,
) -> None:
    client: DownloadStationClient = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    task = await client.get_task(entry.task_id, force_refresh=True)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(context, query, entry)
        return
    if not task:
        _deactivate_token_binding(db, entry)
        await _render_status_after_action(
            context, query, entry, "任务已不存在，活动归属已清理。",
        )
        return

    name = task.get("title") or task.get("name") or entry.task_id
    if len(name) > 80:
        name = name[:79] + "\u2026"
    completed = _is_completed_task(task)
    keep_token = _create_task_token(
        context,
        user_id=entry.viewer_id,
        task_id=entry.task_id,
        binding_id=entry.binding_id,
        mode=entry.mode,
        view=entry.view,
        page=entry.page,
        action="keep",
    )
    rows = [[InlineKeyboardButton(
        "⏹ 移除任务，保留文件" if completed else "🛑 取消下载任务",
        callback_data=build_status_callback_data("keep", keep_token),
    )]]

    lines = [
        "确认移除任务？",
        "",
        name,
        "",
    ]
    if completed:
        lines.append("默认操作只移除 Download Station 任务，下载文件会保留。")
        code = _task_status_code(task)
        if code in (5, 8) and str(task.get("type") or "").lower() == "bt":
            try:
                manifest = await client.prepare_file_manifest(task)
            except Exception:
                if not _token_binding_is_current(db, entry):
                    await _render_stale_token(context, query, entry)
                    return
                logger.warning("无法生成安全文件清单: %s", entry.task_id, exc_info=True)
                lines.append("未能核实精确文件清单，已关闭删除文件选项。")
            else:
                if not _token_binding_is_current(db, entry):
                    await _render_stale_token(context, query, entry)
                    return
                purge_token = _create_task_token(
                    context,
                    user_id=entry.viewer_id,
                    task_id=entry.task_id,
                    binding_id=entry.binding_id,
                    mode=entry.mode,
                    view=entry.view,
                    page=entry.page,
                    action="purge",
                    metadata={"fingerprint": manifest.fingerprint},
                )
                rows.append([InlineKeyboardButton(
                    f"🗑 删除 {len(manifest.paths)} 个文件并移除任务",
                    callback_data=build_status_callback_data("purge", purge_token),
                )])
                lines.append(
                    f"高风险操作将删除已核实的 {len(manifest.paths)} 个文件"
                    f"（约 {_format_size(manifest.total_size)}），且不可恢复；空目录可能保留。"
                )
        elif code == 7:
            lines.append("任务正在准备做种，暂不开放文件删除；可刷新后再试。")
        else:
            lines.append("该任务类型或状态不支持安全删除文件。")
    else:
        lines.append("未完成数据将由 Download Station 按自身规则处理。")

    rows.append([InlineKeyboardButton(
        "↩ 返回详情",
        callback_data=build_status_callback_data("open", page_token),
    )])
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _execute_token_keep(context, query, entry: StatusTokenEntry) -> None:
    client: DownloadStationClient = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    task = await client.get_task(entry.task_id, force_refresh=True)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(context, query, entry)
        return
    if not task:
        _deactivate_token_binding(db, entry)
        await _render_status_after_action(
            context, query, entry, "任务已不存在，活动归属已清理。",
        )
        return

    completed = _is_completed_task(task)
    ok = await client.delete_task(entry.task_id, delete_files=False)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(
            context, query, entry,
            "任务归属或操作权限已变化，请刷新后核对移除结果。",
        )
        return
    if ok:
        _deactivate_token_binding(db, entry)
        notice = (
            "✅ 任务已移除，下载文件已保留。"
            if completed
            else "✅ 下载任务已取消，未完成数据由 Download Station 处理。"
        )
    else:
        notice = "❌ 移除失败，请稍后重试。"
    await _render_status_after_action(context, query, entry, notice)


async def _execute_token_purge(context, query, entry: StatusTokenEntry) -> None:
    client: DownloadStationClient = context.bot_data["dl_client"]
    db = context.bot_data["db"]
    task = await client.get_task(entry.task_id, force_refresh=True)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(context, query, entry)
        return
    if not task:
        _deactivate_token_binding(db, entry)
        await _render_status_after_action(
            context, query, entry, "任务已不存在，活动归属已清理。",
        )
        return
    code = _task_status_code(task or {})
    if (
        code not in (5, 8)
        or str(task.get("type") or "").lower() != "bt"
    ):
        await _render_status_after_action(
            context, query, entry, "任务状态已变化，未执行文件删除。",
        )
        return

    try:
        manifest: DS7FileManifest = await client.prepare_file_manifest(task)
    except Exception:
        if not _token_binding_is_current(db, entry):
            await _render_stale_token(context, query, entry)
            return
        logger.exception("执行前无法重新核实文件清单: %s", entry.task_id)
        await _render_status_after_action(
            context, query, entry, "无法重新核实精确文件清单，未执行任何删除。",
        )
        return
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(context, query, entry)
        return
    if manifest.fingerprint != entry.metadata.get("fingerprint"):
        await _render_status_after_action(
            context, query, entry, "文件清单已变化，未执行删除；请重新确认。",
        )
        return

    if code == 8:
        paused = await client.pause_task(entry.task_id)
        if not _token_binding_is_current(db, entry):
            await _render_stale_token(context, query, entry)
            return
        if not paused:
            await _render_status_after_action(
                context, query, entry, "停止做种失败，未执行文件删除。",
            )
            return

        paused_task = await client.wait_for_task_status(entry.task_id, (3,))
        if not _token_binding_is_current(db, entry):
            await _render_stale_token(context, query, entry)
            return
        if not paused_task:
            await _render_status_after_action(
                context, query, entry, "等待做种任务暂停超时，未执行文件删除。",
            )
            return

    files_deleted = await client.delete_file_manifest(manifest)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(
            context, query, entry,
            "任务归属或操作权限已变化，请在 DSM 中核对文件状态。",
        )
        return
    if not files_deleted:
        await _render_status_after_action(
            context,
            query,
            entry,
            "❌ 文件删除未完整成功，任务仍保留；请在 DSM 中核对文件状态。",
        )
        return

    task_deleted = await client.delete_task(entry.task_id, delete_files=False)
    if not _token_binding_is_current(db, entry):
        await _render_stale_token(
            context, query, entry,
            "任务归属或操作权限已变化，请刷新后核对移除结果。",
        )
        return
    if not task_deleted:
        await _render_status_after_action(
            context,
            query,
            entry,
            "⚠️ 下载文件已删除，但任务移除失败；请刷新后再次仅移除任务。",
        )
        return
    _deactivate_token_binding(db, entry)
    await _render_status_after_action(
        context, query, entry, "✅ 已删除核实过的下载文件并移除任务。",
    )


async def ds_status_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 DS7 详情、控制和 token 化删除回调。"""
    query = update.callback_query
    parts = (query.data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "dst":
        await query.answer("无效请求。", show_alert=True)
        return
    _, operation, token = parts
    if operation not in {"open", "back", "pause", "resume", "remove", "keep", "purge"}:
        await query.answer("无效请求。", show_alert=True)
        return

    registry = _get_status_token_registry(context)
    entry = registry.get(token)
    if not entry:
        await query.answer("操作已过期，请重新发送 /status。", show_alert=True)
        return
    db = context.bot_data["db"]
    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if query.from_user.id != entry.viewer_id:
        await query.answer("这不是你的任务操作。", show_alert=True)
        return
    if not _token_binding_is_current(db, entry):
        await query.answer("无权限操作此任务，或任务归属已变化。", show_alert=True)
        return
    if (operation in {"keep", "purge"}) != (entry.action in {"keep", "purge"}):
        await query.answer("无效请求。", show_alert=True)
        return
    if operation in {"keep", "purge"} and operation != entry.action:
        await query.answer("无效请求。", show_alert=True)
        return
    if entry.action == "task" and operation in {"keep", "purge"}:
        await query.answer("无效请求。", show_alert=True)
        return

    client = context.bot_data.get("dl_client")
    if not isinstance(client, DownloadStationClient):
        await query.answer("该操作仅支持 DS7。", show_alert=True)
        return
    try:
        is_ds7 = await client.is_ds7()
    except Exception:
        logger.exception("确认 Download Station API 版本失败")
        await query.answer("无法确认 DS7 状态，请稍后重试。", show_alert=True)
        return
    if not is_ds7:
        await query.answer("该操作仅支持 DS7。", show_alert=True)
        return
    if not _token_binding_is_current(db, entry):
        await query.answer("任务归属或操作权限已变化。", show_alert=True)
        return

    if operation in {"keep", "purge"}:
        entry = registry.consume(token)
        if not entry:
            await query.answer("操作已执行或已过期。", show_alert=True)
            return

    await query.answer()
    try:
        if operation == "open":
            await _open_task_panel(context, query, entry, token)
        elif operation == "back":
            await _render_status_after_action(context, query, entry, "已刷新任务状态。")
        elif operation in {"pause", "resume"}:
            await _control_task(context, query, entry, operation)
        elif operation == "remove":
            await _show_remove_confirmation(context, query, entry, token)
        elif operation == "keep":
            await _execute_token_keep(context, query, entry)
        else:
            await _execute_token_purge(context, query, entry)
    except Exception:
        logger.exception("DS7 状态操作失败: %s", operation)
        await _send_callback_notice(context, query, "操作失败，请稍后重试。")


async def delete_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示基于当前任务状态的安全删除确认。"""
    query = update.callback_query
    db = context.bot_data["db"]
    parsed = _parse_delete_callback(query.data, "cdel")
    if not parsed:
        await query.answer("无效请求。", show_alert=True)
        return
    user_id, task_id, requested_action = parsed

    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if query.from_user.id != user_id:
        await query.answer("这不是你的任务操作。", show_alert=True)
        return

    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await query.answer("下载客户端未配置。", show_alert=True)
        return
    binding_snapshot = _active_binding(db, task_id)
    if await _handle_old_ds_capability(query, dl_client):
        return
    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if not _binding_snapshot_is_current(db, task_id, binding_snapshot):
        await query.answer("任务归属已变化，请重新发送 /status。", show_alert=True)
        return
    if not _can_manage_task(db, query.from_user.id, task_id):
        await query.answer("无权限操作此任务。", show_alert=True)
        return

    await query.answer()
    try:
        task = await _find_current_task(dl_client, task_id)
    except Exception:
        logger.exception("确认删除前获取任务失败")
        await _send_callback_notice(context, query, "获取任务状态失败，请稍后重试。")
        return
    if (
        not db.is_authorized(query.from_user.id)
        or not _binding_snapshot_is_current(db, task_id, binding_snapshot)
        or not _can_manage_task(db, query.from_user.id, task_id)
    ):
        await _send_callback_notice(context, query, "任务归属或操作权限已变化，请重新发送 /status。")
        return
    if not task:
        await _send_callback_notice(context, query, "任务已不存在。发送 /status 刷新。")
        return

    completed = _is_completed_task(task)
    action = _ACTION_KEEP if completed else (requested_action or _ACTION_CANCEL)
    name = task.get("title") or task.get("name") or task_id
    if len(name) > 60:
        name = name[:59] + "\u2026"

    if action == _ACTION_KEEP:
        if _task_status_code(task) in (7, 8):
            prompt = f"确认停止做种并移除任务？\n\n{name}\n\n已下载文件将保留。"
            button_text = "⏹ 停止做种，保留文件"
        else:
            prompt = f"确认移除已完成任务？\n\n{name}\n\n已下载文件将保留。"
            button_text = "✅ 移除任务，保留文件"
    else:
        prompt = (
            f"确认取消下载任务？\n\n{name}\n\n"
            "未完成数据将由 Download Station 按自身规则处理。"
        )
        button_text = "🛑 取消下载任务"

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            button_text,
            callback_data=f"delok:{user_id}:{task_id}:{action}",
        ),
        InlineKeyboardButton("取消", callback_data=f"delno:{user_id}"),
    ]])
    await query.edit_message_text(prompt, reply_markup=keyboard)


async def delete_execute_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """按 task_id 删除任务，并在执行前再次检查是否已经完成。"""
    query = update.callback_query
    db = context.bot_data["db"]
    parsed = _parse_delete_callback(query.data, "delok")
    if not parsed:
        await query.answer("无效请求。", show_alert=True)
        return
    user_id, task_id, action = parsed
    action = action or _ACTION_CANCEL

    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if query.from_user.id != user_id and not db.is_owner(query.from_user.id):
        await query.answer("无权限操作此任务。", show_alert=True)
        return

    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        await query.answer()
        await query.edit_message_text("下载客户端未配置。")
        return
    binding_snapshot = _active_binding(db, task_id)
    if await _handle_old_ds_capability(query, dl_client):
        return
    if not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if not _binding_snapshot_is_current(db, task_id, binding_snapshot):
        await query.answer("任务归属已变化，请重新发送 /status。", show_alert=True)
        return
    if not _can_manage_task(db, query.from_user.id, task_id):
        await query.answer("无权限操作此任务。", show_alert=True)
        return

    await query.answer()
    try:
        task = await _find_current_task(dl_client, task_id)
    except Exception:
        logger.exception("执行删除前获取任务失败")
        await query.edit_message_text("获取任务状态失败，请稍后重试。")
        return
    if (
        not db.is_authorized(query.from_user.id)
        or not _binding_snapshot_is_current(db, task_id, binding_snapshot)
        or not _can_manage_task(db, query.from_user.id, task_id)
    ):
        await query.edit_message_text("任务归属或操作权限已变化，请重新发送 /status。")
        return
    if not task:
        if binding_snapshot is not None:
            db.deactivate_task_binding(task_id, binding_snapshot["id"])
        await query.edit_message_text("任务已不存在。发送 /status 查看最新状态。")
        return

    completed_during_confirmation = action == _ACTION_CANCEL and _is_completed_task(task)

    try:
        # DS2 Task.delete 没有可靠的“删除目标文件”参数；状态页不调用路径推测式清理。
        ok = await dl_client.delete_task(task_id, delete_files=False)
    except Exception:
        logger.exception("删除 Download Station 任务失败: %s", task_id)
        ok = False

    if not ok:
        await query.edit_message_text("❌ 移除失败，请稍后重试。")
        return

    if (
        not db.is_authorized(query.from_user.id)
        or not _binding_snapshot_is_current(db, task_id, binding_snapshot)
        or not _can_manage_task(db, query.from_user.id, task_id)
    ):
        await query.edit_message_text(
            "任务归属或操作权限已变化，请发送 /status 核对移除结果。"
        )
        return
    if binding_snapshot is not None:
        db.deactivate_task_binding(task_id, binding_snapshot["id"])
    if completed_during_confirmation:
        await query.edit_message_text(
            "✅ 任务在确认期间已完成，已安全移除任务并保留文件。发送 /status 刷新。"
        )
    elif action == _ACTION_CANCEL:
        await query.edit_message_text(
            "✅ 下载任务已取消，未完成数据由 Download Station 处理。发送 /status 刷新。"
        )
    else:
        await query.edit_message_text("✅ 任务已移除，下载文件已保留。发送 /status 刷新。")


async def delete_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消删除，并验证按钮归属。"""
    query = update.callback_query
    parts = (query.data or "").split(":", 1)
    try:
        user_id = int(parts[1]) if len(parts) == 2 and parts[0] == "delno" else None
    except ValueError:
        user_id = None

    db = context.bot_data.get("db")
    if user_id is None:
        await query.answer("无效请求。", show_alert=True)
        return
    if db and not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return
    if query.from_user.id != user_id:
        await query.answer("这不是你的任务操作。", show_alert=True)
        return

    dl_client = context.bot_data.get("dl_client")
    if await _handle_old_ds_capability(query, dl_client):
        return
    if db and not db.is_authorized(query.from_user.id):
        await query.answer("无权限执行此操作。", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text("已取消。")
