"""Shared fixtures for all tests."""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.database import Database


@pytest.fixture
def tmp_db():
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    db.conn.close()
    os.unlink(path)


@pytest.fixture
def db_with_owner(tmp_db):
    """Database with owner initialized (ID=111)."""
    tmp_db.init_owner(111)
    return tmp_db


@pytest.fixture
def db_with_users(db_with_owner):
    """Database with owner + pending user + approved user + banned user."""
    db = db_with_owner
    db.apply_user(222, "pending_user", "Pending User")
    db.apply_user(333, "approved_user", "Approved User")
    db.approve_user(333, 111)
    db.apply_user(444, "banned_user", "Banned User")
    db.approve_user(444, 111)
    db.ban_user(444)
    return db


def make_update(user_id=111, username="testuser", full_name="Test User",
                text="/start", chat_id=111, is_callback=False, callback_data=None):
    """Create a mock telegram Update object."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.effective_user.full_name = full_name
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_message = update.message
    update.message.text = text
    update.message.chat_id = chat_id

    if is_callback:
        update.callback_query = MagicMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.from_user.id = user_id
        update.callback_query.data = callback_data
        update.callback_query.message.text = "original message"
        update.callback_query.edit_message_text = AsyncMock()
    else:
        update.callback_query = None

    return update


def make_context(db=None, pt_client=None, dl_client=None, owner_id=111,
                 page_size=10, args=None):
    """Create a mock telegram context object."""
    context = MagicMock()
    context.bot_data = {}
    if db:
        context.bot_data["db"] = db
    if pt_client:
        context.bot_data["pt_client"] = pt_client
    if dl_client:
        context.bot_data["dl_client"] = dl_client
    context.bot_data["owner_id"] = owner_id
    context.bot_data["page_size"] = page_size
    context.args = args or []
    context.bot = MagicMock()
    context.bot.send_message = AsyncMock()
    return context
