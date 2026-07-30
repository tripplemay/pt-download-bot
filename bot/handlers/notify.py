"""下载完成通知 — 定时轮询检测任务完成并推送消息"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import ContextTypes

from bot.clients.download_station import DownloadStationClient, get_ds7_status_info
from bot.handlers.status import _format_size

logger = logging.getLogger(__name__)

_SNAPSHOT_KEY = "_task_snapshot"
_PENDING_KEY = "_completion_notification_pending"


def _build_completion_messages(record: dict, task_data: dict,
                               owner_id: int | None) -> dict[int, str]:
    user_id = record["telegram_id"]
    title = record["torrent_title"] or "未知"
    size = record["torrent_size"] or ""

    duration_str = ""
    if record.get("created_at"):
        try:
            created = datetime.fromisoformat(record["created_at"])
            elapsed = datetime.now() - created
            total_minutes = int(elapsed.total_seconds() / 60)
            if total_minutes < 60:
                duration_str = f"{total_minutes}分钟"
            else:
                hours = total_minutes // 60
                mins = total_minutes % 60
                duration_str = f"{hours}小时{mins}分钟" if mins else f"{hours}小时"
        except Exception:
            pass

    actual_size = task_data.get("size", 0)
    if actual_size:
        size = _format_size(actual_size)

    lines = ["✅ 下载完成", f"标题: {title[:80]}"]
    if size:
        lines.append(f"大小: {size}")
    if duration_str:
        lines.append(f"用时: 约 {duration_str}")
    text = "\n".join(lines)

    messages = {user_id: text}
    if owner_id and user_id != owner_id:
        messages[owner_id] = f"用户 {user_id} 的下载已完成:\n{text}"
    return messages


async def _deliver_pending_notifications(context, pending: dict) -> None:
    """发送每个待处理接收者一次，仅保留本轮失败项。"""
    for task_id, recipients in list(pending.items()):
        for chat_id, text in list(recipients.items()):
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception:
                logger.exception(
                    "发送完成通知失败 (task=%s, recipient=%s)",
                    task_id,
                    chat_id,
                )
            else:
                recipients.pop(chat_id, None)
                logger.info("通知接收者 %s: 任务 %s 已完成", chat_id, task_id)
        if not recipients:
            pending.pop(task_id, None)


async def check_completed_tasks(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue 回调：检测新完成的任务并通知用户。

    首次调用时仅做快照（不发通知），后续调用对比快照检测状态变化。
    """
    dl_client = context.bot_data.get("dl_client")
    if not dl_client:
        return

    db = context.bot_data.get("db")
    if not db:
        return

    owner_id = context.bot_data.get("owner_id")
    tracked_task_ids: set[str] | None = None
    pending = context.bot_data.setdefault(_PENDING_KEY, {})

    try:
        if (
            isinstance(dl_client, DownloadStationClient)
            and await dl_client.is_ds7()
        ):
            active_task_ids = db.get_active_task_ids()
            tracked_task_ids = {str(task_id) for task_id in active_task_ids}
            tasks = await dl_client.get_tasks_by_ids(active_task_ids)
        else:
            tasks = await dl_client.get_tasks()
    except Exception:
        logger.debug("通知轮询: 获取任务列表失败，跳过本次")
        await _deliver_pending_notifications(context, pending)
        return

    # 构建当前快照: {task_id: status}
    current = {}
    task_map = {}
    for t in tasks:
        tid = t.get("id", "")
        if tid:
            current[tid] = t.get("status")
            task_map[tid] = t

    prev = context.bot_data.get(_SNAPSHOT_KEY)

    # 首次调用：仅做快照，不发通知
    if prev is None:
        context.bot_data[_SNAPSHOT_KEY] = current
        logger.info("通知轮询: 初始化快照（%d 个任务）", len(current))
        await _deliver_pending_notifications(context, pending)
        return

    # Task.get 偶尔可能少返回一个仍在跟踪的任务。保留其上一状态，避免它
    # 下一轮以完成态恢复时因为 previous_status=None 而永久漏通知。
    if tracked_task_ids is not None:
        for task_id in tracked_task_ids:
            if task_id not in current and task_id in prev:
                current[task_id] = prev[task_id]

    # 对比：找出首次进入完成/准备做种/做种状态的任务。
    newly_completed = []
    for tid, status in current.items():
        current_completed = get_ds7_status_info(status)[0] == "completed"
        previous_status = prev.get(tid)
        previous_completed = get_ds7_status_info(previous_status)[0] == "completed"
        if current_completed and (tid not in prev or not previous_completed):
            newly_completed.append(tid)

    # 更新快照
    context.bot_data[_SNAPSHOT_KEY] = current

    # 新完成事件只入队一次。队列按接收者记录，成功发送后立即移除，失败项
    # 留到后续轮询重试，避免 Owner 和下载者互相导致重复通知。
    for tid in newly_completed:
        if tid in pending:
            continue
        record = db.get_download_by_task_id(tid)
        if not record:
            # 不是通过 Bot 添加的任务，跳过
            continue
        pending[tid] = _build_completion_messages(
            record,
            task_map.get(tid, {}),
            owner_id,
        )

    await _deliver_pending_notifications(context, pending)
