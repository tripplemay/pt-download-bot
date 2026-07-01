"""Tests for direct external resource downloads."""

from unittest.mock import AsyncMock, MagicMock

from bot.handlers.external import (
    MAX_TORRENT_FILE_SIZE,
    _classify_external_url,
    external_torrent_file_message,
    external_url_message,
)
from tests.conftest import make_context, make_update


class TestClassifyExternalUrl:

    def test_magnet_url(self):
        result = _classify_external_url(" magnet:?xt=urn:btih:abc ")
        assert result == ("magnet:?xt=urn:btih:abc", "磁力链接")

    def test_http_url(self):
        result = _classify_external_url("https://example.com/a.torrent")
        assert result == ("https://example.com/a.torrent", "HTTP/HTTPS 链接")

    def test_requires_single_url_message(self):
        assert _classify_external_url("download https://example.com/a.torrent") is None
        assert _classify_external_url("hello") is None


class TestExternalUrlMessage:

    async def test_user_magnet_success(self, db_with_users):
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value="task_1")

        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        update = make_update(
            user_id=333,
            full_name="Approved User",
            text="magnet:?xt=urn:btih:abcdef&dn=Movie+Name",
        )
        update.message.reply_to_message = None
        update.message.reply_text = AsyncMock(return_value=status_msg)
        context = make_context(db=db_with_users, dl_client=dl_client, owner_id=111)

        await external_url_message(update, context)

        dl_client.add_torrent_url.assert_awaited_once_with(
            "magnet:?xt=urn:btih:abcdef&dn=Movie+Name"
        )
        assert "成功" in status_msg.edit_text.call_args[0][0]
        record = db_with_users.get_download_by_task_id("task_1")
        assert record["telegram_id"] == 333
        assert "Movie Name" in record["torrent_title"]
        assert record["torrent_size"] == "磁力链接"
        context.bot.send_message.assert_awaited_once()
        assert context.bot.send_message.call_args[1]["chat_id"] == 111

    async def test_owner_success_does_not_notify_owner(self, db_with_users):
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value="task_owner")

        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        update = make_update(user_id=111, text="https://example.com/file.torrent")
        update.message.reply_to_message = None
        update.message.reply_text = AsyncMock(return_value=status_msg)
        context = make_context(db=db_with_users, dl_client=dl_client, owner_id=111)

        await external_url_message(update, context)

        assert "成功" in status_msg.edit_text.call_args[0][0]
        context.bot.send_message.assert_not_awaited()

    async def test_no_download_client(self, db_with_users):
        update = make_update(user_id=333, text="https://example.com/file.torrent")
        update.message.reply_to_message = None
        context = make_context(db=db_with_users)

        await external_url_message(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "下载客户端尚未配置" in text

    async def test_download_client_failure(self, db_with_users):
        dl_client = AsyncMock()
        dl_client.add_torrent_url = AsyncMock(return_value=None)

        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        update = make_update(user_id=333, text="https://example.com/file.torrent")
        update.message.reply_to_message = None
        update.message.reply_text = AsyncMock(return_value=status_msg)
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_url_message(update, context)

        assert "失败" in status_msg.edit_text.call_args[0][0]
        assert db_with_users.get_download_by_task_id("task_missing") is None
        context.bot.send_message.assert_not_awaited()

    async def test_pending_user_blocked(self, db_with_users):
        dl_client = AsyncMock()
        update = make_update(user_id=222, text="magnet:?xt=urn:btih:abc")
        update.message.reply_to_message = None
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_url_message(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "等待" in text
        dl_client.add_torrent_url.assert_not_awaited()

    async def test_ignores_normal_text_when_called_directly(self, db_with_users):
        dl_client = AsyncMock()
        update = make_update(user_id=333, text="please download https://example.com/a.torrent")
        update.message.reply_to_message = None
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_url_message(update, context)

        update.message.reply_text.assert_not_awaited()
        dl_client.add_torrent_url.assert_not_awaited()

    async def test_ignores_replies_to_avoid_force_reply_conflict(self, db_with_users):
        dl_client = AsyncMock()
        update = make_update(user_id=333, text="https://example.com/a.torrent")
        update.message.reply_to_message = MagicMock()
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_url_message(update, context)

        update.message.reply_text.assert_not_awaited()
        dl_client.add_torrent_url.assert_not_awaited()


class TestExternalTorrentFileMessage:

    async def test_torrent_file_success(self, db_with_users):
        dl_client = AsyncMock()
        dl_client.add_torrent_file = AsyncMock(return_value="file_task")

        tg_file = MagicMock()
        tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"torrent bytes"))

        document = MagicMock()
        document.file_name = "movie.torrent"
        document.mime_type = "application/x-bittorrent"
        document.file_size = 1024
        document.get_file = AsyncMock(return_value=tg_file)

        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        update = make_update(user_id=333)
        update.message.document = document
        update.message.reply_text = AsyncMock(return_value=status_msg)
        context = make_context(db=db_with_users, dl_client=dl_client, owner_id=111)

        await external_torrent_file_message(update, context)

        dl_client.add_torrent_file.assert_awaited_once_with(b"torrent bytes", "movie.torrent")
        assert "成功" in status_msg.edit_text.call_args[0][0]
        record = db_with_users.get_download_by_task_id("file_task")
        assert record["telegram_id"] == 333
        assert record["torrent_title"] == "站外种子文件: movie.torrent"
        assert record["torrent_size"] == ".torrent 文件"
        context.bot.send_message.assert_awaited_once()

    async def test_torrent_file_too_large(self, db_with_users):
        dl_client = AsyncMock()

        document = MagicMock()
        document.file_name = "huge.torrent"
        document.mime_type = "application/x-bittorrent"
        document.file_size = MAX_TORRENT_FILE_SIZE + 1

        update = make_update(user_id=333)
        update.message.document = document
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_torrent_file_message(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "过大" in text
        dl_client.add_torrent_file.assert_not_awaited()

    async def test_non_torrent_document_ignored(self, db_with_users):
        dl_client = AsyncMock()

        document = MagicMock()
        document.file_name = "readme.txt"
        document.mime_type = "text/plain"
        document.file_size = 10

        update = make_update(user_id=333)
        update.message.document = document
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_torrent_file_message(update, context)

        update.message.reply_text.assert_not_awaited()
        dl_client.add_torrent_file.assert_not_awaited()

    async def test_no_download_client(self, db_with_users):
        document = MagicMock()
        document.file_name = "movie.torrent"
        document.mime_type = "application/x-bittorrent"
        document.file_size = 10

        update = make_update(user_id=333)
        update.message.document = document
        context = make_context(db=db_with_users)

        await external_torrent_file_message(update, context)

        text = update.message.reply_text.call_args[0][0]
        assert "下载客户端尚未配置" in text

    async def test_download_client_failure(self, db_with_users):
        dl_client = AsyncMock()
        dl_client.add_torrent_file = AsyncMock(return_value=None)

        tg_file = MagicMock()
        tg_file.download_as_bytearray = AsyncMock(return_value=bytearray(b"torrent bytes"))

        document = MagicMock()
        document.file_name = "movie.torrent"
        document.mime_type = "application/x-bittorrent"
        document.file_size = 1024
        document.get_file = AsyncMock(return_value=tg_file)

        status_msg = MagicMock()
        status_msg.edit_text = AsyncMock()
        update = make_update(user_id=333)
        update.message.document = document
        update.message.reply_text = AsyncMock(return_value=status_msg)
        context = make_context(db=db_with_users, dl_client=dl_client)

        await external_torrent_file_message(update, context)

        assert "失败" in status_msg.edit_text.call_args[0][0]
        context.bot.send_message.assert_not_awaited()
