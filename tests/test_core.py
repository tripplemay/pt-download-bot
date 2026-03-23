"""Tests for bot.database, bot.config, and bot.utils modules."""

import os

import pytest

from bot.database import Database, User
from bot.config import load_config, TelegramConfig, PTConfig, DownloadClientConfig
from bot.utils import truncate


# =====================================================================
# Database tests
# =====================================================================


class TestDatabaseInit:
    def test_creates_tables(self, tmp_db):
        """Tables 'users' and 'download_logs' should exist after init."""
        cur = tmp_db.conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in cur.fetchall()]
        assert "users" in tables
        assert "download_logs" in tables


class TestInitOwner:
    def test_creates_owner(self, tmp_db):
        tmp_db.init_owner(111)
        user = tmp_db.get_user(111)
        assert user is not None
        assert user.role == "owner"
        assert user.display_name == "Owner"

    def test_idempotent(self, tmp_db):
        """Calling init_owner twice with the same ID should not raise."""
        tmp_db.init_owner(111)
        tmp_db.init_owner(111)
        user = tmp_db.get_user(111)
        assert user is not None
        assert user.role == "owner"

    def test_upgrades_existing_user_to_owner(self, tmp_db):
        """If a user already exists with a different role, init_owner upgrades them."""
        tmp_db.apply_user(111, "someuser", "Some User")
        assert tmp_db.get_user(111).role == "pending"
        tmp_db.init_owner(111)
        assert tmp_db.get_user(111).role == "owner"


class TestGetUser:
    def test_existing_user(self, db_with_owner):
        user = db_with_owner.get_user(111)
        assert user is not None
        assert isinstance(user, User)
        assert user.telegram_id == 111
        assert user.role == "owner"

    def test_non_existing_user(self, db_with_owner):
        assert db_with_owner.get_user(999) is None


class TestApplyUser:
    def test_success(self, db_with_owner):
        result = db_with_owner.apply_user(222, "newuser", "New User")
        assert result is True
        user = db_with_owner.get_user(222)
        assert user.role == "pending"
        assert user.username == "newuser"
        assert user.display_name == "New User"

    def test_duplicate_returns_false(self, db_with_owner):
        db_with_owner.apply_user(222, "newuser", "New User")
        result = db_with_owner.apply_user(222, "newuser", "New User")
        assert result is False


class TestApproveUser:
    def test_success_on_pending_user(self, db_with_users):
        result = db_with_users.approve_user(222, 111)
        assert result is True
        user = db_with_users.get_user(222)
        assert user.role == "user"
        assert user.approved_by == 111
        assert user.approved_at is not None

    def test_fails_on_non_pending_user(self, db_with_users):
        """Approved user (333) is already 'user', so approve again should fail."""
        result = db_with_users.approve_user(333, 111)
        assert result is False

    def test_fails_on_non_existing_user(self, db_with_users):
        result = db_with_users.approve_user(999, 111)
        assert result is False


class TestRejectUser:
    def test_success_on_pending_user(self, db_with_users):
        result = db_with_users.reject_user(222)
        assert result is True
        assert db_with_users.get_user(222) is None

    def test_fails_on_non_pending_user(self, db_with_users):
        """Approved user (333) cannot be rejected."""
        result = db_with_users.reject_user(333)
        assert result is False

    def test_fails_on_non_existing_user(self, db_with_users):
        result = db_with_users.reject_user(999)
        assert result is False


class TestBanUser:
    def test_success_on_regular_user(self, db_with_users):
        result = db_with_users.ban_user(333)
        assert result is True
        assert db_with_users.get_user(333).role == "banned"

    def test_fails_on_owner(self, db_with_users):
        """Owner (111) cannot be banned."""
        result = db_with_users.ban_user(111)
        assert result is False
        assert db_with_users.get_user(111).role == "owner"

    def test_fails_on_non_existing_user(self, db_with_users):
        result = db_with_users.ban_user(999)
        assert result is False


class TestUnbanUser:
    def test_success_on_banned_user(self, db_with_users):
        result = db_with_users.unban_user(444)
        assert result is True
        assert db_with_users.get_user(444).role == "user"

    def test_fails_on_non_banned_user(self, db_with_users):
        """Approved user (333) is not banned, so unban should fail."""
        result = db_with_users.unban_user(333)
        assert result is False

    def test_fails_on_non_existing_user(self, db_with_users):
        result = db_with_users.unban_user(999)
        assert result is False


class TestUserLists:
    def test_get_pending_users(self, db_with_users):
        pending = db_with_users.get_pending_users()
        assert len(pending) == 1
        assert pending[0].telegram_id == 222
        assert pending[0].role == "pending"

    def test_get_approved_users(self, db_with_users):
        """Should include owner (111) and approved user (333), not pending or banned."""
        approved = db_with_users.get_approved_users()
        ids = [u.telegram_id for u in approved]
        assert 111 in ids
        assert 333 in ids
        assert 222 not in ids
        assert 444 not in ids

    def test_get_all_users(self, db_with_users):
        """Should return all four users."""
        all_users = db_with_users.get_all_users()
        assert len(all_users) == 4
        ids = {u.telegram_id for u in all_users}
        assert ids == {111, 222, 333, 444}


class TestLogDownload:
    def test_inserts_record(self, db_with_owner):
        db_with_owner.log_download(111, "Test Torrent", "1.5 GB")
        cur = db_with_owner.conn.cursor()
        cur.execute("SELECT * FROM download_logs WHERE telegram_id = 111")
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["torrent_title"] == "Test Torrent"
        assert rows[0]["torrent_size"] == "1.5 GB"

    def test_inserts_multiple_records(self, db_with_owner):
        db_with_owner.log_download(111, "Torrent A", "1 GB")
        db_with_owner.log_download(111, "Torrent B", "2 GB")
        cur = db_with_owner.conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM download_logs WHERE telegram_id = 111")
        assert cur.fetchone()["cnt"] == 2


class TestIsAuthorized:
    def test_true_for_owner(self, db_with_users):
        assert db_with_users.is_authorized(111) is True

    def test_true_for_approved_user(self, db_with_users):
        assert db_with_users.is_authorized(333) is True

    def test_false_for_pending_user(self, db_with_users):
        assert db_with_users.is_authorized(222) is False

    def test_false_for_banned_user(self, db_with_users):
        assert db_with_users.is_authorized(444) is False

    def test_false_for_non_existing_user(self, db_with_users):
        assert db_with_users.is_authorized(999) is False


class TestIsOwner:
    def test_true_for_owner(self, db_with_users):
        assert db_with_users.is_owner(111) is True

    def test_false_for_regular_user(self, db_with_users):
        assert db_with_users.is_owner(333) is False

    def test_false_for_pending_user(self, db_with_users):
        assert db_with_users.is_owner(222) is False

    def test_false_for_non_existing_user(self, db_with_users):
        assert db_with_users.is_owner(999) is False


# =====================================================================
# Config tests
# =====================================================================

# Minimal required env vars for a valid config
REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "test-token-123",
    "OWNER_TELEGRAM_ID": "111",
    "PT_SITE_URL": "https://example.com",
    "PT_PASSKEY": "abc123",
}

# All env vars that load_config reads, used to ensure a clean slate
ALL_CONFIG_KEYS = [
    "TELEGRAM_BOT_TOKEN", "OWNER_TELEGRAM_ID",
    "PT_SITE_URL", "PT_PASSKEY", "PT_MAX_RESULTS", "PT_PAGE_SIZE", "PT_COOKIE",
    "DOWNLOAD_CLIENT",
    "DS_HOST", "DS_USERNAME", "DS_PASSWORD",
    "QB_HOST", "QB_USERNAME", "QB_PASSWORD",
    "TR_HOST", "TR_USERNAME", "TR_PASSWORD",
    "TMDB_API_KEY",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all config-related env vars, then set the required ones."""
    for key in ALL_CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, val in REQUIRED_ENV.items():
        monkeypatch.setenv(key, val)


class TestLoadConfig:
    def test_with_all_required_vars(self, clean_env):
        tg, pt, dl, _ = load_config()
        assert isinstance(tg, TelegramConfig)
        assert tg.bot_token == "test-token-123"
        assert tg.owner_id == 111
        assert isinstance(pt, PTConfig)
        assert pt.site_url == "https://example.com"
        assert pt.passkey == "abc123"
        assert isinstance(dl, DownloadClientConfig)

    def test_missing_telegram_bot_token(self, clean_env, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            load_config()

    def test_missing_owner_telegram_id(self, clean_env, monkeypatch):
        monkeypatch.delenv("OWNER_TELEGRAM_ID")
        with pytest.raises(ValueError, match="OWNER_TELEGRAM_ID"):
            load_config()

    def test_missing_pt_site_url(self, clean_env, monkeypatch):
        monkeypatch.delenv("PT_SITE_URL")
        with pytest.raises(ValueError, match="PT_SITE_URL"):
            load_config()

    def test_missing_pt_passkey(self, clean_env, monkeypatch):
        monkeypatch.delenv("PT_PASSKEY")
        with pytest.raises(ValueError, match="PT_PASSKEY"):
            load_config()

    def test_download_client_defaults_to_download_station(self, clean_env):
        _, _, dl, _ = load_config()
        assert dl.client_type == "download_station"

    def test_download_client_custom_value(self, clean_env, monkeypatch):
        monkeypatch.setenv("DOWNLOAD_CLIENT", "qbittorrent")
        _, _, dl, _ = load_config()
        assert dl.client_type == "qbittorrent"

    def test_pt_max_results_default(self, clean_env):
        _, pt, _, _ = load_config()
        assert pt.max_results == 50

    def test_pt_max_results_custom(self, clean_env, monkeypatch):
        monkeypatch.setenv("PT_MAX_RESULTS", "100")
        _, pt, _, _ = load_config()
        assert pt.max_results == 100

    def test_pt_page_size_default(self, clean_env):
        _, pt, _, _ = load_config()
        assert pt.page_size == 10

    def test_pt_page_size_custom(self, clean_env, monkeypatch):
        monkeypatch.setenv("PT_PAGE_SIZE", "25")
        _, pt, _, _ = load_config()
        assert pt.page_size == 25


# =====================================================================
# Utils tests
# =====================================================================


class TestTruncate:
    def test_short_string_unchanged(self):
        assert truncate("hello") == "hello"

    def test_exact_length_unchanged(self):
        text = "a" * 55
        assert truncate(text) == text

    def test_long_string_truncated_with_ellipsis(self):
        text = "a" * 60
        result = truncate(text)
        assert len(result) == 55
        assert result.endswith("\u2026")
        assert result == "a" * 54 + "\u2026"

    def test_custom_max_len(self):
        text = "abcdefghij"  # length 10
        result = truncate(text, max_len=5)
        assert len(result) == 5
        assert result == "abcd\u2026"

    def test_custom_max_len_no_truncation(self):
        text = "abc"
        assert truncate(text, max_len=10) == "abc"
