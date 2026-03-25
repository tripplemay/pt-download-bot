"""Tests for bot/handlers/settings.py — all /set* commands and /settings."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.handlers.settings import (
    setsite_command, setpasskey_command, settmdb_command,
    setds_command, setqb_command, settr_command,
    settings_command,
    _check_setup_complete, _is_valid_url,
)
from bot.handlers.search import user_cache, _search_result_cache
from tests.conftest import make_update, make_context


@pytest.fixture(autouse=True)
def _clear_caches():
    user_cache.clear()
    _search_result_cache.clear()
    yield
    user_cache.clear()
    _search_result_cache.clear()


# ===================================================================
# Helper function tests
# ===================================================================


class TestIsValidUrl:
    def test_valid_http(self):
        assert _is_valid_url("http://localhost:5000") is True

    def test_valid_https(self):
        assert _is_valid_url("https://example.com") is True

    def test_no_scheme(self):
        assert _is_valid_url("example.com") is False

    def test_empty(self):
        assert _is_valid_url("") is False

    def test_ftp_rejected(self):
        assert _is_valid_url("ftp://example.com") is False


class TestCheckSetupComplete:
    def test_all_set(self, db_with_owner):
        db_with_owner.set_setting("pt_site_url", "https://example.com")
        db_with_owner.set_setting("pt_passkey", "abc123")
        db_with_owner.set_setting("dl_client_type", "qbittorrent")
        db_with_owner.set_setting("dl_client_host", "http://localhost:8080")
        assert _check_setup_complete(db_with_owner) is True

    def test_missing_passkey(self, db_with_owner):
        db_with_owner.set_setting("pt_site_url", "https://example.com")
        db_with_owner.set_setting("dl_client_type", "qbittorrent")
        db_with_owner.set_setting("dl_client_host", "http://localhost:8080")
        assert _check_setup_complete(db_with_owner) is False

    def test_missing_dl_client(self, db_with_owner):
        db_with_owner.set_setting("pt_site_url", "https://example.com")
        db_with_owner.set_setting("pt_passkey", "abc123")
        assert _check_setup_complete(db_with_owner) is False

    def test_empty_db(self, db_with_owner):
        assert _check_setup_complete(db_with_owner) is False


# ===================================================================
# /setsite
# ===================================================================


class TestSetSiteCommand:

    async def test_no_args_shows_usage(self, db_with_owner):
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner, args=[])
        await setsite_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "PT 站地址" in text
        assert update.message.reply_text.call_args[1].get("reply_markup") is not None

    async def test_invalid_url(self, db_with_owner):
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner, args=["not-a-url"])
        await setsite_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "无效" in text

    async def test_valid_url_saved(self, db_with_owner):
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner, args=["https://ptchdbits.co"])

        with patch("bot.main.init_pt_client", return_value=None):
            await setsite_command(update, context)

        assert db_with_owner.get_setting("pt_site_url") == "https://ptchdbits.co"
        text = update.message.reply_text.call_args[0][0]
        assert "ptchdbits.co" in text

    async def test_strips_trailing_slash(self, db_with_owner):
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner, args=["https://example.com/"])

        with patch("bot.main.init_pt_client", return_value=None):
            await setsite_command(update, context)

        assert db_with_owner.get_setting("pt_site_url") == "https://example.com"

    async def test_non_owner_blocked(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["https://example.com"])
        await setsite_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text


# ===================================================================
# /setpasskey
# ===================================================================


class TestSetPasskeyCommand:

    async def test_no_args_shows_usage(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=[])
        context.bot.delete_message = AsyncMock()
        await setpasskey_command(update, context)
        # Should have tried to delete user message
        context.bot.delete_message.assert_called_once()
        # Should show usage
        text = context.bot.send_message.call_args[1].get("text", "") or context.bot.send_message.call_args[0][0] if context.bot.send_message.call_args[0] else context.bot.send_message.call_args[1]["text"]
        assert "Passkey" in text
        assert context.bot.send_message.call_args[1].get("reply_markup") is not None

    async def test_saves_passkey_without_site(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=["abc123key"])
        context.bot.delete_message = AsyncMock()
        msg_mock = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        with patch("bot.main.init_pt_client", return_value=None):
            await setpasskey_command(update, context)

        assert db_with_owner.get_setting("pt_passkey") == "abc123key"

    async def test_deletes_user_message(self, db_with_owner):
        """The command should delete the user's message containing the passkey."""
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        update.message.message_id = 42
        context = make_context(db=db_with_owner, args=["secret_passkey"])
        context.bot.delete_message = AsyncMock()
        msg_mock = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        with patch("bot.main.init_pt_client", return_value=None):
            await setpasskey_command(update, context)

        context.bot.delete_message.assert_called_once()


# ===================================================================
# /settmdb
# ===================================================================


class TestSetTmdbCommand:

    async def test_saves_tmdb_key(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=["tmdb_key_123"])
        context.bot.delete_message = AsyncMock()

        with patch("bot.main.init_tmdb_client", return_value=MagicMock()):
            await settmdb_command(update, context)

        assert db_with_owner.get_setting("tmdb_api_key") == "tmdb_key_123"
        assert context.bot_data["tmdb_client"] is not None


# ===================================================================
# /setds, /setqb, /settr
# ===================================================================


class TestSetDlClientCommands:

    async def test_setds_no_args(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=[])
        context.bot.delete_message = AsyncMock()
        await setds_command(update, context)
        text = context.bot.send_message.call_args[1].get("text", "")
        assert "Download Station" in text
        assert context.bot.send_message.call_args[1].get("reply_markup") is not None

    async def test_setds_saves_config(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=["http://localhost:5000", "admin", "pass123"])
        context.bot.delete_message = AsyncMock()
        msg_mock = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        mock_client = AsyncMock()
        mock_client.test_connection = AsyncMock(return_value=True)
        with patch("bot.main.init_dl_client", return_value=mock_client):
            await setds_command(update, context)

        assert db_with_owner.get_setting("dl_client_type") == "download_station"
        assert db_with_owner.get_setting("dl_client_host") == "http://localhost:5000"
        assert db_with_owner.get_setting("dl_client_username") == "admin"
        assert db_with_owner.get_setting("dl_client_password") == "pass123"

    async def test_setqb_saves_config(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=["http://localhost:8080", "admin", "adminadmin"])
        context.bot.delete_message = AsyncMock()
        msg_mock = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        mock_client = AsyncMock()
        mock_client.test_connection = AsyncMock(return_value=True)
        with patch("bot.main.init_dl_client", return_value=mock_client):
            await setqb_command(update, context)

        assert db_with_owner.get_setting("dl_client_type") == "qbittorrent"

    async def test_settr_saves_config(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=["http://localhost:9091", "admin", "admin"])
        context.bot.delete_message = AsyncMock()
        msg_mock = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        mock_client = AsyncMock()
        mock_client.test_connection = AsyncMock(return_value=True)
        with patch("bot.main.init_dl_client", return_value=mock_client):
            await settr_command(update, context)

        assert db_with_owner.get_setting("dl_client_type") == "transmission"

    async def test_invalid_host_url(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        context = make_context(db=db_with_owner, args=["not-a-url", "admin", "pass"])
        context.bot.delete_message = AsyncMock()
        await setds_command(update, context)
        text = context.bot.send_message.call_args[1].get("text", "")
        assert "无效" in text

    async def test_deletes_user_message(self, db_with_owner):
        update = make_update(user_id=111)
        update.effective_chat = MagicMock()
        update.effective_chat.id = 111
        update.message.message_id = 42
        context = make_context(db=db_with_owner, args=["http://localhost:5000", "admin", "secret"])
        context.bot.delete_message = AsyncMock()
        msg_mock = AsyncMock()
        context.bot.send_message = AsyncMock(return_value=msg_mock)

        mock_client = AsyncMock()
        mock_client.test_connection = AsyncMock(return_value=True)
        with patch("bot.main.init_dl_client", return_value=mock_client):
            await setds_command(update, context)

        context.bot.delete_message.assert_called_once()


# ===================================================================
# /settings
# ===================================================================


class TestSettingsCommand:

    async def test_empty_config(self, db_with_owner):
        update = make_update(user_id=111)
        context = make_context(db=db_with_owner)
        await settings_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "未配置" in text
        assert "❌" in text
        assert "/setsite" in text

    async def test_fully_configured(self, db_with_owner):
        db_with_owner.set_setting("pt_site_url", "https://example.com")
        db_with_owner.set_setting("pt_passkey", "key123")
        db_with_owner.set_setting("pt_cookie", "uid=1; pass=abc")
        db_with_owner.set_setting("tmdb_api_key", "tmdb123")
        db_with_owner.set_setting("dl_client_type", "qbittorrent")
        db_with_owner.set_setting("dl_client_host", "http://localhost:8080")

        update = make_update(user_id=111)
        context = make_context(db=db_with_owner)
        await settings_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "✅ 所有必需配置已完成" in text
        assert "example.com" in text
        assert "/setsite" not in text  # no missing config prompts

    async def test_partial_config(self, db_with_owner):
        db_with_owner.set_setting("pt_site_url", "https://example.com")
        # missing passkey and dl client

        update = make_update(user_id=111)
        context = make_context(db=db_with_owner)
        await settings_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "必需配置未完成" in text
        assert "/setpasskey" in text

    async def test_non_owner_blocked(self, db_with_users):
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        await settings_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "管理员" in text


# ===================================================================
# _migrate_env_to_db
# ===================================================================


class TestMigrateEnvToDb:

    def test_migrates_pt_config(self, db_with_owner, monkeypatch):
        from bot.main import _migrate_env_to_db
        monkeypatch.setenv("PT_SITE_URL", "https://example.com")
        monkeypatch.setenv("PT_PASSKEY", "mykey")
        _migrate_env_to_db(db_with_owner)
        assert db_with_owner.get_setting("pt_site_url") == "https://example.com"
        assert db_with_owner.get_setting("pt_passkey") == "mykey"

    def test_does_not_overwrite_existing(self, db_with_owner, monkeypatch):
        from bot.main import _migrate_env_to_db
        db_with_owner.set_setting("pt_site_url", "https://existing.com")
        monkeypatch.setenv("PT_SITE_URL", "https://new.com")
        _migrate_env_to_db(db_with_owner)
        assert db_with_owner.get_setting("pt_site_url") == "https://existing.com"

    def test_ignores_placeholder_values(self, db_with_owner, monkeypatch):
        from bot.main import _migrate_env_to_db
        monkeypatch.setenv("PT_PASSKEY", "placeholder")
        _migrate_env_to_db(db_with_owner)
        assert db_with_owner.get_setting("pt_passkey") is None

    def test_migrates_download_station(self, db_with_owner, monkeypatch):
        from bot.main import _migrate_env_to_db
        monkeypatch.setenv("DOWNLOAD_CLIENT", "download_station")
        monkeypatch.setenv("DS_HOST", "http://localhost:5000")
        monkeypatch.setenv("DS_USERNAME", "admin")
        monkeypatch.setenv("DS_PASSWORD", "pass")
        _migrate_env_to_db(db_with_owner)
        assert db_with_owner.get_setting("dl_client_type") == "download_station"
        assert db_with_owner.get_setting("dl_client_host") == "http://localhost:5000"
        assert db_with_owner.get_setting("dl_client_username") == "admin"
        assert db_with_owner.get_setting("dl_client_password") == "pass"

    def test_migrates_qbittorrent(self, db_with_owner, monkeypatch):
        from bot.main import _migrate_env_to_db
        monkeypatch.setenv("DOWNLOAD_CLIENT", "qbittorrent")
        monkeypatch.setenv("QB_HOST", "http://localhost:8080")
        monkeypatch.setenv("QB_USERNAME", "admin")
        monkeypatch.setenv("QB_PASSWORD", "adminadmin")
        _migrate_env_to_db(db_with_owner)
        assert db_with_owner.get_setting("dl_client_type") == "qbittorrent"
        assert db_with_owner.get_setting("dl_client_host") == "http://localhost:8080"


# ===================================================================
# init_* functions
# ===================================================================


class TestInitFunctions:

    def test_init_pt_client_with_config(self, db_with_owner):
        from bot.main import init_pt_client
        db_with_owner.set_setting("pt_site_url", "https://example.com")
        db_with_owner.set_setting("pt_passkey", "testkey")
        with patch("bot.main.NexusPHPSite") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = init_pt_client(db_with_owner)
            assert client is not None
            mock_cls.assert_called_once_with("https://example.com", "testkey")

    def test_init_pt_client_missing_config(self, db_with_owner):
        from bot.main import init_pt_client
        assert init_pt_client(db_with_owner) is None

    def test_init_dl_client_with_config(self, db_with_owner):
        from bot.main import init_dl_client
        db_with_owner.set_setting("dl_client_type", "qbittorrent")
        db_with_owner.set_setting("dl_client_host", "http://localhost:8080")
        db_with_owner.set_setting("dl_client_username", "admin")
        db_with_owner.set_setting("dl_client_password", "pass")
        with patch("bot.main.create_download_client") as mock_create:
            mock_create.return_value = MagicMock()
            client = init_dl_client(db_with_owner)
            assert client is not None

    def test_init_dl_client_missing_config(self, db_with_owner):
        from bot.main import init_dl_client
        assert init_dl_client(db_with_owner) is None

    def test_init_tmdb_client_with_key(self, db_with_owner):
        from bot.main import init_tmdb_client
        db_with_owner.set_setting("tmdb_api_key", "test_key")
        with patch("bot.main.TMDBClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = init_tmdb_client(db_with_owner)
            assert client is not None

    def test_init_tmdb_client_no_key(self, db_with_owner):
        from bot.main import init_tmdb_client
        assert init_tmdb_client(db_with_owner) is None


# ===================================================================
# Graceful degradation: search/download without clients
# ===================================================================


class TestGracefulDegradation:

    async def test_search_without_pt_client(self, db_with_users):
        from bot.handlers.search import search_command
        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["test"])
        # pt_client not set in bot_data
        await search_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "PT 站尚未配置" in text

    async def test_download_without_dl_client(self, db_with_users):
        from bot.handlers.search import user_cache
        from bot.handlers.download import download_command
        from bot.pt.base import TorrentResult

        # Populate cache
        user_cache[333] = {
            "results": [TorrentResult(title="test", torrent_url="http://x.com/dl", size="1 GB")],
            "page": 0, "page_size": 10,
        }

        update = make_update(user_id=333)
        context = make_context(db=db_with_users, args=["1"])
        # dl_client not set in bot_data
        await download_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "下载客户端尚未配置" in text

    async def test_status_without_dl_client(self, db_with_users):
        from bot.handlers.status import status_command
        update = make_update(user_id=333)
        context = make_context(db=db_with_users)
        # dl_client not set
        await status_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "下载客户端尚未配置" in text

    async def test_test_command_without_any_client(self, db_with_users):
        from bot.main import test_command
        update = make_update(user_id=111)
        context = make_context(db=db_with_users)
        # Neither pt_client nor dl_client set
        await test_command(update, context)
        text = update.message.reply_text.call_args[0][0]
        assert "未配置" in text
