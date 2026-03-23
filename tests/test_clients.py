"""Comprehensive tests for download client modules."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.clients import (
    DownloadStationClient,
    QBittorrentClient,
    TransmissionClient,
    create_download_client,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Create a mock DownloadClientConfig with sensible defaults."""
    cfg = MagicMock()
    cfg.client_type = overrides.get("client_type", "download_station")
    cfg.ds_host = overrides.get("ds_host", "http://ds-host:5000")
    cfg.ds_username = overrides.get("ds_username", "ds_user")
    cfg.ds_password = overrides.get("ds_password", "ds_pass")
    cfg.qb_host = overrides.get("qb_host", "http://qb-host:8080")
    cfg.qb_username = overrides.get("qb_username", "qb_user")
    cfg.qb_password = overrides.get("qb_password", "qb_pass")
    cfg.tr_host = overrides.get("tr_host", "http://tr-host:9091")
    cfg.tr_username = overrides.get("tr_username", "tr_user")
    cfg.tr_password = overrides.get("tr_password", "tr_pass")
    return cfg


def _httpx_response(status_code=200, json_data=None, text=None, headers=None):
    """Build a real httpx.Response for use in mocks."""
    resp = httpx.Response(
        status_code=status_code,
        headers=headers or {},
        json=json_data,
        text=text,
        request=httpx.Request("GET", "http://test"),
    )
    return resp


def _mock_async_client(**kwargs):
    """Return an AsyncMock that stands in for httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


# ===================================================================
# Factory function tests
# ===================================================================

class TestCreateDownloadClient:
    @patch("bot.clients.download_station.httpx.AsyncClient", new_callable=lambda: _mock_async_client)
    def test_download_station(self, _mock_client):
        cfg = _make_config(client_type="download_station")
        client = create_download_client(cfg)
        assert isinstance(client, DownloadStationClient)

    @patch("bot.clients.qbittorrent.httpx.AsyncClient", new_callable=lambda: _mock_async_client)
    def test_qbittorrent(self, _mock_client):
        cfg = _make_config(client_type="qbittorrent")
        client = create_download_client(cfg)
        assert isinstance(client, QBittorrentClient)

    @patch("bot.clients.transmission.httpx.AsyncClient", new_callable=lambda: _mock_async_client)
    def test_transmission(self, _mock_client):
        cfg = _make_config(client_type="transmission")
        client = create_download_client(cfg)
        assert isinstance(client, TransmissionClient)

    def test_invalid_type_raises_value_error(self):
        cfg = _make_config(client_type="invalid")
        with pytest.raises(ValueError, match="不支持的下载客户端类型"):
            create_download_client(cfg)

    @patch("bot.clients.download_station.httpx.AsyncClient", new_callable=lambda: _mock_async_client)
    def test_case_insensitive_download_station(self, _mock_client):
        cfg = _make_config(client_type="Download_Station")
        client = create_download_client(cfg)
        assert isinstance(client, DownloadStationClient)

    @patch("bot.clients.qbittorrent.httpx.AsyncClient", new_callable=lambda: _mock_async_client)
    def test_case_insensitive_qbittorrent(self, _mock_client):
        cfg = _make_config(client_type="QBittorrent")
        client = create_download_client(cfg)
        assert isinstance(client, QBittorrentClient)

    @patch("bot.clients.transmission.httpx.AsyncClient", new_callable=lambda: _mock_async_client)
    def test_case_insensitive_transmission(self, _mock_client):
        cfg = _make_config(client_type="TRANSMISSION")
        client = create_download_client(cfg)
        assert isinstance(client, TransmissionClient)


# ===================================================================
# DownloadStationClient tests
# ===================================================================

class TestDownloadStationClient:
    @pytest.fixture
    def ds_client(self):
        with patch("bot.clients.download_station.httpx.AsyncClient"):
            client = DownloadStationClient(
                host="http://ds-host:5000",
                username="admin",
                password="secret",
            )
        client.client = AsyncMock(spec=httpx.AsyncClient)
        return client

    # -- _login ----------------------------------------------------------

    async def test_login_success(self, ds_client):
        ds_client.client.get = AsyncMock(
            return_value=_httpx_response(
                json_data={"success": True, "data": {"sid": "test_sid"}}
            )
        )
        await ds_client._login()
        assert ds_client.sid == "test_sid"

    async def test_login_failure(self, ds_client):
        ds_client.client.get = AsyncMock(
            return_value=_httpx_response(
                json_data={"success": False, "error": {"code": 400}}
            )
        )
        with pytest.raises(ConnectionError, match="登录失败"):
            await ds_client._login()

    # -- add_torrent_url -------------------------------------------------

    async def test_add_torrent_url_success(self, ds_client):
        ds_client._request_with_retry = AsyncMock(
            return_value={"success": True}
        )
        ds_client.sid = "existing_sid"
        result = await ds_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is True
        ds_client._request_with_retry.assert_awaited_once()

    async def test_add_torrent_url_failure(self, ds_client):
        ds_client._request_with_retry = AsyncMock(
            side_effect=ConnectionError("fail")
        )
        ds_client.sid = "existing_sid"
        result = await ds_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is False

    # -- add_torrent_file ------------------------------------------------

    async def test_add_torrent_file_success(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client.client.post = AsyncMock(
            return_value=_httpx_response(json_data={"success": True})
        )
        result = await ds_client.add_torrent_file(b"\x00torrent", "test.torrent")
        assert result is True

    async def test_add_torrent_file_failure(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client.client.post = AsyncMock(side_effect=Exception("network error"))
        result = await ds_client.add_torrent_file(b"\x00torrent", "test.torrent")
        assert result is False

    # -- get_tasks -------------------------------------------------------

    async def test_get_tasks(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._request_with_retry = AsyncMock(
            return_value={
                "success": True,
                "data": {
                    "tasks": [
                        {"title": "Movie.mkv", "id": "1"},
                        {"title": "Album.flac", "id": "2"},
                    ]
                },
            }
        )
        tasks = await ds_client.get_tasks()
        assert len(tasks) == 2
        assert tasks[0]["title"] == "Movie.mkv"
        assert tasks[1]["title"] == "Album.flac"

    # -- test_connection -------------------------------------------------

    async def test_test_connection_success(self, ds_client):
        ds_client.client.get = AsyncMock(
            return_value=_httpx_response(
                json_data={"success": True, "data": {"sid": "new_sid"}}
            )
        )
        ds_client._request_with_retry = AsyncMock(
            return_value={
                "success": True,
                "data": {"tasks": []},
            }
        )
        result = await ds_client.test_connection()
        assert result is True

    async def test_test_connection_failure(self, ds_client):
        ds_client.client.get = AsyncMock(
            side_effect=ConnectionError("cannot connect")
        )
        result = await ds_client.test_connection()
        assert result is False

    # -- _request_with_retry SID expiry ----------------------------------

    async def test_request_with_retry_sid_expiry(self, ds_client):
        """First request returns success=false (SID expired), re-login then retry succeeds."""
        ds_client.sid = "old_sid"

        expired_resp = _httpx_response(
            json_data={"success": False, "error": {"code": 105}}
        )
        success_resp = _httpx_response(
            json_data={"success": True, "data": {"result": "ok"}}
        )

        # request: first call expired, second call success
        ds_client.client.request = AsyncMock(
            side_effect=[expired_resp, success_resp]
        )
        # _login re-establishes sid
        ds_client.client.get = AsyncMock(
            return_value=_httpx_response(
                json_data={"success": True, "data": {"sid": "new_sid"}}
            )
        )

        result = await ds_client._request_with_retry(
            "GET", "http://ds-host:5000/webapi/test", params={"_sid": "old_sid"}
        )
        assert result["success"] is True
        assert ds_client.sid == "new_sid"
        assert ds_client.client.request.await_count == 2

    # -- close -----------------------------------------------------------

    async def test_close(self, ds_client):
        ds_client.client.aclose = AsyncMock()
        await ds_client.close()
        ds_client.client.aclose.assert_awaited_once()


# ===================================================================
# QBittorrentClient tests
# ===================================================================

class TestQBittorrentClient:
    @pytest.fixture
    def qb_client(self):
        with patch("bot.clients.qbittorrent.httpx.AsyncClient"):
            client = QBittorrentClient(
                host="http://qb-host:8080",
                username="admin",
                password="secret",
            )
        client.client = AsyncMock(spec=httpx.AsyncClient)
        return client

    # -- _login ----------------------------------------------------------

    async def test_login_success(self, qb_client):
        qb_client.client.post = AsyncMock(
            return_value=_httpx_response(text="Ok.")
        )
        await qb_client._login()
        assert qb_client.logged_in is True

    async def test_login_failure(self, qb_client):
        qb_client.client.post = AsyncMock(
            return_value=_httpx_response(text="Fails.")
        )
        with pytest.raises(ConnectionError, match="登录失败"):
            await qb_client._login()

    # -- add_torrent_url -------------------------------------------------

    async def test_add_torrent_url_success(self, qb_client):
        qb_client.logged_in = True
        qb_client.client.request = AsyncMock(
            return_value=_httpx_response(json_data={"status": "ok"})
        )
        result = await qb_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is True

    async def test_add_torrent_url_failure(self, qb_client):
        qb_client.logged_in = True
        qb_client.client.request = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "http://x"),
                response=_httpx_response(status_code=500),
            )
        )
        result = await qb_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is False

    # -- add_torrent_file ------------------------------------------------

    async def test_add_torrent_file_success(self, qb_client):
        qb_client.logged_in = True
        qb_client.client.request = AsyncMock(
            return_value=_httpx_response(json_data={"status": "ok"})
        )
        result = await qb_client.add_torrent_file(b"\x00torrent", "test.torrent")
        assert result is True

    # -- get_tasks -------------------------------------------------------

    async def test_get_tasks(self, qb_client):
        qb_client.logged_in = True
        qb_client.client.request = AsyncMock(
            return_value=_httpx_response(
                json_data=[
                    {"name": "Movie.mkv", "hash": "abc"},
                    {"name": "Album.flac", "hash": "def"},
                ]
            )
        )
        tasks = await qb_client.get_tasks()
        assert len(tasks) == 2
        assert tasks[0]["name"] == "Movie.mkv"
        assert tasks[1]["name"] == "Album.flac"

    # -- _request_with_retry 403 -----------------------------------------

    async def test_request_with_retry_403(self, qb_client):
        """First request returns 403, re-login, retry succeeds."""
        qb_client.logged_in = True

        forbidden_resp = _httpx_response(status_code=403)
        success_resp = _httpx_response(status_code=200, json_data={"ok": True})

        qb_client.client.request = AsyncMock(
            side_effect=[forbidden_resp, success_resp]
        )
        # _login via post
        qb_client.client.post = AsyncMock(
            return_value=_httpx_response(text="Ok.")
        )

        resp = await qb_client._request_with_retry("GET", "http://qb-host:8080/api/v2/test")
        assert resp.status_code == 200
        assert qb_client.logged_in is True
        assert qb_client.client.request.await_count == 2

    # -- test_connection -------------------------------------------------

    async def test_test_connection_success(self, qb_client):
        qb_client.client.post = AsyncMock(
            return_value=_httpx_response(text="Ok.")
        )
        qb_client.client.request = AsyncMock(
            return_value=_httpx_response(json_data=[])
        )
        result = await qb_client.test_connection()
        assert result is True

    async def test_test_connection_failure(self, qb_client):
        qb_client.client.post = AsyncMock(
            side_effect=ConnectionError("cannot connect")
        )
        result = await qb_client.test_connection()
        assert result is False


# ===================================================================
# TransmissionClient tests
# ===================================================================

class TestTransmissionClient:
    @pytest.fixture
    def tr_client(self):
        with patch("bot.clients.transmission.httpx.AsyncClient"):
            client = TransmissionClient(
                host="http://tr-host:9091",
                username="admin",
                password="secret",
            )
        client.client = AsyncMock(spec=httpx.AsyncClient)
        return client

    # -- __init__ --------------------------------------------------------

    @patch("bot.clients.transmission.httpx.AsyncClient")
    def test_init_with_credentials(self, mock_async_client):
        client = TransmissionClient(
            host="http://tr-host:9091",
            username="admin",
            password="secret",
        )
        # Verify AsyncClient was called with auth=BasicAuth(...)
        call_kwargs = mock_async_client.call_args
        auth_arg = call_kwargs.kwargs.get("auth") or call_kwargs[1].get("auth")
        assert auth_arg is not None
        assert isinstance(auth_arg, httpx.BasicAuth)

    @patch("bot.clients.transmission.httpx.AsyncClient")
    def test_init_without_credentials(self, mock_async_client):
        client = TransmissionClient(
            host="http://tr-host:9091",
            username="",
            password="",
        )
        call_kwargs = mock_async_client.call_args
        auth_arg = call_kwargs.kwargs.get("auth") or call_kwargs[1].get("auth")
        assert auth_arg is None

    # -- _rpc_request ----------------------------------------------------

    async def test_rpc_request_success(self, tr_client):
        tr_client.session_id = "valid_session"
        tr_client.client.post = AsyncMock(
            return_value=_httpx_response(
                json_data={"result": "success", "arguments": {}}
            )
        )
        data = await tr_client._rpc_request("torrent-get")
        assert data["result"] == "success"

    async def test_rpc_request_409_gets_session_id(self, tr_client):
        """409 response provides session id, retry succeeds."""
        resp_409 = _httpx_response(
            status_code=409,
            headers={"X-Transmission-Session-Id": "new_session_abc"},
            json_data={},
        )
        resp_200 = _httpx_response(
            json_data={"result": "success", "arguments": {}}
        )
        tr_client.client.post = AsyncMock(side_effect=[resp_409, resp_200])

        data = await tr_client._rpc_request("torrent-get")
        assert data["result"] == "success"
        assert tr_client.session_id == "new_session_abc"
        assert tr_client.client.post.await_count == 2

    async def test_rpc_request_409_no_header_raises(self, tr_client):
        """409 without X-Transmission-Session-Id header raises ConnectionError."""
        resp_409 = _httpx_response(
            status_code=409,
            headers={},
            json_data={},
        )
        tr_client.client.post = AsyncMock(return_value=resp_409)

        with pytest.raises(ConnectionError, match="未提供 X-Transmission-Session-Id"):
            await tr_client._rpc_request("torrent-get")

    async def test_rpc_request_non_success_result_raises(self, tr_client):
        """Non-success result field raises ConnectionError."""
        tr_client.session_id = "valid_session"
        tr_client.client.post = AsyncMock(
            return_value=_httpx_response(
                json_data={"result": "no method name"}
            )
        )
        with pytest.raises(ConnectionError, match="RPC 失败"):
            await tr_client._rpc_request("bad-method")

    # -- add_torrent_url -------------------------------------------------

    async def test_add_torrent_url_success(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={"result": "success", "arguments": {"torrent-added": {}}}
        )
        result = await tr_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is True
        tr_client._rpc_request.assert_awaited_once_with(
            "torrent-add", {"filename": "magnet:?xt=urn:btih:abc"}
        )

    async def test_add_torrent_url_failure(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            side_effect=ConnectionError("rpc fail")
        )
        result = await tr_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is False

    # -- add_torrent_file ------------------------------------------------

    async def test_add_torrent_file_success(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={"result": "success", "arguments": {"torrent-added": {}}}
        )
        torrent_data = b"\x00\x01\x02torrent_content"
        result = await tr_client.add_torrent_file(torrent_data, "test.torrent")
        assert result is True

        # Verify base64 encoding was used
        expected_metainfo = base64.b64encode(torrent_data).decode("ascii")
        tr_client._rpc_request.assert_awaited_once_with(
            "torrent-add", {"metainfo": expected_metainfo}
        )

    async def test_add_torrent_file_failure(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            side_effect=ConnectionError("rpc fail")
        )
        result = await tr_client.add_torrent_file(b"\x00", "test.torrent")
        assert result is False

    # -- get_tasks -------------------------------------------------------

    async def test_get_tasks(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={
                "result": "success",
                "arguments": {
                    "torrents": [
                        {"name": "Movie.mkv", "status": 4},
                        {"name": "Album.flac", "status": 0},
                    ]
                },
            }
        )
        tasks = await tr_client.get_tasks()
        assert len(tasks) == 2
        assert tasks[0]["name"] == "Movie.mkv"
        assert tasks[1]["name"] == "Album.flac"

    # -- test_connection -------------------------------------------------

    async def test_test_connection_success(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={
                "result": "success",
                "arguments": {"torrents": []},
            }
        )
        result = await tr_client.test_connection()
        assert result is True

    async def test_test_connection_failure(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            side_effect=ConnectionError("cannot connect")
        )
        result = await tr_client.test_connection()
        assert result is False

    # -- close -----------------------------------------------------------

    async def test_close(self, tr_client):
        tr_client.client.aclose = AsyncMock()
        await tr_client.close()
        tr_client.client.aclose.assert_awaited_once()
