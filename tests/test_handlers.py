"""Comprehensive tests for handler and middleware modules."""

import time

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.middleware import require_auth, require_owner
from bot.handlers.start import start_command, apply_command, approval_callback, help_command
from bot.handlers.admin import users_command, pending_command, ban_command, unban_command, setcookie_command, cookiestatus_command
from bot.handlers.search import (
    search_command, more_command, _format_results, user_cache, _search_result_cache,
    page_callback, dl_callback, _seeders_icon, _build_keyboard, ask_command,
)
from bot.handlers.download import download_command
from bot.pt.base import TorrentResult
from bot.pt.nexusphp import CookieExpiredError

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
def _clear_caches():
    """Ensure caches are empty before and after each test."""
    user_cache.clear()
    _search_result_cache.clear()
    yield
    user_cache.clear()
    _search_result_cache.clear()


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

    async def test_owner_first_use_shows_guide(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        await start_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "首次使用" in text
        assert "/setsite" in text

    async def test_owner_after_setup(self, db_with_users):
        db_with_users.set_setting("setup_completed", "true")
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
        assert "/s" in text
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

        # reply_text returns a message mock with edit_text
        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["test"], page_size=10
        )
        await search_command(update, context)

        # Results shown via msg.edit_text
        msg_mock.edit_text.assert_awaited()
        result_text = msg_mock.edit_text.call_args[0][0]
        assert "Torrent Title" in result_text
        assert "1.37 GB" in result_text

        # user_cache populated
        assert 333 in user_cache
        assert user_cache[333]["results"] == results
        assert user_cache[333]["page"] == 0

    async def test_empty_results(self, db_with_users):
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=[])
        pt_client.search_web = AsyncMock(return_value=[])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["nothing"], page_size=10
        )
        await search_command(update, context)

        msg_mock.edit_text.assert_awaited()
        result_text = msg_mock.edit_text.call_args[0][0]
        assert "未找到" in result_text

    async def test_search_error(self, db_with_users):
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(side_effect=Exception("network error"))
        pt_client.search_web = AsyncMock(return_value=[])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["fail"], page_size=10
        )
        await search_command(update, context)

        msg_mock.edit_text.assert_awaited()
        result_text = msg_mock.edit_text.call_args[0][0]
        assert "未找到" in result_text


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
        dl_client.add_torrent_url = AsyncMock(return_value="dbid_100")

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
        dl_client.add_torrent_url = AsyncMock(return_value=None)
        dl_client.add_torrent_file = AsyncMock(return_value="dbid_101")

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
        dl_client.add_torrent_url = AsyncMock(return_value=None)

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
        dl_client.add_torrent_url = AsyncMock(return_value="dbid_102")

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


# ===========================================================================
# bot/handlers/admin.py — setcookie_command
# ===========================================================================

class TestSetcookieCommand:

    async def test_no_args(self, db_with_users):
        pt_client = AsyncMock()
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        update.message.message_id = 42
        context = make_context(db=db_with_users, pt_client=pt_client, args=[])
        context.bot.delete_message = AsyncMock()
        context.bot.send_message = AsyncMock()

        await setcookie_command(update, context)

        # Message deleted for security
        context.bot.delete_message.assert_awaited_once()
        # Usage message sent
        context.bot.send_message.assert_awaited_once()
        text = context.bot.send_message.call_args[1].get("text") or context.bot.send_message.call_args[0][0] if context.bot.send_message.call_args[0] else context.bot.send_message.call_args[1]["text"]
        assert "用法" in text or "setcookie" in text

    async def test_valid_cookie(self, db_with_users):
        pt_client = AsyncMock()
        pt_client.search_web = AsyncMock(return_value=[])

        msg_mock = AsyncMock()
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        update.message.message_id = 42
        context = make_context(db=db_with_users, pt_client=pt_client, args=["uid=1;", "pass=abc"])
        context.bot.delete_message = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        await setcookie_command(update, context)

        # Cookie validated via search_web
        pt_client.search_web.assert_awaited_once()
        # Success message
        msg_mock.edit_text.assert_awaited_once()
        text = msg_mock.edit_text.call_args[0][0]
        assert "已保存并验证通过" in text or "验证通过" in text
        # Cookie saved in DB
        assert db_with_users.get_setting("pt_cookie") == "uid=1; pass=abc"

    async def test_invalid_cookie(self, db_with_users):
        pt_client = AsyncMock()
        pt_client.search_web = AsyncMock(side_effect=CookieExpiredError("expired"))

        msg_mock = AsyncMock()
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        update.message.message_id = 42
        context = make_context(db=db_with_users, pt_client=pt_client, args=["bad_cookie"])
        context.bot.delete_message = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        await setcookie_command(update, context)

        msg_mock.edit_text.assert_awaited_once()
        text = msg_mock.edit_text.call_args[0][0]
        assert "无效" in text
        # Cookie NOT saved
        assert db_with_users.get_setting("pt_cookie") is None

    async def test_non_owner_blocked(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["some_cookie"])

        await setcookie_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text


# ===========================================================================
# bot/handlers/admin.py — cookiestatus_command
# ===========================================================================

class TestCookiestatusCommand:

    async def test_cookie_configured(self, db_with_users):
        db_with_users.set_setting("pt_cookie", "uid=1; pass=abc")
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)

        await cookiestatus_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "已配置" in text

    async def test_cookie_not_configured(self, db_with_users):
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)

        await cookiestatus_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "未配置" in text


# ===========================================================================
# bot/handlers/search.py — search_command (web-first strategy)
# ===========================================================================

class TestSearchCommandWebFirst:

    async def test_web_search_primary_when_cookie_exists(self, db_with_users):
        """When cookie is set, search_web is called and RSS search is NOT called."""
        db_with_users.set_setting("pt_cookie", "valid_cookie")
        results = [_make_torrent(i) for i in range(1, 4)]
        pt_client = AsyncMock()
        pt_client.search_web = AsyncMock(return_value=results)
        pt_client.search = AsyncMock(return_value=[])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["testmovie"], page_size=10
        )

        await search_command(update, context)

        # Web search was called
        pt_client.search_web.assert_awaited()
        # RSS search was NOT called (web returned results)
        pt_client.search.assert_not_awaited()
        # Results displayed
        msg_mock.edit_text.assert_awaited()
        text = msg_mock.edit_text.call_args[0][0]
        assert "Torrent Title" in text

    async def test_fallback_to_rss_when_no_cookie(self, db_with_users):
        """When no cookie is set, RSS search is used."""
        # Ensure no cookie
        assert db_with_users.get_setting("pt_cookie") is None

        results = [_make_torrent(1)]
        pt_client = AsyncMock()
        pt_client.search_web = AsyncMock(return_value=[])
        pt_client.search = AsyncMock(return_value=results)

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["testmovie"], page_size=10
        )

        await search_command(update, context)

        # Web search was NOT called (no cookie)
        pt_client.search_web.assert_not_awaited()
        # RSS search was called
        pt_client.search.assert_awaited_once()

    async def test_cookie_expired_fallback_to_rss(self, db_with_users):
        """When search_web raises CookieExpiredError, cookie is deleted and RSS fallback used."""
        db_with_users.set_setting("pt_cookie", "expired_cookie")
        rss_results = [_make_torrent(1)]
        pt_client = AsyncMock()
        pt_client.search_web = AsyncMock(side_effect=CookieExpiredError("expired"))
        pt_client.search = AsyncMock(return_value=rss_results)

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["testmovie"], page_size=10
        )

        await search_command(update, context)

        # Cookie deleted from DB
        assert db_with_users.get_setting("pt_cookie") is None
        # Owner notified about cookie expiry
        context.bot.send_message.assert_awaited_once()
        notify_kwargs = context.bot.send_message.call_args[1]
        assert notify_kwargs["chat_id"] == 111
        assert "Cookie" in notify_kwargs["text"] or "cookie" in notify_kwargs["text"].lower()
        # RSS fallback used
        pt_client.search.assert_awaited_once()
        # Results displayed
        msg_mock.edit_text.assert_awaited()
        text = msg_mock.edit_text.call_args[0][0]
        assert "Torrent Title" in text

    async def test_web_search_progressive_en_title_first(self, db_with_users):
        """Chinese keyword + TMDB translation: EN title search runs first and is most precise."""
        db_with_users.set_setting("pt_cookie", "valid_cookie")

        en_results = [
            TorrentResult(title="EN.Result.1", torrent_url="https://example.com/dl/en1", size="2 GB"),
            TorrentResult(title="EN.Result.2", torrent_url="https://example.com/dl/en2", size="3 GB"),
            TorrentResult(title="EN.Result.3", torrent_url="https://example.com/dl/en3", size="4 GB"),
        ]

        pt_client = AsyncMock()
        # EN title search returns enough results → no further searches needed
        pt_client.search_web = AsyncMock(return_value=en_results)
        pt_client.search = AsyncMock(return_value=[])

        tmdb_client = AsyncMock()
        tmdb_client.translate = AsyncMock(return_value=["EnglishTitle"])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["测试电影"], page_size=10
        )
        context.bot_data["tmdb_client"] = tmdb_client

        await search_command(update, context)

        # Only EN title search needed (>= 3 results)
        assert pt_client.search_web.await_count == 1
        pt_client.search_web.assert_awaited_with("EnglishTitle", cookie="valid_cookie", search_area=0)
        pt_client.search.assert_not_awaited()
        assert len(user_cache[333]["results"]) == 3

    async def test_web_search_progressive_fallback_to_cn(self, db_with_users):
        """When EN title returns < 3 results, falls back to CN title then CN description."""
        db_with_users.set_setting("pt_cookie", "valid_cookie")

        call_count = 0

        async def mock_search_web(keyword, cookie=None, search_area=0):
            nonlocal call_count
            call_count += 1
            if keyword == "EnglishTitle" and search_area == 0:
                # EN title: only 1 result
                return [TorrentResult(title="EN.1", torrent_url="https://x.com/dl/en1", size="1 GB")]
            if keyword == "测试电影" and search_area == 0:
                # CN title: 1 more result
                return [TorrentResult(title="CN.Title.1", torrent_url="https://x.com/dl/cn1", size="2 GB")]
            if keyword == "测试电影" and search_area == 1:
                # CN description: 2 more results
                return [
                    TorrentResult(title="CN.Desc.1", torrent_url="https://x.com/dl/desc1", size="3 GB"),
                    TorrentResult(title="CN.Desc.2", torrent_url="https://x.com/dl/desc2", size="4 GB"),
                ]
            return []

        pt_client = AsyncMock()
        pt_client.search_web = AsyncMock(side_effect=mock_search_web)
        pt_client.search = AsyncMock(return_value=[])

        tmdb_client = AsyncMock()
        tmdb_client.translate = AsyncMock(return_value=["EnglishTitle"])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["测试电影"], page_size=10
        )
        context.bot_data["tmdb_client"] = tmdb_client

        await search_command(update, context)

        # All 3 tiers called: EN title (1) + CN title (1) + CN desc (2)
        assert pt_client.search_web.await_count == 3
        pt_client.search.assert_not_awaited()
        merged = user_cache[333]["results"]
        titles = {r.title for r in merged}
        assert "EN.1" in titles
        assert "CN.Title.1" in titles
        assert "CN.Desc.1" in titles
        assert "CN.Desc.2" in titles
        assert len(merged) == 4


# ===========================================================================
# bot/handlers/search.py — _seeders_icon
# ===========================================================================

class TestSeedersIcon:

    def test_zero_seeders(self):
        result = _seeders_icon(0)
        assert "0种" in result
        assert "🔴" in result

    def test_positive_seeders(self):
        result = _seeders_icon(5)
        assert "5种" in result
        assert "🟢" in result

    def test_large_seeders(self):
        result = _seeders_icon(100)
        assert "100种" in result
        assert "🟢" in result


# ===========================================================================
# bot/handlers/search.py — _build_keyboard
# ===========================================================================

class TestBuildKeyboard:

    def test_first_page_multiple_pages(self):
        """First page should have '下一页' but no '上一页'."""
        kb = _build_keyboard(user_id=333, page=0, page_size=5, total=12)
        all_buttons = [btn for row in kb.inline_keyboard for btn in row]
        texts = [btn.text for btn in all_buttons]
        callbacks = [btn.callback_data for btn in all_buttons]
        # Download buttons 1-5
        assert "1" in texts
        assert "5" in texts
        assert "dl:333:1" in callbacks
        assert "dl:333:5" in callbacks
        # Nav: next only
        assert "下一页 ▶" in texts
        assert "◀ 上一页" not in texts

    def test_middle_page(self):
        """Middle page should have both nav buttons."""
        kb = _build_keyboard(user_id=333, page=1, page_size=5, total=15)
        all_buttons = [btn for row in kb.inline_keyboard for btn in row]
        texts = [btn.text for btn in all_buttons]
        assert "◀ 上一页" in texts
        assert "下一页 ▶" in texts

    def test_last_page(self):
        """Last page should have '上一页' but no '下一页'."""
        kb = _build_keyboard(user_id=333, page=2, page_size=5, total=12)
        all_buttons = [btn for row in kb.inline_keyboard for btn in row]
        texts = [btn.text for btn in all_buttons]
        assert "◀ 上一页" in texts
        assert "下一页 ▶" not in texts
        # Download buttons 11-12
        callbacks = [btn.callback_data for btn in all_buttons]
        assert "dl:333:11" in callbacks
        assert "dl:333:12" in callbacks

    def test_single_page_no_nav(self):
        """Single page should have no nav buttons."""
        kb = _build_keyboard(user_id=333, page=0, page_size=10, total=3)
        all_buttons = [btn for row in kb.inline_keyboard for btn in row]
        texts = [btn.text for btn in all_buttons]
        assert "◀ 上一页" not in texts
        assert "下一页 ▶" not in texts


# ===========================================================================
# bot/handlers/search.py — page_callback
# ===========================================================================

class TestPageCallback:

    def _make_page_update(self, user_id, callback_data):
        return make_update(
            user_id=user_id, is_callback=True, callback_data=callback_data
        )

    async def test_valid_page_turn(self, db_with_users):
        """Authorized user turns to a valid page."""
        results = [_make_torrent(i) for i in range(1, 16)]
        user_cache[333] = {"results": results, "page": 0, "page_size": 5}

        update = self._make_page_update(333, "page:333:1")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        query.edit_message_text.assert_awaited_once()
        text = query.edit_message_text.call_args[0][0]
        assert "第 2/" in text
        assert user_cache[333]["page"] == 1

    async def test_unauthorized_user(self, db_with_users):
        """Unauthorized user (not in DB) gets rejected."""
        update = self._make_page_update(999, "page:999:0")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "无权限" in second_call[0][0]
        assert second_call[1]["show_alert"] is True

    async def test_invalid_format_too_few_parts(self, db_with_users):
        """Callback data with wrong format is rejected."""
        update = self._make_page_update(333, "page:333")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "无效" in second_call[0][0]

    async def test_negative_page(self, db_with_users):
        """Negative page number is rejected."""
        update = self._make_page_update(333, "page:333:-1")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "无效" in second_call[0][0]

    async def test_non_numeric_page(self, db_with_users):
        """Non-numeric page value is rejected."""
        update = self._make_page_update(333, "page:333:abc")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2

    async def test_wrong_user(self, db_with_users):
        """User trying to page someone else's results is rejected."""
        results = [_make_torrent(i) for i in range(1, 6)]
        user_cache[111] = {"results": results, "page": 0, "page_size": 5}

        update = self._make_page_update(333, "page:111:0")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "不是你的" in second_call[0][0]

    async def test_expired_cache(self, db_with_users):
        """User with no cache gets 'expired' message."""
        update = self._make_page_update(333, "page:333:0")
        context = make_context(db=db_with_users)
        await page_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "过期" in second_call[0][0]


# ===========================================================================
# bot/handlers/search.py — dl_callback
# ===========================================================================

class TestDlCallback:

    def _make_dl_update(self, user_id, callback_data, full_name="Test User", username="testuser"):
        return make_update(
            user_id=user_id, is_callback=True, callback_data=callback_data,
            full_name=full_name, username=username,
        )

    @pytest.fixture
    def dl_search_cache(self):
        """Pre-populate user_cache for dl_callback tests."""
        results = [_make_torrent(i) for i in range(1, 4)]
        user_cache[333] = {"results": results, "page": 0, "page_size": 10}
        return results

    async def test_valid_download_success(self, db_with_users, dl_search_cache):
        """Successful download via URL method."""
        pt_client = AsyncMock()
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value="task_1")

        update = self._make_dl_update(333, "dl:333:1")
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client
        )
        await dl_callback(update, context)

        send_calls = context.bot.send_message.call_args_list
        texts = [c[1]["text"] for c in send_calls]
        assert any("正在" in t or "添加下载" in t for t in texts)
        assert any("成功" in t for t in texts)

    async def test_valid_download_failure(self, db_with_users, dl_search_cache):
        """Both URL and file methods fail."""
        pt_client = AsyncMock()
        pt_client.download_torrent = AsyncMock(side_effect=Exception("fail"))
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value=None)

        update = self._make_dl_update(333, "dl:333:1")
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client
        )
        await dl_callback(update, context)

        send_calls = context.bot.send_message.call_args_list
        texts = [c[1]["text"] for c in send_calls]
        assert any("失败" in t for t in texts)

    async def test_unauthorized_user(self, db_with_users):
        """Unauthorized user is rejected."""
        update = self._make_dl_update(999, "dl:999:1")
        context = make_context(db=db_with_users)
        await dl_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "无权限" in second_call[0][0]

    async def test_invalid_format(self, db_with_users):
        """Callback data with wrong format is rejected."""
        update = self._make_dl_update(333, "dl:333")
        context = make_context(db=db_with_users)
        await dl_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "无效" in second_call[0][0]

    async def test_expired_cache(self, db_with_users):
        """No cache for user results in 'expired' message."""
        update = self._make_dl_update(333, "dl:333:1")
        context = make_context(db=db_with_users)
        await dl_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "过期" in second_call[0][0]

    async def test_no_dl_client(self, db_with_users, dl_search_cache):
        """No download client configured."""
        update = self._make_dl_update(333, "dl:333:1")
        context = make_context(db=db_with_users, pt_client=AsyncMock())
        await dl_callback(update, context)

        send_calls = context.bot.send_message.call_args_list
        texts = [c[1]["text"] for c in send_calls]
        assert any("尚未配置" in t for t in texts)

    async def test_wrong_user(self, db_with_users, dl_search_cache):
        """User trying to download from someone else's results is rejected."""
        update = self._make_dl_update(111, "dl:333:1")
        context = make_context(db=db_with_users)
        await dl_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "不是你的" in second_call[0][0]

    async def test_invalid_index_zero(self, db_with_users):
        """Index 0 is rejected as invalid."""
        update = self._make_dl_update(333, "dl:333:0")
        context = make_context(db=db_with_users)
        await dl_callback(update, context)

        query = update.callback_query
        assert query.answer.await_count == 2
        second_call = query.answer.call_args_list[1]
        assert "无效" in second_call[0][0]

    async def test_owner_download_no_notification(self, db_with_users):
        """Owner download does not trigger self-notification."""
        results = [_make_torrent(1)]
        user_cache[111] = {"results": results, "page": 0, "page_size": 10}

        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value="task_1")

        update = self._make_dl_update(111, "dl:111:1")
        context = make_context(
            db=db_with_users, pt_client=AsyncMock(), dl_client=dl_client, owner_id=111
        )
        await dl_callback(update, context)

        send_calls = context.bot.send_message.call_args_list
        texts = [c[1]["text"] for c in send_calls]
        assert any("成功" in t for t in texts)
        # No notification to owner about own download
        for call in send_calls:
            text = call[1]["text"]
            assert "添加了下载" not in text

    async def test_non_owner_triggers_notification(self, db_with_users, dl_search_cache):
        """Non-owner download triggers notification to owner."""
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value="task_1")

        update = self._make_dl_update(333, "dl:333:1", full_name="Approved User")
        context = make_context(
            db=db_with_users, pt_client=AsyncMock(), dl_client=dl_client, owner_id=111
        )
        await dl_callback(update, context)

        send_calls = context.bot.send_message.call_args_list
        owner_calls = [c for c in send_calls if c[1].get("chat_id") == 111]
        assert len(owner_calls) == 1
        assert "添加了下载" in owner_calls[0][1]["text"]


# ===========================================================================
# bot/handlers/search.py — search_command (cache hit & keyword too long)
# ===========================================================================

class TestSearchCommandCacheAndValidation:

    async def test_keyword_too_long(self, db_with_users):
        """Keyword over 100 chars is rejected."""
        long_keyword = "a" * 101
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=[long_keyword])
        await search_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "100" in text or "过长" in text

    async def test_cache_hit_with_results(self, db_with_users):
        """When search result cache has valid entry, PT site is not queried."""
        results = [_make_torrent(i) for i in range(1, 4)]
        _search_result_cache["cached"] = {
            "results": results,
            "expires": time.time() + 300,
        }

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["cached"], page_size=10)
        await search_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "Torrent Title" in text
        assert "条" in text
        # P0 fix: cache hit with results now includes keyboard
        assert update.message.reply_text.call_args[1].get("reply_markup") is not None
        assert 333 in user_cache
        assert len(user_cache[333]["results"]) == 3

    async def test_cache_hit_empty_results(self, db_with_users):
        """Cached empty results show 'not found' message."""
        _search_result_cache["empty"] = {
            "results": [],
            "expires": time.time() + 300,
        }

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["empty"], page_size=10)
        await search_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "未找到" in text

    async def test_cache_expired_not_used(self, db_with_users):
        """Expired cache entry is not used; PT site is queried."""
        _search_result_cache["old"] = {
            "results": [_make_torrent(1)],
            "expires": time.time() - 10,
        }

        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=[_make_torrent(99)])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(
            db=db_with_users, pt_client=pt_client, args=["old"], page_size=10
        )
        await search_command(update, context)

        pt_client.search.assert_awaited_once()
        msg_mock.edit_text.assert_awaited()
        text = msg_mock.edit_text.call_args[0][0]
        assert "Torrent Title 99" in text


# ===========================================================================
# bot/handlers/search.py — ask_command (AI smart search)
# ===========================================================================

class TestAskCommand:

    async def test_no_ai_client(self, db_with_users):
        """ai_client not configured -> prompt to set up."""
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        context.bot_data["ai_client"] = None
        await ask_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "未启用" in text or "/setai" in text

    async def test_no_args(self, db_with_users):
        """No arguments -> usage message."""
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=[])
        context.bot_data["ai_client"] = AsyncMock()
        await ask_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "用法" in text

    async def test_ai_parse_failure(self, db_with_users):
        """AI returns None -> error message."""
        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value=None)
        pt_client = AsyncMock()

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["test"])
        context.bot_data["ai_client"] = ai_client

        await ask_command(update, context)
        msg_mock.edit_text.assert_awaited()
        assert "失败" in msg_mock.edit_text.call_args[0][0]

    async def test_direct_mode(self, db_with_users):
        """AI returns direct mode -> delegates to search_command."""
        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value={"mode": "direct", "keyword": "Interstellar"})
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=[])
        pt_client.search_web = AsyncMock(return_value=[])

        msg_mock = AsyncMock()
        msg_mock.delete = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["test query"])
        context.bot_data["ai_client"] = ai_client
        context.bot_data["tmdb_client"] = None

        await ask_command(update, context)
        # In direct mode it deletes the analyzing msg and calls search_command
        msg_mock.delete.assert_awaited()

    async def test_recommend_mode(self, db_with_users):
        """AI returns recommend mode -> searches each title."""
        results = [TorrentResult(
            title="Inception.2010.1080p",
            torrent_url="http://x/download.php?id=1",
            size="10 GB",
            seeders=5,
        )]

        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value={
            "mode": "recommend",
            "titles": ["Inception", "Interstellar"],
            "reason": "经典烧脑科幻",
        })
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=results)

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["类似盗梦空间"])
        context.bot_data["ai_client"] = ai_client
        context.bot_data["tmdb_client"] = None

        await ask_command(update, context)
        # Should show results via edit_text
        assert msg_mock.edit_text.await_count >= 1

    async def test_tmdb_person_credits_mode(self, db_with_users):
        """AI returns tmdb/person_credits mode -> queries TMDB then searches PT."""
        results = [TorrentResult(
            title="Movie.A.2024.1080p",
            torrent_url="http://x/download.php?id=10",
            size="5 GB",
            seeders=3,
        )]

        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value={
            "mode": "tmdb",
            "action": "person_credits",
            "person": "诺兰",
            "role": "director",
            "media": "movie",
        })
        tmdb_client = AsyncMock()
        tmdb_client.search_person = AsyncMock(return_value=525)
        tmdb_client.get_person_credits = AsyncMock(return_value=["Movie A", "Movie B"])

        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=results)

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["诺兰导演的电影"])
        context.bot_data["ai_client"] = ai_client
        context.bot_data["tmdb_client"] = tmdb_client

        await ask_command(update, context)
        tmdb_client.search_person.assert_awaited_once_with("诺兰")
        tmdb_client.get_person_credits.assert_awaited_once_with(525, role="director", media="movie")
        assert msg_mock.edit_text.await_count >= 1

    async def test_tmdb_person_not_found(self, db_with_users):
        """TMDB person not found -> shows error message."""
        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value={
            "mode": "tmdb",
            "action": "person_credits",
            "person": "不存在的人",
            "role": "actor",
            "media": "movie",
        })
        tmdb_client = AsyncMock()
        tmdb_client.search_person = AsyncMock(return_value=None)

        pt_client = AsyncMock()

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["不存在的人的电影"])
        context.bot_data["ai_client"] = ai_client
        context.bot_data["tmdb_client"] = tmdb_client

        await ask_command(update, context)
        msg_mock.edit_text.assert_awaited()
        text = msg_mock.edit_text.call_args[0][0]
        assert "未找到" in text

    async def test_tmdb_discover_mode(self, db_with_users):
        """AI returns tmdb/discover mode -> discovers then searches PT."""
        results = [TorrentResult(
            title="Korean.Film.2024.1080p",
            torrent_url="http://x/download.php?id=20",
            size="8 GB",
            seeders=10,
        )]

        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value={
            "mode": "tmdb",
            "action": "discover",
            "media": "movie",
            "year": 2024,
            "genre": "action",
            "region": "KR",
        })
        tmdb_client = AsyncMock()
        tmdb_client.discover = AsyncMock(return_value=["Korean Film"])

        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=results)

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["2024韩国动作片"])
        context.bot_data["ai_client"] = ai_client
        context.bot_data["tmdb_client"] = tmdb_client

        await ask_command(update, context)
        tmdb_client.discover.assert_awaited_once_with(
            media="movie", year=2024, genre="action", region="KR",
        )
        assert msg_mock.edit_text.await_count >= 1

    async def test_no_pt_client(self, db_with_users):
        """PT client not configured -> prompt to set up."""
        ai_client = AsyncMock()

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["test"])
        context.bot_data["ai_client"] = ai_client
        # pt_client not in bot_data

        await ask_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "PT" in text or "配置" in text

    async def test_input_too_long(self, db_with_users):
        """Input over 200 chars -> rejected."""
        ai_client = AsyncMock()
        long_input = ["x" * 201]

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=long_input)
        context.bot_data["ai_client"] = ai_client

        await ask_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "200" in text or "过长" in text

    async def test_recommend_no_pt_results(self, db_with_users):
        """Recommend mode but PT has no results -> shows not found."""
        ai_client = AsyncMock()
        ai_client.parse_intent = AsyncMock(return_value={
            "mode": "recommend",
            "titles": ["Obscure Film"],
            "reason": "推荐理由",
        })
        pt_client = AsyncMock()
        pt_client.search = AsyncMock(return_value=[])

        msg_mock = AsyncMock()
        update = make_update(user_id=333)
        update.message.reply_text = AsyncMock(return_value=msg_mock)
        context = make_context(db=db_with_users, pt_client=pt_client, args=["推荐"])
        context.bot_data["ai_client"] = ai_client
        context.bot_data["tmdb_client"] = None

        await ask_command(update, context)
        msg_mock.edit_text.assert_awaited()
        text = msg_mock.edit_text.call_args[0][0]
        assert "未找到" in text
