"""Tests for bot/handlers/notify.py — download completion notifications."""

from unittest.mock import AsyncMock, MagicMock

from bot.clients.download_station import DownloadStationClient
from bot.handlers.notify import check_completed_tasks, _PENDING_KEY, _SNAPSHOT_KEY


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


def _make_ds7_client(tasks):
    """Create a DS7-typed test double without opening an HTTP client."""
    client = DownloadStationClient.__new__(DownloadStationClient)
    client.is_ds7 = AsyncMock(return_value=True)
    client.get_tasks_by_ids = AsyncMock(return_value=tasks)
    client.get_tasks = AsyncMock(
        side_effect=AssertionError("DS7 notifications must not scan all tasks")
    )
    return client


def _make_dsm6_client(tasks):
    """Create a DSM6-typed test double without opening an HTTP client."""
    client = DownloadStationClient.__new__(DownloadStationClient)
    client.is_ds7 = AsyncMock(return_value=False)
    client.get_tasks = AsyncMock(return_value=tasks)
    client.get_tasks_by_ids = AsyncMock(
        side_effect=AssertionError("DSM6 notifications cannot use Task.get")
    )
    return client


class TestCheckCompletedTasks:

    async def test_ds7_only_fetches_active_tracked_tasks(self, db_with_owner):
        db_with_owner.log_download(
            111, "Tracked 1.mkv", "1 GB", task_id="tracked_1"
        )
        db_with_owner.log_download(
            111, "Tracked 2.mkv", "2 GB", task_id="tracked_2"
        )
        get_active_task_ids = MagicMock(
            wraps=db_with_owner.get_active_task_ids
        )
        db_with_owner.get_active_task_ids = get_active_task_ids
        dl_client = _make_ds7_client([
            {"id": "tracked_1", "title": "Tracked 1.mkv", "status": 2},
            {"id": "tracked_2", "title": "Tracked 2.mkv", "status": 8},
        ])
        context = _make_context(db=db_with_owner, dl_client=dl_client)

        await check_completed_tasks(context)

        dl_client.is_ds7.assert_awaited_once_with()
        get_active_task_ids.assert_called_once_with()
        dl_client.get_tasks_by_ids.assert_awaited_once_with(
            ["tracked_1", "tracked_2"]
        )
        dl_client.get_tasks.assert_not_awaited()
        assert context.bot_data[_SNAPSHOT_KEY] == {
            "tracked_1": 2,
            "tracked_2": 8,
        }
        context.bot.send_message.assert_not_awaited()

    async def test_ds7_no_active_ids_initializes_empty_snapshot_without_scan(
        self, db_with_owner
    ):
        get_active_task_ids = MagicMock(
            wraps=db_with_owner.get_active_task_ids
        )
        db_with_owner.get_active_task_ids = get_active_task_ids
        dl_client = _make_ds7_client([])
        context = _make_context(db=db_with_owner, dl_client=dl_client)

        await check_completed_tasks(context)

        get_active_task_ids.assert_called_once_with()
        dl_client.get_tasks_by_ids.assert_awaited_once_with([])
        dl_client.get_tasks.assert_not_awaited()
        assert context.bot_data[_SNAPSHOT_KEY] == {}
        context.bot.send_message.assert_not_awaited()

    async def test_dsm6_download_station_uses_legacy_task_list(
        self, db_with_owner
    ):
        get_active_task_ids = MagicMock(
            wraps=db_with_owner.get_active_task_ids
        )
        db_with_owner.get_active_task_ids = get_active_task_ids
        dl_client = _make_dsm6_client([
            {"id": "legacy_1", "title": "Legacy", "status": 2},
        ])
        context = _make_context(db=db_with_owner, dl_client=dl_client)

        await check_completed_tasks(context)

        dl_client.is_ds7.assert_awaited_once_with()
        dl_client.get_tasks.assert_awaited_once_with()
        dl_client.get_tasks_by_ids.assert_not_awaited()
        get_active_task_ids.assert_not_called()
        assert context.bot_data[_SNAPSHOT_KEY] == {"legacy_1": 2}

    async def test_profile_probe_failure_still_retries_pending_notification(
        self, db_with_owner
    ):
        dl_client = _make_ds7_client([])
        dl_client.is_ds7.side_effect = ConnectionError("probe failed")
        context = _make_context(
            db=db_with_owner,
            dl_client=dl_client,
            snapshot={"tracked_1": 2},
        )
        context.bot_data[_PENDING_KEY] = {
            "tracked_1": {111: "retry completion notification"},
        }

        await check_completed_tasks(context)

        dl_client.is_ds7.assert_awaited_once_with()
        dl_client.get_tasks_by_ids.assert_not_awaited()
        dl_client.get_tasks.assert_not_awaited()
        context.bot.send_message.assert_awaited_once_with(
            chat_id=111,
            text="retry completion notification",
        )
        assert context.bot_data[_PENDING_KEY] == {}
        assert context.bot_data[_SNAPSHOT_KEY] == {"tracked_1": 2}

    async def test_ds7_transient_missing_task_does_not_lose_completion(
        self, db_with_owner
    ):
        db_with_owner.log_download(
            111, "Tracked.mkv", "1 GB", task_id="tracked_1"
        )
        dl_client = _make_ds7_client([])
        dl_client.get_tasks_by_ids.side_effect = [
            [],
            [{"id": "tracked_1", "status": 8, "size": 1024}],
        ]
        context = _make_context(
            db=db_with_owner,
            dl_client=dl_client,
            snapshot={"tracked_1": 2},
        )

        await check_completed_tasks(context)
        assert context.bot_data[_SNAPSHOT_KEY] == {"tracked_1": 2}
        context.bot.send_message.assert_not_awaited()

        await check_completed_tasks(context)
        assert context.bot_data[_SNAPSHOT_KEY] == {"tracked_1": 8}
        context.bot.send_message.assert_awaited_once()

    async def test_ds7_new_tracked_task_already_completed_notifies_after_startup(
        self, db_with_owner
    ):
        db_with_owner.log_download(
            111, "Startup.mkv", "1 GB", task_id="startup_done"
        )
        dl_client = _make_ds7_client([])
        dl_client.get_tasks_by_ids.side_effect = [
            [{"id": "startup_done", "status": 8}],
            [
                {"id": "startup_done", "status": 8},
                {"id": "late_done", "status": 5},
            ],
        ]
        context = _make_context(db=db_with_owner, dl_client=dl_client)

        await check_completed_tasks(context)
        context.bot.send_message.assert_not_awaited()

        db_with_owner.log_download(
            111, "Late.mkv", "2 GB", task_id="late_done"
        )
        await check_completed_tasks(context)

        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.call_args.kwargs["chat_id"] == 111
        assert "Late.mkv" in context.bot.send_message.call_args.kwargs["text"]
        assert "Startup.mkv" not in context.bot.send_message.call_args.kwargs["text"]

    async def test_failed_owner_delivery_retries_without_repeating_user(
        self, db_with_users
    ):
        db_with_users.log_download(
            333, "Retry.mkv", "3 GB", task_id="retry_task"
        )
        dl_client = _make_ds7_client([])
        dl_client.get_tasks_by_ids.side_effect = [
            [{"id": "retry_task", "status": 8}],
            RuntimeError("temporary DS failure"),
        ]
        context = _make_context(
            db=db_with_users,
            dl_client=dl_client,
            snapshot={"retry_task": 2},
        )
        attempts = []

        async def send_message(*, chat_id, text):
            attempts.append(chat_id)
            if chat_id == 111 and attempts.count(111) == 1:
                raise RuntimeError("temporary Telegram failure")

        context.bot.send_message.side_effect = send_message

        await check_completed_tasks(context)

        assert attempts == [333, 111]
        assert set(context.bot_data[_PENDING_KEY]["retry_task"]) == {111}

        await check_completed_tasks(context)

        assert attempts == [333, 111, 111]
        assert context.bot_data[_PENDING_KEY] == {}

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

    async def test_finished_status_five_also_notifies(self, db_with_owner):
        db_with_owner.log_download(111, "Finished.mkv", "1 GB", task_id="dbid_5")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_5", "title": "Finished.mkv", "status": 5},
        ])
        context = _make_context(
            db=db_with_owner,
            dl_client=dl_client,
            snapshot={"dbid_5": 2},
        )

        await check_completed_tasks(context)

        assert context.bot.send_message.await_count == 1
        assert "下载完成" in context.bot.send_message.call_args[1]["text"]

    async def test_preseeding_to_seeding_does_not_double_notify(self, db_with_owner):
        db_with_owner.log_download(111, "Seed.mkv", "1 GB", task_id="dbid_7")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "dbid_7", "title": "Seed.mkv", "status": 8},
        ])
        context = _make_context(
            db=db_with_owner,
            dl_client=dl_client,
            snapshot={"dbid_7": 7},
        )

        await check_completed_tasks(context)

        context.bot.send_message.assert_not_awaited()

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
