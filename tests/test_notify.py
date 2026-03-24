"""Tests for bot/handlers/notify.py — download completion notifications."""

from unittest.mock import AsyncMock, MagicMock

from bot.handlers.notify import check_completed_tasks, _SNAPSHOT_KEY


def _make_context(db=None, dl_client=None, owner_id=111, snapshot=None):
    """Create a mock context for JobQueue callbacks."""
    context = MagicMock()
    context.bot_data = {}
    if db:
        context.bot_data["db"] = db
    if dl_client:
        context.bot_data["dl_client"] = dl_client
    context.bot_data["owner_id"] = owner_id
    if snapshot is not None:
        context.bot_data[_SNAPSHOT_KEY] = snapshot
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context


class TestCheckCompletedTasks:

    async def test_no_dl_client_returns_silently(self):
        context = _make_context()
        await check_completed_tasks(context)
        # No crash, no messages
        context.bot.send_message.assert_not_awaited()

    async def test_first_call_takes_snapshot_no_notification(self, db_with_owner):
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_1", "title": "Movie1", "status": 2},
            {"id": "dbid_2", "title": "Movie2", "status": 8},
        ])
        context = _make_context(db=db_with_owner, dl_client=dl_client)

        await check_completed_tasks(context)

        # Snapshot created but no messages sent
        assert _SNAPSHOT_KEY in context.bot_data
        assert context.bot_data[_SNAPSHOT_KEY] == {"dbid_1": 2, "dbid_2": 8}
        context.bot.send_message.assert_not_awaited()

    async def test_detects_newly_completed_task(self, db_with_owner):
        # User 111 (owner) downloaded this task
        db_with_owner.log_download(111, "Movie.mkv", "14 GB", task_id="dbid_1")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_1", "title": "Movie.mkv", "status": 8, "size": 15000000000},
        ])

        # Previous snapshot had status=2 (downloading)
        prev_snapshot = {"dbid_1": 2}
        context = _make_context(db=db_with_owner, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        # Notification sent to user
        context.bot.send_message.assert_awaited()
        text = context.bot.send_message.call_args_list[0][1]["text"]
        assert "下载完成" in text
        assert "Movie.mkv" in text

    async def test_no_notification_for_already_seeding(self, db_with_owner):
        db_with_owner.log_download(111, "Movie.mkv", "14 GB", task_id="dbid_1")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_1", "title": "Movie.mkv", "status": 8},
        ])

        # Previous snapshot already had status=8
        prev_snapshot = {"dbid_1": 8}
        context = _make_context(db=db_with_owner, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        context.bot.send_message.assert_not_awaited()

    async def test_no_notification_for_non_bot_task(self, db_with_owner):
        # No download_logs record for dbid_99
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_99", "title": "ManualTask", "status": 8},
        ])

        prev_snapshot = {"dbid_99": 2}
        context = _make_context(db=db_with_owner, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        context.bot.send_message.assert_not_awaited()

    async def test_owner_notified_for_other_user_completion(self, db_with_users):
        # User 333 downloaded this task
        db_with_users.log_download(333, "Movie.mkv", "14 GB", task_id="dbid_1")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_1", "title": "Movie.mkv", "status": 8, "size": 5000000000},
        ])

        prev_snapshot = {"dbid_1": 2}
        context = _make_context(db=db_with_users, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        # Two messages: one to user 333, one to owner 111
        assert context.bot.send_message.await_count == 2
        chat_ids = [c[1]["chat_id"] for c in context.bot.send_message.call_args_list]
        assert 333 in chat_ids
        assert 111 in chat_ids

    async def test_owner_not_double_notified_for_own_task(self, db_with_owner):
        # Owner 111 downloaded this task
        db_with_owner.log_download(111, "Movie.mkv", "14 GB", task_id="dbid_1")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_1", "title": "Movie.mkv", "status": 8, "size": 5000000000},
        ])

        prev_snapshot = {"dbid_1": 2}
        context = _make_context(db=db_with_owner, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        # Only one message to owner, not two
        assert context.bot.send_message.await_count == 1
        assert context.bot.send_message.call_args_list[0][1]["chat_id"] == 111

    async def test_task_disappeared_no_notification(self, db_with_owner):
        db_with_owner.log_download(111, "Movie.mkv", "14 GB", task_id="dbid_1")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[])  # task gone

        prev_snapshot = {"dbid_1": 2}
        context = _make_context(db=db_with_owner, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        # Task disappeared, no notification (not status=8)
        context.bot.send_message.assert_not_awaited()

    async def test_get_tasks_error_skips_gracefully(self, db_with_owner):
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(side_effect=Exception("network error"))

        prev_snapshot = {"dbid_1": 2}
        context = _make_context(db=db_with_owner, dl_client=dl_client, snapshot=prev_snapshot)

        await check_completed_tasks(context)

        # No crash, snapshot unchanged
        context.bot.send_message.assert_not_awaited()
        assert context.bot_data[_SNAPSHOT_KEY] == prev_snapshot
