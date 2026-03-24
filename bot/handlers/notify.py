"""下载完成通知 — 定时轮询检测任务完成并推送消息"""

from __future__ import annotations

import logging
from datetime import datetime

from telegram.ext import ContextTypes

from bot.handlers.status import _format_size

logger = logging.getLogger(__name__)

_SNAPSHOT_KEY = "_task_snapshot"


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

    try:
        tasks = await dl_client.get_tasks()
    except Exception:
        logger.debug("通知轮询: 获取任务列表失败，跳过本次")
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
        return

    # 对比：找出状态从非 8 变为 8 的任务
    newly_completed = []
    for tid, status in current.items():
        if status == 8 and prev.get(tid) not in (8, None):
            # 之前不是做种，现在是做种 → 刚完成
            newly_completed.append(tid)

    # 更新快照
    context.bot_data[_SNAPSHOT_KEY] = current

    if not newly_completed:
        return

    # 对每个新完成的任务发送通知
    for tid in newly_completed:
        record = db.get_download_by_task_id(tid)
        if not record:
            # 不是通过 Bot 添加的任务，跳过
            continue

        user_id = record["telegram_id"]
        title = record["torrent_title"] or "未知"
        size = record["torrent_size"] or ""

        # 计算用时
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

        # 从 DS 任务获取实际大小（可能比搜索结果更准确）
        task_data = task_map.get(tid, {})
        actual_size = task_data.get("size", 0)
        if actual_size:
            size = _format_size(actual_size)

        # 构建通知消息
        lines = ["✅ 下载完成", f"标题: {title[:80]}"]
        if size:
            lines.append(f"大小: {size}")
        if duration_str:
            lines.append(f"用时: 约 {duration_str}")
        text = "\n".join(lines)

        # 通知下载者
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
            logger.info("通知用户 %d: 任务 %s 已完成", user_id, tid)
        except Exception:
            logger.exception("发送完成通知失败 (user=%d)", user_id)

        # 如果下载者不是 Owner，额外通知 Owner
        if owner_id and user_id != owner_id:
            try:
                owner_text = f"用户 {user_id} 的下载已完成:\n{text}"
                await context.bot.send_message(chat_id=owner_id, text=owner_text)
            except Exception:
                logger.exception("通知 Owner 失败")
