"""Comprehensive tests for handler and middleware modules."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.middleware import require_auth, require_owner
from bot.handlers.start import start_command, apply_command, approval_callback, help_command
from bot.handlers.admin import users_command, pending_command, ban_command, unban_command
from bot.handlers.search import search_command, more_command, _format_results, user_cache
from bot.handlers.download import download_command
from bot.pt.base import TorrentResult

from tests.conftest import make_update, make_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_torrent(n: int) -> TorrentResult:
    """Create a dummy TorrentResult with index in title."""
    return TorrentResult(
        title=f"Torrent.Title.{n}.2024.BluRay.1080p",
        torrent_url=f"https://example.com/dl/{n}",
        size=f"{n}.37 GB",
        seeders=n * 10,
        leechers=n,
    )


@pytest.fixture(autouse=True)
def _clear_user_cache():
    """Ensure user_cache is empty before and after each test."""
    user_cache.clear()
    yield
    user_cache.clear()


# ===========================================================================
# bot/middleware.py — require_auth
# ===========================================================================

class TestRequireAuth:

    async def test_authorized_user_called(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_auth(inner)
        update = make_update(user_id=333)  # approved user
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_awaited_once_with(update, context)

    async def test_authorized_owner_called(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_auth(inner)
        update = make_update(user_id=111)  # owner
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_awaited_once_with(update, context)

    async def test_pending_user_rejected(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_auth(inner)
        update = make_update(user_id=222)  # pending
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_not_awaited()
        reply_text = update.message.reply_text
        reply_text.assert_awaited_once()
        assert "等待" in reply_text.call_args[0][0]

    async def test_banned_user_rejected(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_auth(inner)
        update = make_update(user_id=444)  # banned
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_not_awaited()
        assert "封禁" in update.message.reply_text.call_args[0][0]

    async def test_unknown_user_rejected(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_auth(inner)
        update = make_update(user_id=999)  # not in db
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_not_awaited()
        assert "申请" in update.message.reply_text.call_args[0][0]


# ===========================================================================
# bot/middleware.py — require_owner
# ===========================================================================

class TestRequireOwner:

    async def test_owner_called(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_owner(inner)
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_awaited_once_with(update, context)

    async def test_non_owner_user_rejected(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_owner(inner)
        update = make_update(user_id=333)  # regular user
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_not_awaited()
        assert "管理员" in update.message.reply_text.call_args[0][0]

    async def test_unknown_user_rejected(self, db_with_users):
        inner = AsyncMock()
        wrapped = require_owner(inner)
        update = make_update(user_id=999)
        context = make_context(db=db_with_users)

        await wrapped(update, context)
        inner.assert_not_awaited()
        assert "管理员" in update.message.reply_text.call_args[0][0]


# ===========================================================================
# bot/handlers/start.py — start_command
# ===========================================================================

class TestStartCommand:

    async def test_unknown_user(self, db_with_users):
        update = make_update(user_id=999)
        context = make_context(db=db_with_users)
        await start_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "欢迎" in text
        assert "/apply" in text

    async def test_pending_user(self, db_with_users):
        update = make_update(user_id=222)
        context = make_context(db=db_with_users)
        await start_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "等待" in text

    async def test_banned_user(self, db_with_users):
        update = make_update(user_id=444)
        context = make_context(db=db_with_users)
        await start_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "封禁" in text

    async def test_owner(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        await start_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text

    async def test_regular_user(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await start_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "欢迎回来" in text
        assert "/help" in text


# ===========================================================================
# bot/handlers/start.py — apply_command
# ===========================================================================

class TestApplyCommand:

    async def test_new_user_applies(self, db_with_users):
        update = make_update(user_id=555, username="newbie", full_name="New User")
        context = make_context(db=db_with_users, owner_id=111)
        await apply_command(update, context)

        # User gets confirmation
        text = update.message.reply_text.call_args[0][0]
        assert "申请已提交" in text

        # Owner gets notified with InlineKeyboard
        context.bot.send_message.assert_awaited_once()
        call_kwargs = context.bot.send_message.call_args[1]
        assert call_kwargs["chat_id"] == 111
        assert "新用户申请" in call_kwargs["text"]
        assert call_kwargs["reply_markup"] is not None

    async def test_existing_owner(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        await apply_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "无需申请" in text

    async def test_existing_user(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await apply_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "已经是授权用户" in text

    async def test_existing_pending(self, db_with_users):
        update = make_update(user_id=222)
        context = make_context(db=db_with_users)
        await apply_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "已经提交过" in text

    async def test_existing_banned(self, db_with_users):
        update = make_update(user_id=444)
        context = make_context(db=db_with_users)
        await apply_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "封禁" in text


# ===========================================================================
# bot/handlers/start.py — approval_callback
# ===========================================================================

class TestApprovalCallback:

    async def test_owner_approves_pending(self, db_with_users):
        update = make_update(
            user_id=111, is_callback=True, callback_data="approve:222"
        )
        context = make_context(db=db_with_users, owner_id=111)
        await approval_callback(update, context)

        query = update.callback_query
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_awaited_once()
        edit_text = query.edit_message_text.call_args[0][0]
        assert "已通过" in edit_text

        # Verify user is now approved in db
        user = db_with_users.get_user(222)
        assert user.role == "user"

        # Notification sent to the user
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.call_args[1]["chat_id"] == 222

    async def test_owner_rejects_pending(self, db_with_users):
        update = make_update(
            user_id=111, is_callback=True, callback_data="reject:222"
        )
        context = make_context(db=db_with_users, owner_id=111)
        await approval_callback(update, context)

        query = update.callback_query
        query.edit_message_text.assert_awaited_once()
        edit_text = query.edit_message_text.call_args[0][0]
        assert "已拒绝" in edit_text

        # User should be removed from db
        user = db_with_users.get_user(222)
        assert user is None

    async def test_non_owner_blocked(self, db_with_users):
        update = make_update(
            user_id=333, is_callback=True, callback_data="approve:222"
        )
        context = make_context(db=db_with_users, owner_id=111)
        await approval_callback(update, context)

        query = update.callback_query
        # answer is called twice: once unconditionally, once with alert
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "仅管理员" in second_call[0][0]
        assert second_call[1]["show_alert"] is True
        # edit_message_text should NOT be called
        query.edit_message_text.assert_not_awaited()

    async def test_approve_already_processed(self, db_with_users):
        # Approve user 333 who is already approved (not pending)
        update = make_update(
            user_id=111, is_callback=True, callback_data="approve:333"
        )
        context = make_context(db=db_with_users, owner_id=111)
        await approval_callback(update, context)

        query = update.callback_query
        edit_text = query.edit_message_text.call_args[0][0]
        assert "操作失败" in edit_text


# ===========================================================================
# bot/handlers/start.py — help_command
# ===========================================================================

class TestHelpCommand:

    async def test_unknown_user(self, db_with_users):
        update = make_update(user_id=999)
        context = make_context(db=db_with_users)
        await help_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "/apply" in text
        assert "/users" not in text

    async def test_owner(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        await help_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "管理命令" in text
        assert "/users" in text
        assert "/ban" in text

    async def test_regular_user(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await help_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "/search" in text
        assert "/users" not in text

    async def test_banned_user(self, db_with_users):
        update = make_update(user_id=444)
        context = make_context(db=db_with_users)
        await help_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "封禁" in text


# ===========================================================================
# bot/handlers/admin.py — users_command
# ===========================================================================

class TestUsersCommand:

    async def test_owner_lists_users(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        await users_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "所有用户" in text
        # Should contain all user IDs
        assert "111" in text
        assert "222" in text
        assert "333" in text
        assert "444" in text

    async def test_owner_no_users(self, db_with_owner):
        """Owner is the only user — still shows at least the owner."""
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner)
        await users_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        # get_all_users should return at least the owner
        assert "111" in text or "暂无" in text

    async def test_non_owner_blocked(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await users_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text


# ===========================================================================
# bot/handlers/admin.py — pending_command
# ===========================================================================

class TestPendingCommand:

    async def test_has_pending(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        await pending_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "待审批" in text
        assert "222" in text

    async def test_no_pending(self, db_with_owner):
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner)
        await pending_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "没有待审批" in text

    async def test_non_owner_blocked(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await pending_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text


# ===========================================================================
# bot/handlers/admin.py — ban_command
# ===========================================================================

class TestBanCommand:

    async def test_no_args(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=[])
        await ban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "用法" in text

    async def test_non_numeric_arg(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["abc"])
        await ban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "数字" in text

    async def test_valid_user_banned(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["333"])
        await ban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "已封禁" in text
        assert "333" in text

        # Notification sent to banned user
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.call_args[1]["chat_id"] == 333

    async def test_owner_target_fails(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["111"])
        await ban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "操作失败" in text or "无法封禁" in text


# ===========================================================================
# bot/handlers/admin.py — unban_command
# ===========================================================================

class TestUnbanCommand:

    async def test_no_args(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=[])
        await unban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "用法" in text

    async def test_non_numeric_arg(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["abc"])
        await unban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "数字" in text

    async def test_valid_banned_user(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["444"])
        await unban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "已解封" in text
        assert "444" in text

        # Notification sent to unbanned user
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.call_args[1]["chat_id"] == 444

    async def test_non_banned_user_fails(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users, args=["333"])  # user, not banned
        await unban_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "操作失败" in text


# ===========================================================================
# bot/handlers/search.py — search_command
# ===========================================================================

class TestSearchCommand:

    async def test_no_args(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=[])
        await search_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "用法" in text

    async def test_successful_search(self, db_with_users):
        results = [_make_torrent(i) for i in range(1, 4)]
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=results)

        update = make_update(user_id=333)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["test"], page_size=10
        )
        await search_command(update, context)

        # reply_text called twice: searching message + results
        assert update.message.reply_text.await_count == 2
        result_text = update.message.reply_text.call_args_list[1][0][0]
        assert "Torrent.Title.1" in result_text
        assert "1.37 GB" in result_text

        # user_cache populated
        assert 333 in user_cache
        assert user_cache[333]["results"] == results
        assert user_cache[333]["page"] == 0

    async def test_empty_results(self, db_with_users):
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=[])

        update = make_update(user_id=333)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["nothing"], page_size=10
        )
        await search_command(update, context)

        result_text = update.message.reply_text.call_args_list[1][0][0]
        assert "未找到" in result_text

    async def test_search_error(self, db_with_users):
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(side_effect=Exception("network error"))

        update = make_update(user_id=333)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["fail"], page_size=10
        )
        await search_command(update, context)

        result_text = update.message.reply_text.call_args_list[1][0][0]
        assert "出错" in result_text


# ===========================================================================
# bot/handlers/search.py — more_command
# ===========================================================================

class TestMoreCommand:

    async def test_no_cache(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await more_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "先" in text and "搜索" in text

    async def test_has_more_pages(self, db_with_users):
        results = [_make_torrent(i) for i in range(1, 16)]  # 15 results
        user_cache[333] = {"results": results, "page": 0, "page_size": 10}

        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await more_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "第 2/2 页" in text
        assert user_cache[333]["page"] == 1

    async def test_last_page(self, db_with_users):
        results = [_make_torrent(i) for i in range(1, 6)]  # 5 results, 1 page with size=10
        user_cache[333] = {"results": results, "page": 0, "page_size": 10}

        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await more_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "最后一页" in text


# ===========================================================================
# bot/handlers/search.py — _format_results
# ===========================================================================

class TestFormatResults:

    def test_normal_results(self):
        results = [_make_torrent(i) for i in range(1, 4)]
        text = _format_results(results, page=0, page_size=10)
        assert "1." in text
        assert "2." in text
        assert "3." in text
        assert "第 1/1 页" in text
        assert "共 3 条" in text

    def test_empty_results(self):
        text = _format_results([], page=0, page_size=10)
        assert "未找到" in text

    def test_pagination_footer(self):
        results = [_make_torrent(i) for i in range(1, 26)]  # 25 results
        text = _format_results(results, page=0, page_size=10)
        assert "第 1/3 页" in text
        assert "共 25 条" in text
        assert "/more" in text

    def test_last_page_no_more(self):
        results = [_make_torrent(i) for i in range(1, 26)]
        text = _format_results(results, page=2, page_size=10)
        assert "第 3/3 页" in text
        assert "/more" not in text


# ===========================================================================
# bot/handlers/download.py — download_command
# ===========================================================================

class TestDownloadCommand:

    @pytest.fixture
    def search_cache(self):
        """Pre-populate user_cache with 3 results for user 333."""
        results = [_make_torrent(i) for i in range(1, 4)]
        user_cache[333] = {"results": results, "page": 0, "page_size": 10}
        return results

    async def test_no_cache(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await download_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "先" in text and "搜索" in text

    async def test_no_args(self, db_with_users, search_cache):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=[])
        await download_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "用法" in text

    async def test_invalid_number(self, db_with_users, search_cache):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["abc"])
        await download_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "有效" in text or "数字" in text

    async def test_out_of_range(self, db_with_users, search_cache):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["99"])
        await download_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "超出范围" in text

    async def test_url_download_success(self, db_with_users, search_cache):
        pt_client = AsyncMock()
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value=True)

        update = make_update(user_id=333, full_name="Approved User")
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client, args=["1"]
        )
        await download_command(update, context)

        # Two reply_text calls: "adding..." and "success"
        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("成功" in t for t in texts)

        # Owner notified (user 333 != owner 111)
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.call_args[1]["chat_id"] == 111

    async def test_url_fails_file_succeeds(self, db_with_users, search_cache):
        pt_client = AsyncMock()
        pt_client.download_torrent = AsyncMock(return_value=b"torrent data")
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value=False)
        dl_client.add_torrent_file = AsyncMock(return_value=True)

        update = make_update(user_id=333)
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client, args=["1"]
        )
        await download_command(update, context)

        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("成功" in t for t in texts)
        dl_client.add_torrent_file.assert_awaited_once()

    async def test_both_fail(self, db_with_users, search_cache):
        pt_client = AsyncMock()
        pt_client.download_torrent = AsyncMock(side_effect=Exception("fail"))
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value=False)

        update = make_update(user_id=333)
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client, args=["1"]
        )
        await download_command(update, context)

        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("失败" in t for t in texts)

    async def test_owner_downloads_no_notification(self, db_with_users, search_cache):
        # Put cache for owner user_id=111
        user_cache[111] = user_cache.pop(333)

        pt_client = AsyncMock()
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value=True)

        update = make_update(user_id=111)
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client,
            owner_id=111, args=["1"]
        )
        await download_command(update, context)

        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("成功" in t for t in texts)

        # Owner should NOT receive notification about own download
        context.bot.send_message.assert_not_awaited()
