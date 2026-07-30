"""High-value tests for the DS7-only second phase of /status."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bot.clients.download_station import (
    DS7FileManifest,
    DS7TaskPage,
    DownloadStationClient,
)
from bot.handlers.status import (
    _control_task,
    _format_task_info,
    _task_panel_keyboard,
    delete_cancel_callback,
    delete_confirm_callback,
    delete_execute_callback,
    ds_status_action_callback,
    status_command,
)
from bot.handlers.status_tokens import (
    STATUS_TOKEN_REGISTRY_KEY,
    StatusTokenEntry,
    StatusTokenRegistry,
)
from tests.conftest import make_context, make_update


class MutableClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


class DS7Stub(DownloadStationClient):
    """Method-level stub that still selects the DS7-only handler path."""

    def __init__(self):
        self.is_ds7 = AsyncMock(return_value=True)
        self.get_tasks = AsyncMock(
            side_effect=AssertionError("DS7 status must not scan the full task list")
        )
        self.get_tasks_page = AsyncMock(
            return_value=DS7TaskPage(tasks=(), total=0, offset=0)
        )
        self.get_tasks_by_ids = AsyncMock(return_value=[])
        self.get_task_statistics = AsyncMock(
            return_value={"download_rate": 0, "upload_rate": 0}
        )
        self.get_task = AsyncMock(return_value=None)
        self.pause_task = AsyncMock(return_value=True)
        self.resume_task = AsyncMock(return_value=True)
        self.wait_for_task_status = AsyncMock(
            return_value=_task(status=3),
        )
        self.prepare_file_manifest = AsyncMock()
        self.delete_file_manifest = AsyncMock(return_value=True)
        self.delete_task = AsyncMock(return_value=True)


def _task(
    task_id: str = "task-1",
    *,
    status: int = 2,
    task_type: str = "bt",
    title: str = "Example",
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "type": task_type,
        "status": status,
        "size": 1024,
        "additional": {
            "detail": {"destination": "/downloads"},
            "transfer": {
                "size_downloaded": 512,
                "size_uploaded": 128,
                "speed_download": 64,
                "speed_upload": 16,
            },
        },
    }


def _entry(task_id: str = "task-1") -> StatusTokenEntry:
    return StatusTokenEntry(
        viewer_id=333,
        task_id=task_id,
        binding_id=1,
        mode="mine",
        view="active",
        page=0,
        action="task",
        expires_at=999999,
    )


def _create_token(
    registry: StatusTokenRegistry,
    *,
    viewer_id: int = 333,
    task_id: str = "task-1",
    binding_id: int | None = 1,
    action: str = "task",
    metadata: dict | None = None,
) -> str:
    return registry.create(
        viewer_id=viewer_id,
        task_id=task_id,
        binding_id=binding_id,
        mode="mine",
        view="active",
        page=0,
        action=action,
        metadata=metadata,
    )


def _context(db, client: DS7Stub, registry: StatusTokenRegistry | None = None):
    context = make_context(db=db, dl_client=client)
    if registry is not None:
        context.bot_data[STATUS_TOKEN_REGISTRY_KEY] = registry
    return context


class TestDS7NativeLoading:
    async def test_owner_status_uses_native_pages_without_full_scan(self, db_with_users):
        client = DS7Stub()

        async def get_page(offset, limit, *, status_inverse, **kwargs):
            if status_inverse:
                return DS7TaskPage(
                    tasks=(_task("active-1", title="Native Active"),),
                    total=11,
                    offset=offset,
                )
            return DS7TaskPage(tasks=(), total=3, offset=offset)

        client.get_tasks_page.side_effect = get_page
        client.get_task_statistics.return_value = {
            "download_rate": 2048,
            "upload_rate": 1024,
        }
        update = make_update(user_id=111)
        context = _context(db_with_users, client)

        await status_command(update, context)

        client.get_tasks.assert_not_awaited()
        client.get_tasks_by_ids.assert_not_awaited()
        calls = {
            (call.args[0], call.args[1], call.kwargs["status_inverse"])
            for call in client.get_tasks_page.await_args_list
        }
        assert calls == {(0, 8, True), (0, 1, False)}
        assert all(call.kwargs["statuses"] == (5, 7, 8)
                   for call in client.get_tasks_page.await_args_list)
        text = update.message.reply_text.call_args.args[0]
        assert "Native Active" in text
        assert "进行中 11 | 完成/做种 3" in text
        assert "2.0KB/s" in text

    async def test_mine_fetches_only_bound_task_ids(self, db_with_users):
        db_with_users.log_download(333, "Mine A", "1 GB", task_id="mine-a")
        db_with_users.log_download(333, "Mine B", "2 GB", task_id="mine-b")
        client = DS7Stub()
        client.get_tasks_by_ids.return_value = [
            _task("mine-a", title="Mine A"),
            _task("mine-b", title="Mine B"),
        ]
        update = make_update(user_id=333)
        context = _context(db_with_users, client)

        await status_command(update, context)

        client.get_tasks_by_ids.assert_awaited_once_with(
            ["mine-a", "mine-b"], force_refresh=False
        )
        client.get_tasks_page.assert_not_awaited()
        client.get_tasks.assert_not_awaited()
        text = update.message.reply_text.call_args.args[0]
        assert "Mine A" in text
        assert "Mine B" in text

    async def test_mine_refilters_bindings_after_targeted_fetch(self, db_with_users):
        db_with_users.log_download(333, "Old", "1 GB", task_id="task-1")
        client = DS7Stub()

        async def fetch_then_rebind(*args, **kwargs):
            db_with_users.log_download(111, "New", "1 GB", task_id="task-1")
            return [_task(title="New Owner Task")]

        client.get_tasks_by_ids.side_effect = fetch_then_rebind
        update = make_update(user_id=333)
        context = _context(db_with_users, client)

        await status_command(update, context)

        text = update.message.reply_text.call_args.args[0]
        assert "New Owner Task" not in text
        assert "进行中 0" in text
        assert db_with_users.user_owns_active_task(111, "task-1")

    async def test_dsm6_download_station_falls_back_to_legacy_list(self, db_with_users):
        client = DS7Stub()
        client.is_ds7.return_value = False
        client.get_tasks.side_effect = None
        client.get_tasks.return_value = [
            _task("legacy-task", title="DSM6 Legacy Task"),
        ]
        update = make_update(user_id=111)
        context = _context(db_with_users, client)

        await status_command(update, context)

        client.is_ds7.assert_awaited_once_with()
        client.get_tasks.assert_awaited_once_with()
        client.get_tasks_page.assert_not_awaited()
        assert "DSM6 Legacy Task" in update.message.reply_text.call_args.args[0]


class TestDS7DetailsAndControls:
    def test_details_never_expose_uri_or_extract_password(self):
        task = _task(title="Private Download")
        task["additional"]["detail"].update({
            "uri": "https://secret.example/token-value",
            "extract_password": "super-secret-password",
        })

        text = _format_task_info(task)

        assert "Private Download" in text
        assert "/downloads" in text
        assert "secret.example" not in text
        assert "token-value" not in text
        assert "super-secret-password" not in text
        assert "uri" not in text.lower()
        assert "password" not in text.lower()

    @pytest.mark.parametrize(
        ("status", "task_type", "expected_operations"),
        [
            (1, "bt", {"pause", "remove", "back"}),
            (2, "bt", {"pause", "remove", "back"}),
            (6, "bt", {"pause", "remove", "back"}),
            (8, "bt", {"pause", "remove", "back"}),
            (9, "http", {"pause", "remove", "back"}),
            (3, "bt", {"resume", "remove", "back"}),
            (105, "bt", {"resume", "remove", "back"}),
            (5, "bt", {"resume", "remove", "back"}),
            (5, "http", {"remove", "back"}),
            (4, "bt", {"remove", "back"}),
            (7, "bt", {"remove", "back"}),
            (10, "bt", {"remove", "back"}),
            (15, "bt", {"remove", "back"}),
        ],
    )
    def test_action_buttons_follow_ds7_status_matrix(
        self, status, task_type, expected_operations
    ):
        markup = _task_panel_keyboard(
            _task(status=status, task_type=task_type), "short-token"
        )

        operations = {
            button.callback_data.split(":", 2)[1]
            for row in markup.inline_keyboard
            for button in row
        }
        assert operations == expected_operations

    @pytest.mark.parametrize(
        ("operation", "current_status", "expected_method"),
        [
            ("pause", 2, "pause_task"),
            ("pause", 4, None),
            ("resume", 3, "resume_task"),
            ("resume", 2, None),
        ],
    )
    async def test_control_refetches_state_before_mutation(
        self, db_with_users, operation, current_status, expected_method
    ):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        current = _task(status=current_status)
        client = DS7Stub()
        client.get_task.return_value = current
        client.get_tasks_by_ids.return_value = [current]
        context = _context(db_with_users, client)
        query = make_update(user_id=333, is_callback=True).callback_query

        await _control_task(context, query, _entry(), operation)

        client.get_task.assert_awaited_once_with("task-1", force_refresh=True)
        if expected_method == "pause_task":
            client.pause_task.assert_awaited_once_with("task-1")
            client.resume_task.assert_not_awaited()
        elif expected_method == "resume_task":
            client.resume_task.assert_awaited_once_with("task-1")
            client.pause_task.assert_not_awaited()
        else:
            client.pause_task.assert_not_awaited()
            client.resume_task.assert_not_awaited()


class TestDS7TokenValidation:
    async def test_open_refetches_and_renders_sanitized_details(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(registry)
        client = DS7Stub()
        task = _task()
        task["additional"]["detail"].update({
            "uri": "https://secret.example/download",
            "extract_password": "do-not-show",
        })
        client.get_task.return_value = task
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        client.get_task.assert_awaited_once_with("task-1", force_refresh=True)
        text = update.callback_query.edit_message_text.call_args.args[0]
        assert "Example" in text
        assert "secret.example" not in text
        assert "do-not-show" not in text

    async def test_token_cannot_be_used_by_another_viewer(self, db_with_users):
        registry = StatusTokenRegistry()
        token = _create_token(registry, viewer_id=333)
        client = DS7Stub()
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=111,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert "不是你的" in update.callback_query.answer.call_args.args[0]
        client.get_task.assert_not_awaited()

    async def test_token_does_not_bypass_active_task_ownership(self, db_with_users):
        registry = StatusTokenRegistry()
        token = _create_token(registry, viewer_id=333, task_id="not-mine")
        client = DS7Stub()
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert "无权限操作此任务" in update.callback_query.answer.call_args.args[0]
        client.get_task.assert_not_awaited()

    async def test_none_binding_is_only_valid_for_owner(self, db_with_users):
        registry = StatusTokenRegistry()
        token = _create_token(registry, binding_id=None)
        client = DS7Stub()
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert "无权限操作此任务" in update.callback_query.answer.call_args.args[0]
        client.is_ds7.assert_not_awaited()

    async def test_owner_none_binding_rejects_new_binding_after_refetch(self, db_with_users):
        registry = StatusTokenRegistry()
        token = _create_token(registry, viewer_id=111, binding_id=None)
        client = DS7Stub()

        async def refetch_then_bind(*args, **kwargs):
            db_with_users.log_download(333, "New", "1 GB", task_id="task-1")
            return _task(title="Newly Bound Task")

        client.get_task.side_effect = refetch_then_bind
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=111,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        text = update.callback_query.edit_message_text.call_args.args[0]
        assert "归属已变化" in text
        assert "Newly Bound Task" not in text
        assert db_with_users.user_owns_active_task(333, "task-1")

    async def test_binding_is_rechecked_after_ds7_capability_await(self, db_with_users):
        db_with_users.log_download(333, "Old", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(registry)
        client = DS7Stub()

        async def probe_then_rebind():
            db_with_users.log_download(111, "New", "1 GB", task_id="task-1")
            return True

        client.is_ds7.side_effect = probe_then_rebind
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert "归属" in update.callback_query.answer.call_args.args[0]
        client.get_task.assert_not_awaited()
        assert db_with_users.user_owns_active_task(111, "task-1")

    async def test_dst_callback_rejects_dsm6_client(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(registry)
        client = DS7Stub()
        client.is_ds7.return_value = False
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert "仅支持 DS7" in update.callback_query.answer.call_args.args[0]
        client.get_task.assert_not_awaited()

    async def test_expired_token_is_rejected_before_client_access(self, db_with_users):
        clock = MutableClock(10)
        registry = StatusTokenRegistry(ttl_seconds=5, clock=clock)
        token = _create_token(registry, viewer_id=333)
        clock.now = 15
        client = DS7Stub()
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert "已过期" in update.callback_query.answer.call_args.args[0]
        client.get_task.assert_not_awaited()

    async def test_keep_token_is_one_time_and_deactivates_binding(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(registry, action="keep")
        client = DS7Stub()
        client.get_task.return_value = _task(status=5)
        context = _context(db_with_users, client, registry)

        first = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:keep:{token}",
        )
        await ds_status_action_callback(first, context)

        client.delete_task.assert_awaited_once_with("task-1", delete_files=False)
        assert not db_with_users.user_owns_active_task(333, "task-1")
        assert "文件已保留" in first.callback_query.edit_message_text.call_args.args[0]

        second = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:keep:{token}",
        )
        await ds_status_action_callback(second, context)

        assert "已过期" in second.callback_query.answer.call_args.args[0]
        assert client.delete_task.await_count == 1


class TestDS7PurgeSafety:
    async def test_keep_rechecks_ownership_after_refetch(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(registry, action="keep")
        client = DS7Stub()

        async def refetch_then_reassign(*args, **kwargs):
            db_with_users.log_download(111, "Reassigned", "1 GB", task_id="task-1")
            return _task(status=5)

        client.get_task.side_effect = refetch_then_reassign
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:keep:{token}",
        )

        await ds_status_action_callback(update, context)

        client.delete_task.assert_not_awaited()
        assert db_with_users.user_owns_active_task(111, "task-1")
        assert "归属已变化" in update.callback_query.edit_message_text.call_args.args[0]

    async def test_missing_refetch_never_deactivates_new_binding(self, db_with_users):
        db_with_users.log_download(333, "Old", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(registry)
        client = DS7Stub()

        async def refetch_then_reassign(*args, **kwargs):
            db_with_users.log_download(111, "New", "1 GB", task_id="task-1")
            return None

        client.get_task.side_effect = refetch_then_reassign
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:open:{token}",
        )

        await ds_status_action_callback(update, context)

        assert db_with_users.user_owns_active_task(111, "task-1")
        assert "归属已变化" in update.callback_query.edit_message_text.call_args.args[0]

    async def test_purge_rechecks_binding_after_pause_wait(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(
            registry,
            action="purge",
            metadata={"fingerprint": "current-fingerprint"},
        )
        client = DS7Stub()
        task = _task(status=8)
        manifest = DS7FileManifest(
            task_id="task-1",
            destination="/downloads",
            paths=("/downloads/movie.mkv",),
            total_size=1024,
            fingerprint="current-fingerprint",
        )
        client.get_task.return_value = task
        client.get_tasks_by_ids.return_value = [task]
        client.prepare_file_manifest.return_value = manifest

        async def wait_then_reassign(*args, **kwargs):
            db_with_users.log_download(111, "New", "1 GB", task_id="task-1")
            return _task(status=3)

        client.wait_for_task_status.side_effect = wait_then_reassign
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:purge:{token}",
        )

        await ds_status_action_callback(update, context)

        client.delete_file_manifest.assert_not_awaited()
        client.delete_task.assert_not_awaited()
        assert db_with_users.user_owns_active_task(111, "task-1")

    async def test_purge_rechecks_authorization_after_pause_wait(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(
            registry,
            action="purge",
            metadata={"fingerprint": "current-fingerprint"},
        )
        client = DS7Stub()
        task = _task(status=8)
        manifest = DS7FileManifest(
            task_id="task-1",
            destination="/downloads",
            paths=("/downloads/movie.mkv",),
            total_size=1024,
            fingerprint="current-fingerprint",
        )
        client.get_task.return_value = task
        client.prepare_file_manifest.return_value = manifest

        async def wait_then_ban(*args, **kwargs):
            db_with_users.ban_user(333)
            return _task(status=3)

        client.wait_for_task_status.side_effect = wait_then_ban
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:purge:{token}",
        )

        await ds_status_action_callback(update, context)

        client.delete_file_manifest.assert_not_awaited()
        client.delete_task.assert_not_awaited()
        text = update.callback_query.edit_message_text.call_args.args[0]
        assert "权限已撤销" in text

    async def test_seeding_purge_waits_for_confirmed_pause(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(
            registry,
            action="purge",
            metadata={"fingerprint": "current-fingerprint"},
        )
        client = DS7Stub()
        task = _task(status=8, task_type="bt")
        manifest = DS7FileManifest(
            task_id="task-1",
            destination="/downloads",
            paths=("/downloads/movie.mkv",),
            total_size=1024,
            fingerprint="current-fingerprint",
        )
        client.get_task.return_value = task
        client.get_tasks_by_ids.return_value = [task]
        client.prepare_file_manifest.return_value = manifest
        client.wait_for_task_status.return_value = None
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:purge:{token}",
        )

        await ds_status_action_callback(update, context)

        client.pause_task.assert_awaited_once_with("task-1")
        client.wait_for_task_status.assert_awaited_once_with("task-1", (3,))
        client.delete_file_manifest.assert_not_awaited()
        client.delete_task.assert_not_awaited()
        assert db_with_users.user_owns_active_task(333, "task-1")

    @pytest.mark.parametrize(
        ("confirmed_fingerprint", "delete_files_ok", "delete_task_ok", "deactivated"),
        [
            ("stale-fingerprint", True, True, False),
            ("current-fingerprint", False, True, False),
            ("current-fingerprint", True, False, False),
            ("current-fingerprint", True, True, True),
        ],
    )
    async def test_purge_revalidates_manifest_and_only_deactivates_full_success(
        self,
        db_with_users,
        confirmed_fingerprint,
        delete_files_ok,
        delete_task_ok,
        deactivated,
    ):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        registry = StatusTokenRegistry()
        token = _create_token(
            registry,
            action="purge",
            metadata={"fingerprint": confirmed_fingerprint},
        )
        client = DS7Stub()
        task = _task(status=5, task_type="bt")
        manifest = DS7FileManifest(
            task_id="task-1",
            destination="/downloads",
            paths=("/downloads/movie.mkv",),
            total_size=1024,
            fingerprint="current-fingerprint",
        )
        client.get_task.return_value = task
        client.get_tasks_by_ids.return_value = [task]
        client.prepare_file_manifest.return_value = manifest
        client.delete_file_manifest.return_value = delete_files_ok
        client.delete_task.return_value = delete_task_ok
        context = _context(db_with_users, client, registry)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data=f"dst:purge:{token}",
        )

        await ds_status_action_callback(update, context)

        client.get_task.assert_awaited_once_with("task-1", force_refresh=True)
        client.prepare_file_manifest.assert_awaited_once_with(task)
        fingerprint_matches = confirmed_fingerprint == manifest.fingerprint
        if not fingerprint_matches:
            client.delete_file_manifest.assert_not_awaited()
            client.delete_task.assert_not_awaited()
        elif not delete_files_ok:
            client.delete_file_manifest.assert_awaited_once_with(manifest)
            client.delete_task.assert_not_awaited()
        else:
            client.delete_file_manifest.assert_awaited_once_with(manifest)
            client.delete_task.assert_awaited_once_with(
                "task-1", delete_files=False
            )
        assert (
            not db_with_users.user_owns_active_task(333, "task-1")
        ) is deactivated


class TestLegacyDownloadStationCallbacks:
    @pytest.mark.parametrize(
        ("callback", "data"),
        [
            (delete_confirm_callback, "cdel:333:task-1:cancel"),
            (delete_execute_callback, "delok:333:task-1:cancel"),
            (delete_cancel_callback, "delno:333"),
        ],
    )
    async def test_ds7_old_callbacks_only_redirect_to_status(
        self, db_with_users, callback, data
    ):
        client = DS7Stub()
        context = _context(db_with_users, client)
        update = make_update(user_id=333, is_callback=True, callback_data=data)

        await callback(update, context)

        text = update.callback_query.edit_message_text.call_args.args[0]
        assert "/status" in text
        client.get_task.assert_not_awaited()
        client.get_tasks.assert_not_awaited()
        client.delete_task.assert_not_awaited()

    @pytest.mark.parametrize(
        ("callback", "data"),
        [
            (delete_confirm_callback, "cdel:333:task-1:cancel"),
            (delete_execute_callback, "delok:333:task-1:cancel"),
            (delete_cancel_callback, "delno:333"),
        ],
    )
    async def test_old_callbacks_fail_closed_when_ds_version_probe_fails(
        self, db_with_users, callback, data
    ):
        client = DS7Stub()
        client.is_ds7.side_effect = ConnectionError("offline")
        context = _context(db_with_users, client)
        update = make_update(user_id=333, is_callback=True, callback_data=data)

        await callback(update, context)

        assert "稍后重试" in update.callback_query.answer.call_args.args[0]
        client.get_task.assert_not_awaited()
        client.get_tasks.assert_not_awaited()
        client.delete_task.assert_not_awaited()

    async def test_dsm6_old_confirm_keeps_legacy_flow(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        client = DS7Stub()
        client.is_ds7.return_value = False
        client.get_tasks.side_effect = None
        client.get_tasks.return_value = [_task()]
        context = _context(db_with_users, client)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data="cdel:333:task-1:cancel",
        )

        await delete_confirm_callback(update, context)

        text = update.callback_query.edit_message_text.call_args.args[0]
        assert "确认取消下载任务" in text
        assert "delok:333:task-1:cancel" == (
            update.callback_query.edit_message_text.call_args.kwargs[
                "reply_markup"
            ].inline_keyboard[0][0].callback_data
        )

    async def test_dsm6_old_execute_keeps_legacy_flow(self, db_with_users):
        db_with_users.log_download(333, "Task", "1 GB", task_id="task-1")
        client = DS7Stub()
        client.is_ds7.return_value = False
        client.get_tasks.side_effect = None
        client.get_tasks.return_value = [_task()]
        context = _context(db_with_users, client)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data="delok:333:task-1:cancel",
        )

        await delete_execute_callback(update, context)

        client.delete_task.assert_awaited_once_with(
            "task-1", delete_files=False
        )
        assert not db_with_users.user_owns_active_task(333, "task-1")

    async def test_dsm6_old_cancel_keeps_legacy_flow(self, db_with_users):
        client = DS7Stub()
        client.is_ds7.return_value = False
        context = _context(db_with_users, client)
        update = make_update(
            user_id=333,
            is_callback=True,
            callback_data="delno:333",
        )

        await delete_cancel_callback(update, context)

        update.callback_query.edit_message_text.assert_awaited_once_with("已取消。")
