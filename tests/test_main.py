"""Tests for bot/main.py — entry point and inline commands."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import make_update, make_context


class TestTestCommand:
    """Tests for the /test command defined in main.py."""

    async def test_owner_test_both_ok(self, db_with_users):
        from bot.main import test_command

        pt_client = AsyncMock()
        pt_client.test_connection = AsyncMock(return_value=True)
        dl_client = AsyncMock()
        dl_client.test_connection = AsyncMock(return_value=True)

        update = make_update(user_id=111)
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client
        )
        await test_command(update, context)

        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("正常" in t for t in texts)

    async def test_owner_test_pt_fails(self, db_with_users):
        from bot.main import test_command

        pt_client = AsyncMock()
        pt_client.test_connection = AsyncMock(return_value=False)
        dl_client = AsyncMock()
        dl_client.test_connection = AsyncMock(return_value=True)

        update = make_update(user_id=111)
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client
        )
        await test_command(update, context)

        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("失败" in t for t in texts)

    async def test_owner_test_exception(self, db_with_users):
        from bot.main import test_command

        pt_client = AsyncMock()
        pt_client.test_connection = AsyncMock(side_effect=Exception("boom"))
        dl_client = AsyncMock()
        dl_client.test_connection = AsyncMock(return_value=True)

        update = make_update(user_id=111)
        context = make_context(
            db=db_with_users, pt_client=pt_client, dl_client=dl_client
        )
        await test_command(update, context)

        texts = [c[0][0] for c in update.message.reply_text.call_args_list]
        assert any("异常" in t for t in texts)

    async def test_non_owner_blocked(self, db_with_users):
        from bot.main import test_command

        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await test_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text


class TestStatusCommand:
    """Tests for the /status command defined in main.py."""

    async def test_status_with_tasks(self, db_with_users):
        from bot.main import status_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "Movie.2024.1080p"},
            {"title": "Series.S01E01"},
        ])

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "2 个" in text
        assert "Movie.2024.1080p" in text

    async def test_status_no_tasks(self, db_with_users):
        from bot.main import status_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[])

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "没有" in text

    async def test_status_error(self, db_with_users):
        from bot.main import status_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(side_effect=Exception("fail"))

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "失败" in text

    async def test_status_non_authorized_blocked(self, db_with_users):
        from bot.main import status_command

        update = make_update(user_id=999)
        context = make_context(db=db_with_users)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "申请" in text

    async def test_status_many_tasks_truncated(self, db_with_users):
        from bot.main import status_command

        tasks = [{"title": f"Task {i}"} for i in range(25)]
        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=tasks)

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "25 个" in text
        assert "还有" in text

    async def test_status_long_task_name_truncated(self, db_with_users):
        from bot.main import status_command

        dl_client = AsyncMock()
        dl_client.get_tasks = AsyncMock(return_value=[
            {"title": "A" * 100},
        ])

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, dl_client=dl_client)
        await status_command(update, context)

        text = update.message.reply_text.call_args[0][0]
        # The name should be truncated (59 chars + ellipsis)
        assert "\u2026" in text


class TestMainFunction:
    """Test that main() wires everything together."""

    def test_main_initializes_and_starts(self, monkeypatch):
        """Test main() with all dependencies mocked."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake:token")
        monkeypatch.setenv("OWNER_TELEGRAM_ID", "111")
        monkeypatch.setenv("PT_SITE_URL", "https://example.com")
        monkeypatch.setenv("PT_PASSKEY", "fakekey")
        monkeypatch.setenv("DOWNLOAD_CLIENT", "download_station")
        monkeypatch.setenv("DS_HOST", "http://localhost:5000")
        monkeypatch.setenv("DS_USERNAME", "admin")
        monkeypatch.setenv("DS_PASSWORD", "pass")
        monkeypatch.setenv("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))

        mock_app = MagicMock()
        mock_app.bot_data = {}
        mock_builder = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.build.return_value = mock_app

        with patch("bot.main.ApplicationBuilder", return_value=mock_builder), \
             patch("bot.main.NexusPHPSite") as mock_pt, \
             patch("bot.main.create_download_client") as mock_dl:

            mock_pt.return_value = MagicMock()
            mock_dl.return_value = MagicMock()

            from bot.main import main
            main()

            # Verify app was built and polling started
            mock_builder.token.assert_called_once_with("fake:token")
            mock_builder.build.assert_called_once()
            mock_app.run_polling.assert_called_once()

            # Verify handlers were registered
            assert mock_app.add_handler.call_count >= 14  # 14 commands + 1 callback

            # Verify bot_data populated
            assert "db" in mock_app.bot_data
            assert "pt_client" in mock_app.bot_data
            assert "dl_client" in mock_app.bot_data
            assert mock_app.bot_data["owner_id"] == 111
