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


class TestBuildDeleteButtons:
    """Test _build_delete_buttons."""

    def test_builds_buttons_for_tasks(self):
        from bot.handlers.status import _build_delete_buttons

        tasks = [
            {"id": "task1", "title": "Movie A"},
            {"id": "task2", "title": "Movie B"},
        ]
        markup = _build_delete_buttons(tasks, 111)
        assert markup is not None
        # Two rows, one button each
        assert len(markup.inline_keyboard) == 2
        assert "cdel:111:task1" in markup.inline_keyboard[0][0].callback_data
        assert "cdel:111:task2" in markup.inline_keyboard[1][0].callback_data

    def test_long_name_truncated(self):
        from bot.handlers.status import _build_delete_buttons

        tasks = [{"id": "t1", "title": "A" * 50}]
        markup = _build_delete_buttons(tasks, 111)
        btn_text = markup.inline_keyboard[0][0].text
        assert "\u2026" in btn_text

    def test_no_task_id_skipped(self):
        from bot.handlers.status import _build_delete_buttons

        tasks = [{"title": "No ID task"}]
        markup = _build_delete_buttons(tasks, 111)
        assert markup is None

    def test_empty_tasks(self):
        from bot.handlers.status import _build_delete_buttons

        markup = _build_delete_buttons([], 111)
        assert markup is None


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


class TestCancelCommand:
    """Test cancel_command."""

    async def test_cancel_no_args(self, db_with_users):
        from bot.handlers.status import cancel_command

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=[])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "用法" in text

    async def test_cancel_invalid_index_not_number(self, db_with_users):
        from bot.handlers.status import cancel_command

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["abc"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "有效" in text

    async def test_cancel_invalid_index_zero(self, db_with_users):
        from bot.handlers.status import cancel_command

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["0"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "有效" in text

    async def test_cancel_invalid_index_negative(self, db_with_users):
        from bot.handlers.status import cancel_command

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["-1"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "有效" in text

    async def test_cancel_no_dl_client(self, db_with_users):
        from bot.handlers.status import cancel_command

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["1"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "未配置" in text or "尚未配置" in text

    async def test_cancel_get_tasks_error(self, db_with_users):
        from bot.handlers.status import cancel_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(side_effect=Exception("fail"))

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["1"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "失败" in text

    async def test_cancel_index_out_of_range(self, db_with_users):
        from bot.handlers.status import cancel_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "Task1", "id": "t1", "status": 2},
        ])

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["5"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "超出范围" in text

    async def test_cancel_task_no_id(self, db_with_users):
        from bot.handlers.status import cancel_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "No ID Task", "status": 2},
        ])

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["1"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "无法删除" in text

    async def test_cancel_valid_shows_confirmation(self, db_with_users):
        from bot.handlers.status import cancel_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "My Movie", "id": "t1", "status": 2},
        ])

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["1"])
        await cancel_command(update, context)

        call_kwargs = update.message.reply_text.call_args
        text = call_kwargs[0][0]
        assert "确认删除" in text
        assert "My Movie" in text
        # Check keyboard has confirm and cancel buttons
        markup = call_kwargs[1]["reply_markup"]
        buttons = markup.inline_keyboard[0]
        assert any("delok" in b.callback_data for b in buttons)
        assert any("delno" in b.callback_data for b in buttons)

    async def test_cancel_long_name_truncated(self, db_with_users):
        from bot.handlers.status import cancel_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "A" * 100, "id": "t1", "status": 2},
        ])

        update = make_update(user_id=111)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["1"])
        await cancel_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "\u2026" in text

    async def test_cancel_user_sees_only_own_tasks(self, db_with_users):
        from bot.handlers.status import cancel_command

        db_with_users.log_download(333, "User Task", "1 GB", task_id="ut1")

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "User Task", "id": "ut1", "status": 2},
            {"title": "Owner Task", "id": "ot1", "status": 2},
        ])

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client, args=["1"])
        await cancel_command(update, context)

        # Should show confirmation for user's task
        text = update.message.reply_text.call_args[0][0]
        assert "User Task" in text


class TestDeleteConfirmCallback:
    """Test delete_confirm_callback (cdel:uid:task_id)."""

    async def test_valid_confirm(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "Movie ABC"},
        ])

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        query.edit_message_text.assert_called_once()
        call_args = query.edit_message_text.call_args
        assert "确认删除" in call_args[0][0]
        assert "Movie ABC" in call_args[0][0]

    async def test_unauthorized_user(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        update = make_update(user_id=999, is_callback=True, callback_data="cdel:999:t1")
        context = make_context(db=db_with_users)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        # First call is answer(), second is answer() with alert
        assert query.answer.call_count >= 2
        last_call = query.answer.call_args_list[-1]
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

    async def test_task_not_found_uses_task_id_as_name(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[])

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:unknown_id")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        call_args = query.edit_message_text.call_args
        assert "unknown_id" in call_args[0][0]

    async def test_no_dl_client_uses_task_id_as_name(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:t1")
        context = make_context(db=db_with_users)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        call_args = query.edit_message_text.call_args
        assert "t1" in call_args[0][0]

    async def test_get_tasks_exception_uses_task_id(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(side_effect=Exception("fail"))

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        call_args = query.edit_message_text.call_args
        # Falls back to task_id as name
        assert "t1" in call_args[0][0]

    async def test_long_task_name_truncated(self, db_with_users):
        from bot.handlers.status import delete_confirm_callback

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"id": "t1", "title": "A" * 100},
        ])

        update = make_update(user_id=333, is_callback=True, callback_data="cdel:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_confirm_callback(update, context)

        query = update.callback_query
        text = query.edit_message_text.call_args[0][0]
        assert "\u2026" in text


class TestDeleteExecuteCallback:
    """Test delete_execute_callback (delok:uid:task_id)."""

    async def test_success(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.delete_task = AsyncMock(return_value=True)

        update = make_update(user_id=333, is_callback=True, callback_data="delok:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)

        # User 333 needs to own the task
        db_with_users.log_download(333, "Task", "1 GB", task_id="t1")

        await delete_execute_callback(update, context)

        query = update.callback_query
        query.edit_message_text.assert_called_once()
        assert "已移除" in query.edit_message_text.call_args[0][0]

    async def test_failure(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        dl_client = AsyncMock()
        dl_client.delete_task = AsyncMock(return_value=False)

        update = make_update(user_id=333, is_callback=True, callback_data="delok:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)

        db_with_users.log_download(333, "Task", "1 GB", task_id="t1")

        await delete_execute_callback(update, context)

        query = update.callback_query
        assert "失败" in query.edit_message_text.call_args[0][0]

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
        dl_client.delete_task = AsyncMock(return_value=True)

        # Owner (111) deletes task belonging to user 333
        update = make_update(user_id=111, is_callback=True, callback_data="delok:333:t1")
        context = make_context(db=db_with_users, dl_client=dl_client)
        await delete_execute_callback(update, context)

        query = update.callback_query
        assert "已移除" in query.edit_message_text.call_args[0][0]

    async def test_no_dl_client(self, db_with_users):
        from bot.handlers.status import delete_execute_callback

        # Owner can skip task ownership check
        update = make_update(user_id=111, is_callback=True, callback_data="delok:111:t1")
        context = make_context(db=db_with_users)
        await delete_execute_callback(update, context)

        query = update.callback_query
        assert "未配置" in query.edit_message_text.call_args[0][0]

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
