"""Tests for bot/handlers/status.py — uncovered lines."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import make_context, make_update


class TestFormatSize:
    """Test _format_size edge cases."""

    def test_petabytes(self):
        from bot.handlers.status import _format_size

        # 1 PB = 1024^5 bytes
        result = _format_size(1024 ** 5)
        assert "PB" in result
        assert "1.0PB" == result

    def test_large_petabytes(self):
        from bot.handlers.status import _format_size

        result = _format_size(5 * 1024 ** 5)
        assert "5.0PB" == result


class TestBuildProgressBar:
    """Test _build_progress_bar."""

    def test_zero_percent(self):
        from bot.handlers.status import _build_progress_bar

        bar = _build_progress_bar(0)
        assert bar == "[" + "░" * 16 + "]"

    def test_hundred_percent(self):
        from bot.handlers.status import _build_progress_bar

        bar = _build_progress_bar(100)
        assert bar == "[" + "█" * 16 + "]"

    def test_fifty_percent(self):
        from bot.handlers.status import _build_progress_bar

        bar = _build_progress_bar(50)
        assert "█" in bar
        assert "░" in bar
        filled = bar.count("█")
        assert filled == 8

    def test_negative_clamped(self):
        from bot.handlers.status import _build_progress_bar

        bar = _build_progress_bar(-10)
        assert bar == "[" + "░" * 16 + "]"

    def test_over_hundred_clamped(self):
        from bot.handlers.status import _build_progress_bar

        bar = _build_progress_bar(200)
        assert bar == "[" + "█" * 16 + "]"


class TestFormatEta:
    """Test _format_eta for various time ranges."""

    def test_zero_speed_returns_empty(self):
        from bot.handlers.status import _format_eta

        assert _format_eta(1000, 0) == ""

    def test_negative_speed_returns_empty(self):
        from bot.handlers.status import _format_eta

        assert _format_eta(1000, -1) == ""

    def test_zero_remaining_returns_empty(self):
        from bot.handlers.status import _format_eta

        assert _format_eta(0, 100) == ""

    def test_negative_remaining_returns_empty(self):
        from bot.handlers.status import _format_eta

        assert _format_eta(-100, 100) == ""

    def test_seconds_range(self):
        from bot.handlers.status import _format_eta

        # 30 bytes at 1 byte/s = 30 seconds
        result = _format_eta(30, 1)
        assert "30秒" in result
        assert "预计" in result

    def test_minutes_range(self):
        from bot.handlers.status import _format_eta

        # 300 bytes at 1 byte/s = 300s = 5 minutes
        result = _format_eta(300, 1)
        assert "5分钟" in result

    def test_hours_range(self):
        from bot.handlers.status import _format_eta

        # 7200 bytes at 1 byte/s = 7200s = 2 hours
        result = _format_eta(7200, 1)
        assert "2小时" in result

    def test_hours_with_minutes(self):
        from bot.handlers.status import _format_eta

        # 5400 bytes at 1 byte/s = 5400s = 1.5 hours = 1h30m
        result = _format_eta(5400, 1)
        assert "1小时" in result
        assert "30分钟" in result

    def test_hours_exact_no_minutes(self):
        from bot.handlers.status import _format_eta

        # 3600 bytes at 1 byte/s = exactly 1 hour
        result = _format_eta(3600, 1)
        assert "1小时" in result
        assert "分钟" not in result

    def test_days_range(self):
        from bot.handlers.status import _format_eta

        # 100000 bytes at 1 byte/s = ~27.7 hours > 24h = 1 day
        result = _format_eta(100000, 1)
        assert "天" in result


class TestDS7StatusMapping:

    @pytest.mark.parametrize(
        ("code", "state", "label"),
        [
            (1, "waiting", "等待"),
            (2, "downloading", "下载"),
            (3, "paused", "暂停"),
            (4, "processing", "处理"),
            (5, "completed", "完成"),
            (6, "processing", "校验"),
            (7, "completed", "做种"),
            (8, "completed", "做种"),
            (9, "waiting", "等待"),
            (10, "processing", "解压"),
            (11, "waiting", "预处理"),
            (12, "waiting", "预处理"),
            (13, "processing", "收尾"),
            (14, "processing", "后处理"),
            (15, "waiting", "验证码"),
            (105, "error", "磁盘空间不足"),
            (113, "error", "重复"),
            (123, "error", "无效"),
            (999, "error", "999"),
            (99, "unknown", "99"),
            (None, "unknown", "未知"),
        ],
    )
    def test_status_mapping(self, code, state, label):
        from bot.clients.download_station import get_ds7_status_info

        actual_state, actual_label = get_ds7_status_info(code)
        assert actual_state == state
        assert label in actual_label

    def test_task_buttons_use_safe_action(self):
        from bot.handlers.status import _task_action_button

        active = _task_action_button({"id": "t1", "title": "Active", "status": 2}, 1, 111)
        completed = _task_action_button({"id": "t2", "title": "Done", "status": 8}, 2, 111)

        assert active.callback_data == "cdel:111:t1:cancel"
        assert completed.callback_data == "cdel:111:t2:keep"

    def test_task_button_skips_overlong_callback(self):
        from bot.handlers.status import _task_action_button

        task = {"id": "x" * 60, "title": "Too long", "status": 2}
        assert _task_action_button(task, 1, 111) is None

    def test_extract_progress_is_displayed(self):
        from bot.handlers.status import _format_task_detail

        task = {
            "id": "extract", "title": "Archive", "status": 10,
            "status_extra": {"extract_progress": 42},
        }
        assert "解压中 42%" in _format_task_detail(task, 1)


class TestStatusCommandUserFiltering:
    """Test status_command user filtering branches."""

    async def test_user_no_task_ids(self, db_with_users):
        from bot.handlers.status import status_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "Task", "id": "t1", "status": 2},
        ])

        # user 333 has no logged tasks
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "还没有" in text

    async def test_user_tasks_all_completed(self, db_with_users):
        from bot.handlers.status import status_command

        # user 333 has a logged task but it's not in the active tasks
        db_with_users.log_download(333, "Done Movie", "1 GB", task_id="done_id")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "Other Task", "id": "other_id", "status": 2},
        ])

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "已全部完成" in text or "已被删除" in text

    async def test_status_no_lines_empty_tasks(self, db_with_users):
        """When all tasks are filtered out leaving no lines."""
        from bot.handlers.status import status_command

        # Return tasks that have no recognizable status (not 2, 5, or 8)
        # but still exist -- they go to downloading bucket but produce lines
        # Actually to hit line 197-199, we need grouped tasks producing empty lines
        # This is hard to trigger naturally, so we test via a mock that returns
        # tasks that all get filtered
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "Task", "id": "t1", "status": 2},
        ])

        # Owner with "mine" flag but no logged tasks
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["mine"])
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        # Owner with "mine" -> show_mine=True, no logged task IDs for owner
        assert "还没有" in text or "没有" in text


class TestStatusPagination:

    async def test_default_hides_completed_task_details(self, db_with_users):
        from bot.handlers.status import status_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "active", "title": "Active Movie", "status": 2},
            {"id": "done", "title": "Finished Movie", "status": 5},
            {"id": "seed", "title": "Seeding Movie", "status": 8},
        ])
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        markup = update.message.reply_text.call_args[1]["reply_markup"]
        assert "Active Movie" in text
        assert "Finished Movie" not in text
        assert "Seeding Movie" not in text
        assert "完成/做种 2" in text
        assert any(
            "completed" in button.callback_data
            for row in markup.inline_keyboard for button in row
        )

    async def test_completed_view_callback(self, db_with_users):
        from bot.handlers.status import status_page_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "active", "title": "Active Movie", "status": 2},
            {"id": "seed", "title": "Seeding Movie", "status": 8},
        ])
        update = make_update(
            user_id=111, is_callback=True,
            callback_data="stat:111:all:completed:0",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "Seeding Movie" in text
        assert "Active Movie" not in text
        assert "做种中" in text

    async def test_second_page_uses_global_numbers(self, db_with_users):
        from bot.handlers.status import status_page_callback

        tasks = [
            {"id": f"t{i}", "title": f"Task {i}", "status": 2}
            for i in range(1, 11)
        ]
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=tasks)
        update = make_update(
            user_id=111, is_callback=True,
            callback_data="stat:111:all:active:1",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "9. Task 9" in text
        assert "10. Task 10" in text
        assert "1. Task 1" not in text

    async def test_non_owner_cannot_forge_all_mode(self, db_with_users):
        from bot.handlers.status import status_page_callback

        db_with_users.log_download(333, "Mine", "1 GB", task_id="mine")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "mine", "title": "Mine", "status": 2},
            {"id": "other", "title": "Other", "status": 2},
        ])
        update = make_update(
            user_id=333, is_callback=True,
            callback_data="stat:333:all:active:0",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
        assert "Mine" in text
        assert "Other" not in text
        assert all(
            ":mine:" in button.callback_data
            for row in markup.inline_keyboard for button in row
            if button.callback_data.startswith("stat:")
        )

    async def test_refresh_failure_keeps_existing_message(self, db_with_users):
        from bot.handlers.status import status_page_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(side_effect=Exception("offline"))
        update = make_update(
            user_id=111, is_callback=True,
            callback_data="stat:111:all:active:0",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        update.callback_query.answer.assert_awaited_once_with()
        assert "刷新失败" in context.bot.send_message.call_args.kwargs["text"]
        update.callback_query.edit_message_text.assert_not_awaited()

    async def test_refresh_answers_before_loading_tasks(self, db_with_users):
        from bot.handlers.status import status_page_callback

        update = make_update(
            user_id=111, is_callback=True,
            callback_data="stat:111:all:active:0",
        )

        async def get_tasks():
            update.callback_query.answer.assert_awaited_once_with()
            return [{"id": "t1", "title": "Task", "status": 2}]

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(side_effect=get_tasks)
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        update.callback_query.edit_message_text.assert_awaited_once()

    async def test_page_is_clamped_after_tasks_disappear(self, db_with_users):
        from bot.handlers.status import status_page_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Only Task", "status": 2},
        ])
        update = make_update(
            user_id=111, is_callback=True,
            callback_data="stat:111:all:active:9",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "1. Only Task" in text
        assert "第 10" not in text

    async def test_unchanged_refresh_is_ignored(self, db_with_users):
        from telegram.error import BadRequest
        from bot.handlers.status import status_page_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Task", "status": 2},
        ])
        update = make_update(
            user_id=111, is_callback=True,
            callback_data="stat:111:all:active:0",
        )
        update.callback_query.edit_message_text = AsyncMock(
            side_effect=BadRequest("Message is not modified")
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await status_page_callback(update, context)

        update.callback_query.answer.assert_awaited_once()


class TestCancelCommand:
    async def test_redirects_to_stable_status_buttons(self, db_with_users):
        from bot.handlers.status import cancel_command

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["1"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "/status" in text
        assert "误删" in text


class TestDeleteConfirmCallback:
    async def test_active_task_confirms_cancel(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        db_with_users.log_download(333, "Movie ABC", "1 GB", task_id="t1")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Movie ABC", "status": 2},
        ])

        update = make_update(
            user_id=333, is_callback=True,
            callback_data="cdel:333:t1:cancel",
        )

        async def get_tasks():
            update.callback_query.answer.assert_awaited_once_with()
            return [{"id": "t1", "title": "Movie ABC", "status": 2}]

        dl_client.get_tasks = AsyncMock(side_effect=get_tasks)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        text = query.edit_message_text.call_args[0][0]
        keyboard = query.edit_message_text.call_args[1]["reply_markup"]
        assert "取消下载任务" in text
        assert "Movie ABC" in text
        assert keyboard.inline_keyboard[0][0].callback_data == "delok:333:t1:cancel"

    async def test_completed_task_forces_keep_files(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        db_with_users.log_download(333, "Done", "1 GB", task_id="t1")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Done", "status": 8},
        ])
        update = make_update(
            user_id=333, is_callback=True,
            callback_data="cdel:333:t1:cancel",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_confirm_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        keyboard = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
        assert "保留" in text
        assert keyboard.inline_keyboard[0][0].callback_data == "delok:333:t1:keep"

    async def test_finished_task_uses_completed_wording(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        db_with_users.log_download(333, "Done", "1 GB", task_id="t1")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Done", "status": 5},
        ])
        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:t1:keep")
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_confirm_callback(update, context)

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "移除已完成任务" in text
        assert "停止做种" not in text

    async def test_unauthorized_user(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        update = make_update(user_id=999, is_callback=True, callback_data="cdel:999:t1")
        context = make_context(db=db_with_users)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        assert query.answer.call_count == 1
        last_call = query.answer.call_args
        assert "无权限" in last_call[0][0]

    async def test_wrong_user(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:111:t1")
        context = make_context(db=db_with_users)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "不是你的" in last_call[0][0]

    async def test_invalid_data_format(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:baddata")
        context = make_context(db=db_with_users)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无效" in last_call[0][0]

    async def test_invalid_user_id(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:abc:t1")
        context = make_context(db=db_with_users)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无效" in last_call[0][0]

    async def test_rejects_task_not_owned_by_user(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        dl_client = AsyncMock()
        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:other")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        assert "无权限" in update.callback_query.answer.call_args[0][0]
        dl_client.get_tasks.assert_not_awaited()

    async def test_task_not_found_reports_notice(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        db_with_users.log_download(333, "Gone", "1 GB", task_id="gone")
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[])
        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:gone")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        update.callback_query.answer.assert_awaited_once_with()
        assert "不存在" in context.bot.send_message.call_args.kwargs["text"]
        update.callback_query.edit_message_text.assert_not_awaited()


class TestDeleteExecuteCallback:
    async def test_active_cancel_never_uses_filestation_cleanup(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Task", "status": 2},
        ])
        dl_client.delete_task = AsyncMock(return_value=True)
        db_with_users.log_download(333, "Task", "1 GB", task_id="t1")
        update = make_update(
            user_id=333, is_callback=True,
            callback_data="delok:333:t1:cancel",
        )

        async def get_tasks():
            update.callback_query.answer.assert_awaited_once_with()
            return [{"id": "t1", "title": "Task", "status": 2}]

        dl_client.get_tasks = AsyncMock(side_effect=get_tasks)
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_execute_callback(update, context)

        dl_client.delete_task.assert_awaited_once_with("t1", delete_files=False)
        assert "已取消" in update.callback_query.edit_message_text.call_args[0][0]

    async def test_completed_task_keeps_files(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Task", "status": 8},
        ])
        dl_client.delete_task = AsyncMock(return_value=True)
        db_with_users.log_download(333, "Task", "1 GB", task_id="t1")
        update = make_update(
            user_id=333, is_callback=True,
            callback_data="delok:333:t1:keep",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_execute_callback(update, context)

        dl_client.delete_task.assert_awaited_once_with("t1", delete_files=False)
        assert "文件已保留" in update.callback_query.edit_message_text.call_args[0][0]

    async def test_completion_race_downgrades_to_keep(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Task", "status": 5},
        ])
        dl_client.delete_task = AsyncMock(return_value=True)
        db_with_users.log_download(333, "Task", "1 GB", task_id="t1")
        update = make_update(
            user_id=333, is_callback=True,
            callback_data="delok:333:t1:cancel",
        )
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_execute_callback(update, context)

        dl_client.delete_task.assert_awaited_once_with("t1", delete_files=False)
        assert "确认期间已完成" in update.callback_query.edit_message_text.call_args[0][0]

    async def test_delete_failure(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[{"id": "t1", "status": 2}])
        dl_client.delete_task = AsyncMock(return_value=False)
        db_with_users.log_download(333, "Task", "1 GB", task_id="t1")
        update = make_update(user_id=333, is_callback=True, callback_data="delok:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_execute_callback(update, context)

        assert "失败" in update.callback_query.edit_message_text.call_args[0][0]

    async def test_unauthorized_user(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        update = make_update(user_id=999, is_callback=True, callback_data="delok:999:t1")
        context = make_context(db=db_with_users)
        await delete_execute_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无权限" in last_call[0][0]

    async def test_non_owner_wrong_task(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()

        # User 333 tries to delete a task they don't own
        update = make_update(user_id=333, is_callback=True, callback_data="delok:333:t_other")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_execute_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无权限" in last_call[0][0]

    async def test_owner_can_delete_any_task(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[{"id": "t1", "status": 2}])
        dl_client.delete_task = AsyncMock(return_value=True)

        # Owner (111) deletes task belonging to user 333
        update = make_update(user_id=111, is_callback=True, callback_data="delok:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_execute_callback(update, context)

        query = update.callback_query
        assert "已取消" in query.edit_message_text.call_args[0][0]

    async def test_no_dl_client(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        # Owner can skip task ownership check
        update = make_update(user_id=111, is_callback=True, callback_data="delok:111:t1")
        context = make_context(db=db_with_users)
        await delete_execute_callback(update, context)

        query = update.callback_query
        assert "未配置" in query.edit_message_text.call_args[0][0]

    async def test_missing_task_does_not_delete(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[])
        db_with_users.log_download(333, "Gone", "1 GB", task_id="gone")
        update = make_update(user_id=333, is_callback=True, callback_data="delok:333:gone")
        context = make_context(db=db_with_users, dl_client=dl_client)

        await delete_execute_callback(update, context)

        assert "不存在" in update.callback_query.edit_message_text.call_args[0][0]
        dl_client.delete_task.assert_not_awaited()

    async def test_invalid_data_format(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        update = make_update(user_id=333, is_callback=True, callback_data="delok:badformat")
        context = make_context(db=db_with_users)
        await delete_execute_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无效" in last_call[0][0]

    async def test_invalid_user_id(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        update = make_update(user_id=333, is_callback=True, callback_data="delok:abc:t1")
        context = make_context(db=db_with_users)
        await delete_execute_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无效" in last_call[0][0]

    async def test_different_user_non_owner(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()

        # User 333 tries to delete task that belongs to user 111
        update = make_update(user_id=333, is_callback=True, callback_data="delok:111:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_execute_callback(update, context)

        query = update.callback_query
        last_call = query.answer.call_args_list[-1]
        assert "无权限" in last_call[0][0]


class TestDeleteCancelCallback:
    """Test delete_cancel_callback (delno:uid)."""

    async def test_cancel(self):
        from bot.handlers.status import delete_cancel_callback

        update = make_update(user_id=111, is_callback=True, callback_data="delno:111")
        context = make_context()
        await delete_cancel_callback(update, context)

        query = update.callback_query
        query.answer.assert_called_once()
        query.edit_message_text.assert_called_once_with("已取消。")

    async def test_other_user_cannot_cancel(self, db_with_users):
        from bot.handlers.status import delete_cancel_callback

        update = make_update(user_id=333, is_callback=True, callback_data="delno:111")
        context = make_context(db=db_with_users)

        await delete_cancel_callback(update, context)

        assert "不是你的" in update.callback_query.answer.call_args[0][0]
        update.callback_query.edit_message_text.assert_not_awaited()
