"""Comprehensive tests for download client modules."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.clients import (
    DownloadStationClient,
    QBittorrentClient,
    TransmissionClient,
    create_download_client,
)
from bot.clients.download_station import (
    DS7FileManifest,
    DownloadStationAPIError,
    _map_file_station_destination,
    build_ds7_file_manifest,
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

def _v1_profile():
    """Create a v1 API profile for testing."""
    from bot.clients.download_station import _APIProfile
    return _APIProfile(
        version=1, list_api="SYNO.DownloadStation.Task", list_version="1",
        list_task_key="tasks", create_api="SYNO.DownloadStation.Task",
        create_version="1", create_url_field="uri",
        destination="", destination_required=False,
    )


def _v2_profile():
    """Create a v2 API profile for testing."""
    from bot.clients.download_station import _APIProfile
    return _APIProfile(
        version=2, list_api="SYNO.DownloadStation2.Task", list_version="2",
        list_task_key="task", create_api="SYNO.DownloadStation2.Task",
        create_version="2", create_url_field="url",
        destination="/volume1/downloads", destination_required=True,
    )


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

    async def test_file_station_request_uses_independent_session(self, ds_client):
        ds_client.sid = "download-station-sid"
        ds_client.client.get = AsyncMock(return_value=_httpx_response(
            json_data={"success": True, "data": {"sid": "file-station-sid"}},
        ))
        ds_client.client.request = AsyncMock(return_value=_httpx_response(
            json_data={"success": True, "data": {}},
        ))

        await ds_client._file_station_request(
            "GET", params={"api": "SYNO.FileStation.List"},
        )

        assert ds_client.sid == "download-station-sid"
        assert ds_client._file_station_sid == "file-station-sid"
        assert ds_client.client.get.await_args.kwargs["params"]["session"] == "FileStation"
        assert ds_client.client.request.await_args.kwargs["params"]["_sid"] == "file-station-sid"

    async def test_login_and_profile_initialization_are_singleflight(self, ds_client):
        async def login_once():
            await asyncio.sleep(0)
            ds_client.sid = "single-sid"

        ds_client._login = AsyncMock(side_effect=login_once)
        await asyncio.gather(*[ds_client._ensure_login() for _ in range(4)])
        assert ds_client._login.await_count == 1

        async def probe_once():
            await asyncio.sleep(0)
            return _v2_profile()

        ds_client._run_api_probe = AsyncMock(side_effect=probe_once)
        await asyncio.gather(*[ds_client._ensure_profile() for _ in range(4)])
        assert ds_client._run_api_probe.await_count == 1

    async def test_is_ds7_reuses_discovered_profile(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._run_api_probe = AsyncMock(return_value=_v2_profile())

        assert await ds_client.is_ds7() is True
        assert await ds_client.is_ds7() is True

        ds_client._run_api_probe.assert_awaited_once_with()

    async def test_is_ds7_returns_false_for_dsm6_profile(self, ds_client):
        ds_client._profile = _v1_profile()
        ds_client._run_api_probe = AsyncMock()

        assert await ds_client.is_ds7() is False

        ds_client._run_api_probe.assert_not_awaited()

    async def test_is_ds7_propagates_profile_probe_failure(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._run_api_probe = AsyncMock(
            side_effect=ConnectionError("probe failed")
        )

        with pytest.raises(ConnectionError, match="probe failed"):
            await ds_client.is_ds7()

        assert ds_client._profile is None

    async def test_file_station_login_is_singleflight(self, ds_client):
        async def login_once():
            await asyncio.sleep(0)
            ds_client._file_station_sid = "file-sid"

        ds_client._login_file_station = AsyncMock(side_effect=login_once)
        await asyncio.gather(*[
            ds_client._ensure_file_station_login() for _ in range(4)
        ])
        assert ds_client._login_file_station.await_count == 1

    # -- add_torrent_url -------------------------------------------------

    async def test_add_torrent_url_success(self, ds_client):
        ds_client._api_request = AsyncMock(
            return_value={"success": True, "data": {"task_id": ["dbid_99"]}}
        )
        ds_client._profile = _v1_profile()
        ds_client.sid = "existing_sid"
        result = await ds_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is not None
        ds_client._api_request.assert_awaited_once()

    async def test_add_torrent_url_failure(self, ds_client):
        ds_client._api_request = AsyncMock(side_effect=ConnectionError("fail"))
        ds_client._profile = _v1_profile()
        ds_client.sid = "existing_sid"
        result = await ds_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is None

    # -- add_torrent_file ------------------------------------------------

    async def test_add_torrent_file_success(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v1_profile()
        ds_client.client.post = AsyncMock(
            return_value=_httpx_response(json_data={"success": True, "data": {"task_id": ["dbid_55"]}})
        )
        result = await ds_client.add_torrent_file(b"\x00torrent", "test.torrent")
        assert result is not None
        call = ds_client.client.post.await_args
        assert "params" not in call.kwargs
        assert call.kwargs["data"] == {
            "api": "SYNO.DownloadStation.Task",
            "method": "create",
            "version": "1",
            "_sid": "existing_sid",
        }
        assert call.kwargs["files"] == {
            "file": (
                "test.torrent", b"\x00torrent", "application/x-bittorrent",
            ),
        }

    async def test_add_torrent_file_v2_uses_upload_field_indirection(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client.client.post = AsyncMock(
            return_value=_httpx_response(
                json_data={"success": True, "data": {"task_id": ["dbid_55"]}},
            )
        )

        result = await ds_client.add_torrent_file(
            b"d4:infode", "release.torrent",
        )

        assert result == "dbid_55"
        call = ds_client.client.post.await_args
        assert call.kwargs["params"] == {"_sid": "existing_sid"}
        assert call.kwargs["data"] == {
            "api": "SYNO.DownloadStation2.Task",
            "method": "create",
            "version": "2",
            "type": '"file"',
            "file": '["torrent"]',
            "destination": '"/volume1/downloads"',
            "create_list": "false",
        }
        assert call.kwargs["files"] == {
            "torrent": (
                "release.torrent", b"d4:infode", "application/x-bittorrent",
            ),
        }

    async def test_add_torrent_file_failure(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v1_profile()
        ds_client.client.post = AsyncMock(side_effect=Exception("network error"))
        result = await ds_client.add_torrent_file(b"\x00torrent", "test.torrent")
        assert result is None

    async def test_concurrent_file_upload_auth_refresh_is_singleflight(self, ds_client):
        ds_client.sid = "expired_sid"
        ds_client._profile = _v2_profile()
        expired_requests = 0
        both_expired = asyncio.Event()

        async def post_with_expired_sid(*args, params, data, files, **kwargs):
            nonlocal expired_requests
            assert data["type"] == '"file"'
            assert data["file"] == '["torrent"]'
            assert set(files) == {"torrent"}
            request_sid = params["_sid"]
            if request_sid == "expired_sid":
                expired_requests += 1
                if expired_requests == 2:
                    both_expired.set()
                await both_expired.wait()
                return _httpx_response(
                    json_data={"success": False, "error": {"code": 119}},
                )
            assert request_sid == "fresh_sid"
            return _httpx_response(
                json_data={"success": True, "data": {"task_id": ["dbid_55"]}},
            )

        async def login_once():
            await asyncio.sleep(0)
            ds_client.sid = "fresh_sid"

        ds_client.client.post = AsyncMock(side_effect=post_with_expired_sid)
        ds_client._login = AsyncMock(side_effect=login_once)

        results = await asyncio.gather(
            ds_client.add_torrent_file(b"one", "one.torrent"),
            ds_client.add_torrent_file(b"two", "two.torrent"),
        )

        assert results == ["dbid_55", "dbid_55"]
        assert ds_client._login.await_count == 1
        assert ds_client.client.post.await_count == 4

    # -- get_tasks -------------------------------------------------------

    async def test_get_tasks(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v1_profile()
        ds_client._api_request = AsyncMock(
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

    async def test_get_tasks_v2_normalizes_status(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(return_value={
            "success": True,
            "data": {
                "total": 3,
                "task": [
                    {"id": "t1", "title": "Downloading", "status": 2},
                    {"id": "t2", "title": "Paused", "status": 3},
                    {"id": "t3", "title": "Finished", "status": 5},
                ],
            },
        })

        tasks = await ds_client.get_tasks()

        assert [task["state"] for task in tasks] == [
            "downloading", "paused", "completed",
        ]
        assert tasks[1]["status_label"] == "已暂停"
        assert tasks[2]["status_label"] == "已完成"

    async def test_get_tasks_v2_reads_all_api_pages(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()

        def page(start, count):
            return [
                {"id": f"t{i}", "title": f"Task {i}", "status": 2}
                for i in range(start, start + count)
            ]

        ds_client._api_request = AsyncMock(side_effect=[
            {"success": True, "data": {"total": 250, "task": page(0, 100)}},
            {"success": True, "data": {"total": 250, "task": page(100, 100)}},
            {"success": True, "data": {"total": 250, "task": page(200, 50)}},
        ])

        tasks = await ds_client.get_tasks()

        assert len(tasks) == 250
        offsets = [
            call.kwargs["params"]["offset"]
            for call in ds_client._api_request.await_args_list
        ]
        assert offsets == ["0", "100", "200"]

    async def test_get_tasks_v2_stops_on_repeated_page(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        repeated = [
            {"id": f"t{i}", "title": f"Task {i}", "status": 2}
            for i in range(100)
        ]
        response = {"success": True, "data": {"total": 200, "task": repeated}}
        ds_client._api_request = AsyncMock(side_effect=[response, response])

        tasks = await ds_client.get_tasks()

        assert len(tasks) == 100
        assert ds_client._api_request.await_count == 2

    async def test_get_tasks_page_uses_ds7_filter_sort_and_total(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(return_value={
            "success": True,
            "data": {
                "total_count": "27",
                "task": [{"id": "t1", "title": "Paused", "status": 3}],
            },
        })

        page = await ds_client.get_tasks_page(
            -10,
            1000,
            statuses=[2, 3],
            status_inverse=True,
            sort_by="create_time",
            order="asc",
            additional=("detail",),
        )

        assert page.offset == 0
        assert page.total == 27
        assert page.tasks[0]["state"] == "paused"
        params = ds_client._api_request.await_args.kwargs["params"]
        assert params == {
            "api": "SYNO.DownloadStation2.Task",
            "version": "2",
            "method": "list",
            "offset": "0",
            "limit": "100",
            "sort_by": "create_time",
            "order": "ASC",
            "additional": '["detail"]',
            "status": "[2, 3]",
            "status_inverse": "true",
            "_sid": "existing_sid",
        }

    async def test_get_tasks_page_cache_and_force_refresh(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(return_value={
            "success": True,
            "data": {"total": 0, "task": []},
        })

        first = await ds_client.get_tasks_page(0, 20)
        second = await ds_client.get_tasks_page(0, 20)
        refreshed = await ds_client.get_tasks_page(0, 20, force_refresh=True)

        assert first is second
        assert refreshed == first
        assert ds_client._api_request.await_count == 2

    async def test_get_tasks_page_coalesces_concurrent_cache_misses(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()

        async def respond(*_args, **_kwargs):
            await asyncio.sleep(0)
            return {"success": True, "data": {"total": 0, "task": []}}

        ds_client._api_request = AsyncMock(side_effect=respond)

        pages = await asyncio.gather(
            ds_client.get_tasks_page(0, 20),
            ds_client.get_tasks_page(0, 20),
            ds_client.get_tasks_page(0, 20),
        )

        assert pages[0] is pages[1] is pages[2]
        ds_client._api_request.assert_awaited_once()

    async def test_get_task_uses_ds7_task_get(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(return_value={
            "success": True,
            "data": {
                "task": [{"id": "bt/42", "title": "Movie", "status": 2}],
            },
        })

        task = await ds_client.get_task("bt/42", additional=("detail",))

        assert task["id"] == "bt/42"
        assert task["state"] == "downloading"
        params = ds_client._api_request.await_args.kwargs["params"]
        assert params["method"] == "get"
        assert json.loads(params["id"]) == ["bt/42"]
        assert json.loads(params["additional"]) == ["detail"]

    async def test_get_task_returns_none_for_ds7_not_found(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(
            side_effect=DownloadStationAPIError(404, {"code": 404})
        )

        assert await ds_client.get_task("missing") is None

    async def test_pause_and_resume_use_json_task_id(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(
            return_value={"success": True, "data": {}}
        )

        assert await ds_client.pause_task("bt/42") is True
        assert await ds_client.resume_task("bt/42") is True

        calls = ds_client._api_request.await_args_list
        assert [call.kwargs["data"]["method"] for call in calls] == [
            "pause", "resume",
        ]
        assert [json.loads(call.kwargs["data"]["id"]) for call in calls] == [
            ["bt/42"], ["bt/42"],
        ]

    async def test_wait_for_task_status_polls_until_paused(self, ds_client):
        ds_client.get_task = AsyncMock(side_effect=[
            {"id": "bt-1", "status": 8},
            {"id": "bt-1", "status": 8},
            {"id": "bt-1", "status": 3},
        ])

        task = await ds_client.wait_for_task_status(
            "bt-1", (3,), timeout=1, poll_interval=0,
        )

        assert task["status"] == 3
        assert ds_client.get_task.await_count == 3
        assert all(
            call.kwargs["force_refresh"] is True
            for call in ds_client.get_task.await_args_list
        )

    # -- test_connection -------------------------------------------------

    async def test_test_connection_success(self, ds_client):
        ds_client.client.get = AsyncMock(
            return_value=_httpx_response(
                json_data={"success": True, "data": {"sid": "new_sid", "tasks": []}}
            )
        )
        ds_client._run_api_probe = AsyncMock(return_value=_v1_profile())
        ds_client._api_request = AsyncMock(
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

    async def test_concurrent_connection_tests_refresh_once(self, ds_client):
        ds_client.sid = "existing_sid"
        ds_client._profile = _v1_profile()

        async def login_once():
            await asyncio.sleep(0)
            ds_client.sid = "fresh_sid"

        async def probe_once():
            await asyncio.sleep(0)
            return _v1_profile()

        ds_client._login = AsyncMock(side_effect=login_once)
        ds_client._run_api_probe = AsyncMock(side_effect=probe_once)
        ds_client.get_tasks = AsyncMock(return_value=[])

        results = await asyncio.gather(
            ds_client.test_connection(),
            ds_client.test_connection(),
        )

        assert results == [True, True]
        assert ds_client._login.await_count == 1
        assert ds_client._run_api_probe.await_count == 1
        assert ds_client.get_tasks.await_count == 2

    # -- request errors and SID expiry -----------------------------------

    async def test_api_request_business_error_does_not_retry(self, ds_client):
        ds_client.sid = "old_sid"
        ds_client.client.request = AsyncMock(return_value=_httpx_response(
            json_data={"success": False, "error": {"code": 400}},
        ))
        ds_client.client.get = AsyncMock()

        with pytest.raises(DownloadStationAPIError) as exc_info:
            await ds_client._api_request("GET", params={"_sid": "old_sid"})

        assert exc_info.value.code == 400
        ds_client.client.request.assert_awaited_once()
        ds_client.client.get.assert_not_awaited()
        assert ds_client.sid == "old_sid"

    async def test_api_request_auth_106_relogs_and_retries_once(self, ds_client):
        ds_client.sid = "old_sid"
        ds_client.client.request = AsyncMock(side_effect=[
            _httpx_response(
                json_data={"success": False, "error": {"code": 106}},
            ),
            _httpx_response(
                json_data={"success": True, "data": {"result": "ok"}},
            ),
        ])
        ds_client.client.get = AsyncMock(return_value=_httpx_response(
            json_data={"success": True, "data": {"sid": "new_sid"}},
        ))

        result = await ds_client._api_request(
            "GET", params={"_sid": "old_sid"},
        )

        assert result["data"]["result"] == "ok"
        assert ds_client.sid == "new_sid"
        assert ds_client.client.request.await_count == 2
        assert ds_client.client.request.await_args.kwargs["params"]["_sid"] == "new_sid"

    async def test_cache_invalidation_rejects_inflight_stale_write(self, ds_client):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        call_count = 0

        async def loader():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_started.set()
                await release_first.wait()
                return "stale"
            return "fresh"

        stale_read = asyncio.create_task(ds_client._cached_task_value(
            ("task", "one"), loader, force_refresh=False,
        ))
        await first_started.wait()
        ds_client.invalidate_task_cache()
        fresh_read = asyncio.create_task(ds_client._cached_task_value(
            ("task", "one"), loader, force_refresh=True,
        ))
        release_first.set()

        assert await stale_read == "stale"
        assert await fresh_read == "fresh"
        assert call_count == 2
        assert await ds_client._cached_task_value(
            ("task", "one"), loader, force_refresh=False,
        ) == "fresh"
        assert call_count == 2

    # -- precise file manifests -----------------------------------------

    def test_build_file_manifest_preserves_unicode_and_multiple_files(self):
        task = {
            "id": "bt-1",
            "type": "BT",
            "additional": {
                "detail": {"destination": "/volume1/下载"},
            },
        }
        items = [
            {
                "name": "电影/正片.mkv",
                "size": 1000,
                "size_downloaded": 900,
                "wanted": True,
            },
            {
                "filename": "电影/字幕.zh-CN.srt",
                "size": 20,
                "size_downloaded": 0,
                "wanted": True,
            },
            {
                "name": "跳过.txt",
                "size": 10,
                "size_downloaded": 0,
                "wanted": False,
            },
        ]

        manifest = build_ds7_file_manifest(task, items)

        assert manifest.task_id == "bt-1"
        assert manifest.destination == "/volume1/下载"
        assert manifest.paths == (
            "/volume1/下载/电影/正片.mkv",
            "/volume1/下载/电影/字幕.zh-CN.srt",
        )
        assert manifest.total_size == 920
        assert len(manifest.fingerprint) == 64

    @pytest.mark.parametrize("name", [
        "../etc/passwd",
        "movie/../../outside.mkv",
        "/volume1/other/file.mkv",
        "movie\\outside.mkv",
        "movie\x00.mkv",
        ".",
        "./movie.mkv",
        "movie//outside.mkv",
    ])
    def test_build_file_manifest_rejects_unsafe_paths(self, name):
        task = {
            "id": "bt-1",
            "type": "bt",
            "additional": {
                "detail": {"destination": "/volume1/downloads"},
            },
        }

        with pytest.raises(ValueError, match="路径"):
            build_ds7_file_manifest(task, [{"name": name, "size": 1}])

    def test_build_file_manifest_accepts_shared_folder_destination(self):
        task = {
            "id": "bt-1",
            "type": "bt",
            "additional": {"detail": {"destination": "MOVIE"}},
        }

        manifest = build_ds7_file_manifest(
            task, [{"name": "Movie/movie.mkv", "size": 1}],
        )

        assert manifest.destination == "/MOVIE"
        assert manifest.paths == ("/MOVIE/Movie/movie.mkv",)

    @pytest.mark.parametrize("destination", [
        "//volume1/downloads",
        "/volume1//downloads",
        "/volume1/../downloads",
    ])
    def test_build_file_manifest_rejects_ambiguous_destinations(self, destination):
        task = {
            "id": "bt-1",
            "type": "bt",
            "additional": {"detail": {"destination": destination}},
        }

        with pytest.raises(ValueError, match="下载目录"):
            build_ds7_file_manifest(task, [{"name": "movie.mkv", "size": 1}])

    def test_map_real_destination_to_file_station_share(self):
        shares = [{
            "path": "/downloads",
            "additional": {"real_path": "/volume1/downloads"},
        }]

        assert _map_file_station_destination(
            "/volume1/downloads/Movies", shares,
        ) == "/downloads/Movies"
        assert _map_file_station_destination(
            "/downloads/Movies", shares,
        ) == "/downloads/Movies"

    def test_map_destination_uses_longest_real_path_prefix(self):
        shares = [
            {"path": "/data", "additional": {"real_path": "/volume1/data"}},
            {
                "path": "/movies",
                "additional": {"real_path": "/volume1/data/movies"},
            },
        ]

        assert _map_file_station_destination(
            "/volume1/data/movies/Film", shares,
        ) == "/movies/Film"

    def test_map_destination_rejects_missing_or_ambiguous_share(self):
        with pytest.raises(ValueError, match="无法将"):
            _map_file_station_destination("/volume1/downloads", [])
        duplicate_real_path = [
            {"path": "/one", "additional": {"real_path": "/volume1/data"}},
            {"path": "/two", "additional": {"real_path": "/volume1/data"}},
        ]
        with pytest.raises(ValueError, match="多个"):
            _map_file_station_destination("/volume1/data/file", duplicate_real_path)

    async def test_get_bt_task_files_reads_all_pages(self, ds_client):
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        first = [{"name": f"dir/{index:03}.mkv"} for index in range(100)]
        second = [{"name": "dir/100.mkv"}]
        ds_client._api_request = AsyncMock(side_effect=[
            {"success": True, "data": {"total": 101, "items": first}},
            {"success": True, "data": {"total": 101, "items": second}},
        ])

        items = await ds_client.get_bt_task_files("bt-1")

        assert len(items) == 101
        assert [
            call.kwargs["params"]["offset"]
            for call in ds_client._api_request.await_args_list
        ] == ["0", "100"]
        assert all(
            call.kwargs["params"]["task_id"] == "bt-1"
            for call in ds_client._api_request.await_args_list
        )

    async def test_get_bt_task_files_rejects_incomplete_short_page(self, ds_client):
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        first = [{"name": f"dir/{index:03}.mkv"} for index in range(100)]
        ds_client._api_request = AsyncMock(side_effect=[
            {"success": True, "data": {"total": 101, "items": first}},
            {"success": True, "data": {"total": 101, "items": []}},
        ])

        with pytest.raises(RuntimeError, match="文件清单不完整"):
            await ds_client.get_bt_task_files("bt-1")

        assert ds_client._api_request.await_count == 2

    async def test_get_bt_task_files_rejects_incomplete_loop_limit(self, ds_client):
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client._api_request = AsyncMock(return_value={
            "success": True,
            "data": {"total": 1001, "items": [{"name": "same.mkv"}]},
        })

        with patch("bot.clients.download_station._TASK_PAGE_LIMIT", 1):
            with pytest.raises(RuntimeError, match="文件清单不完整"):
                await ds_client.get_bt_task_files("bt-1")

        assert ds_client._api_request.await_count == 1000

    async def test_verify_file_manifest_requires_every_exact_path(self, ds_client):
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/下载",
            paths=("/volume1/下载/一.mkv", "/volume1/下载/二.srt"),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._file_station_request = AsyncMock(return_value={
            "success": True,
            "data": {"files": [{"path": "/volume1/下载/一.mkv"}]},
        })

        with pytest.raises(ValueError, match="无法确认全部"):
            await ds_client._verify_file_manifest_paths(manifest)

        params = ds_client._file_station_request.await_args.kwargs["params"]
        assert params["method"] == "getinfo"
        assert json.loads(params["path"]) == list(manifest.paths)

    async def test_verify_file_manifest_accepts_all_exact_paths(self, ds_client):
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/下载",
            paths=("/volume1/下载/一.mkv", "/volume1/下载/二.srt"),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._file_station_request = AsyncMock(return_value={
            "success": True,
            "data": {"files": [
                {"path": "/volume1/下载/二.srt", "additional": {"size": 2}},
                {"path": "/volume1/下载/一.mkv", "additional": {"size": 10}},
            ]},
        })

        await ds_client._verify_file_manifest_paths(manifest)

        ds_client._file_station_request.assert_awaited_once()

    async def test_prepare_manifest_maps_real_destination_before_verification(
        self, ds_client,
    ):
        task = {
            "id": "bt-1",
            "type": "bt",
            "additional": {"detail": {"destination": "/volume1/downloads"}},
        }
        ds_client.get_bt_task_files = AsyncMock(return_value=[
            {"name": "Movies/Film.mkv", "size": 12, "wanted": True},
        ])
        ds_client._file_station_request = AsyncMock(side_effect=[
            {"success": True, "data": {"shares": [{
                "path": "/downloads",
                "additional": {"real_path": "/volume1/downloads"},
            }]}},
            {"success": True, "data": {"files": [{
                "path": "/downloads/Movies/Film.mkv",
                "isdir": False,
                "additional": {
                    "real_path": "/volume1/downloads/Movies/Film.mkv",
                    "size": 12,
                },
            }]}},
        ])

        manifest = await ds_client.prepare_file_manifest(task)

        assert manifest.destination == "/downloads"
        assert manifest.paths == ("/downloads/Movies/Film.mkv",)
        assert manifest.real_paths == ("/volume1/downloads/Movies/Film.mkv",)
        assert manifest.file_sizes == (12,)
        list_share = ds_client._file_station_request.await_args_list[0].kwargs["params"]
        assert list_share["method"] == "list_share"
        assert list_share["onlywritable"] == "true"

    async def test_prepare_manifest_accepts_task_title_directory(self, ds_client):
        task = {
            "id": "bt-1",
            "title": "Release.Name",
            "type": "bt",
            "additional": {"detail": {"destination": "MOVIE"}},
        }
        ds_client.get_bt_task_files = AsyncMock(return_value=[
            {"name": "movie.mkv", "size": 12, "wanted": True},
        ])
        ds_client._file_station_request = AsyncMock(side_effect=[
            {"success": True, "data": {"shares": [{
                "path": "/MOVIE",
                "additional": {"real_path": "/volume1/MOVIE"},
            }]}},
            {"success": True, "data": {"files": [{
                "code": 408,
                "path": "/MOVIE/movie.mkv",
            }]}},
            {"success": True, "data": {"files": [{
                "path": "/MOVIE/Release.Name/movie.mkv",
                "isdir": False,
                "additional": {
                    "real_path": "/volume1/MOVIE/Release.Name/movie.mkv",
                    "size": 12,
                },
            }]}},
        ])

        manifest = await ds_client.prepare_file_manifest(task)

        assert manifest.destination == "/MOVIE/Release.Name"
        assert manifest.paths == ("/MOVIE/Release.Name/movie.mkv",)
        assert manifest.real_paths == (
            "/volume1/MOVIE/Release.Name/movie.mkv",
        )

    async def test_delete_file_manifest_polls_and_stops_operation(self, ds_client):
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/downloads",
            paths=("/volume1/downloads/Movie/movie.mkv",),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._verify_file_manifest_paths = AsyncMock()
        ds_client._file_station_request = AsyncMock(side_effect=[
            {"success": True, "data": {"taskid": "delete-7"}},
            {"success": True, "data": {
                "finished": False, "processed_num": 0, "total": 1,
            }},
            {"success": True, "data": {
                "finished": True, "processed_num": 1, "total": 1,
            }},
            {"success": True, "data": {}},
        ])

        result = await ds_client.delete_file_manifest(
            manifest, timeout=1, poll_interval=0,
        )

        assert result is True
        ds_client._verify_file_manifest_paths.assert_awaited_once_with(manifest)
        calls = ds_client._file_station_request.await_args_list
        assert [
            (call.args[0], (call.kwargs.get("data") or call.kwargs.get("params"))["method"])
            for call in calls
        ] == [
            ("POST", "start"),
            ("GET", "status"),
            ("GET", "status"),
            ("POST", "stop"),
        ]
        assert json.loads(calls[0].kwargs["data"]["path"]) == list(manifest.paths)
        assert calls[0].kwargs["data"]["recursive"] == "false"

    async def test_delete_file_manifest_status_failure_still_stops_operation(
        self, ds_client,
    ):
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/downloads",
            paths=("/volume1/downloads/movie.mkv",),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._verify_file_manifest_paths = AsyncMock()
        ds_client._file_station_request = AsyncMock(side_effect=[
            {"success": True, "data": {"taskid": "delete-7"}},
            DownloadStationAPIError(400, {"code": 400}),
            {"success": True, "data": {}},
        ])

        result = await ds_client.delete_file_manifest(
            manifest, timeout=1, poll_interval=0,
        )

        assert result is False
        assert ds_client._file_station_request.await_count == 3
        assert ds_client._file_station_request.await_args.kwargs["data"]["method"] == "stop"

    async def test_delete_file_manifest_rejects_incomplete_finished_count(
        self, ds_client,
    ):
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/downloads",
            paths=("/downloads/one.mkv", "/downloads/two.srt"),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client._verify_file_manifest_paths = AsyncMock()
        ds_client._file_station_request = AsyncMock(side_effect=[
            {"success": True, "data": {"taskid": "delete-8"}},
            {"success": True, "data": {
                "finished": True, "processed_num": 1, "total": 2,
            }},
            {"success": True, "data": {}},
        ])

        assert await ds_client.delete_file_manifest(
            manifest, timeout=1, poll_interval=0,
        ) is False
        assert ds_client._file_station_request.await_count == 3

    # -- delete_task -----------------------------------------------------

    async def test_delete_task_v1_success(self, ds_client):
        ds_client.sid = "sid"
        ds_client._profile = _v1_profile()
        ds_client._api_request = AsyncMock(return_value={"success": True, "data": {}})

        result = await ds_client.delete_task("task_123", delete_files=True)

        assert result is True
        call_data = ds_client._api_request.await_args.kwargs["data"]
        assert call_data["method"] == "delete"
        assert call_data["id"] == "task_123"

    async def test_delete_task_v2_keep_files_skips_manifest(self, ds_client):
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client.get_task = AsyncMock()
        ds_client.prepare_file_manifest = AsyncMock()
        ds_client._api_request = AsyncMock(return_value={"success": True, "data": {}})

        result = await ds_client.delete_task("bt-1", delete_files=False)

        assert result is True
        ds_client.get_task.assert_not_awaited()
        ds_client.prepare_file_manifest.assert_not_awaited()
        data = ds_client._api_request.await_args.kwargs["data"]
        assert data["method"] == "delete"
        assert json.loads(data["id"]) == ["bt-1"]
        assert data["force_complete"] == "false"

    async def test_delete_task_v2_deletes_verified_completed_bt_files(self, ds_client):
        task = {
            "id": "bt-1",
            "type": "bt",
            "status": 5,
            "additional": {"detail": {"destination": "/volume1/downloads"}},
        }
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/downloads",
            paths=("/volume1/downloads/movie.mkv",),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client.get_task = AsyncMock(return_value=task)
        ds_client.prepare_file_manifest = AsyncMock(return_value=manifest)
        ds_client.delete_file_manifest = AsyncMock(return_value=True)
        ds_client.pause_task = AsyncMock()
        ds_client._api_request = AsyncMock(return_value={"success": True, "data": {}})

        result = await ds_client.delete_task("bt-1", delete_files=True)

        assert result is True
        ds_client.prepare_file_manifest.assert_awaited_once_with(task)
        ds_client.delete_file_manifest.assert_awaited_once_with(manifest)
        ds_client.pause_task.assert_not_awaited()
        assert ds_client._api_request.await_args.kwargs["data"]["method"] == "delete"

    async def test_delete_task_v2_pauses_seeding_before_file_deletion(self, ds_client):
        task = {
            "id": "bt-1",
            "type": "bt",
            "status": 8,
            "additional": {"detail": {"destination": "/volume1/downloads"}},
        }
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/downloads",
            paths=("/volume1/downloads/movie.mkv",),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client.get_task = AsyncMock(return_value=task)
        ds_client.prepare_file_manifest = AsyncMock(return_value=manifest)
        ds_client.pause_task = AsyncMock(return_value=True)
        ds_client.wait_for_task_status = AsyncMock(return_value={
            **task, "status": 3,
        })
        ds_client.delete_file_manifest = AsyncMock(return_value=True)
        ds_client._api_request = AsyncMock(return_value={"success": True, "data": {}})

        assert await ds_client.delete_task("bt-1", delete_files=True) is True
        ds_client.pause_task.assert_awaited_once_with("bt-1")
        ds_client.wait_for_task_status.assert_awaited_once_with("bt-1", (3,))
        ds_client.delete_file_manifest.assert_awaited_once_with(manifest)

    @pytest.mark.parametrize(("task_type", "status"), [
        ("bt", 2),
        ("http", 5),
    ])
    async def test_delete_task_v2_rejects_unsafe_file_deletion(
        self, ds_client, task_type, status,
    ):
        task = {"id": "bt-1", "type": task_type, "status": status}
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client.get_task = AsyncMock(return_value=task)
        ds_client.prepare_file_manifest = AsyncMock()
        ds_client._api_request = AsyncMock()

        result = await ds_client.delete_task("bt-1", delete_files=True)

        assert result is False
        ds_client.prepare_file_manifest.assert_not_awaited()
        ds_client._api_request.assert_not_awaited()

    async def test_delete_task_v2_keeps_task_when_file_deletion_fails(self, ds_client):
        task = {
            "id": "bt-1",
            "type": "bt",
            "status": 5,
            "additional": {"detail": {"destination": "/volume1/downloads"}},
        }
        manifest = DS7FileManifest(
            task_id="bt-1",
            destination="/volume1/downloads",
            paths=("/volume1/downloads/movie.mkv",),
            total_size=12,
            fingerprint="f" * 64,
        )
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client.get_task = AsyncMock(return_value=task)
        ds_client.prepare_file_manifest = AsyncMock(return_value=manifest)
        ds_client.delete_file_manifest = AsyncMock(return_value=False)
        ds_client._api_request = AsyncMock()

        result = await ds_client.delete_task("bt-1", delete_files=True)

        assert result is False
        ds_client._api_request.assert_not_awaited()

    async def test_delete_task_v2_missing_task_is_idempotent_success(self, ds_client):
        ds_client.sid = "sid"
        ds_client._profile = _v2_profile()
        ds_client.get_task = AsyncMock(return_value=None)
        ds_client._api_request = AsyncMock()

        assert await ds_client.delete_task("missing", delete_files=True) is True
        ds_client._api_request.assert_not_awaited()

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
        assert result is not None

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
        assert result is None

    # -- add_torrent_file ------------------------------------------------

    async def test_add_torrent_file_success(self, qb_client):
        qb_client.logged_in = True
        qb_client.client.request = AsyncMock(
            return_value=_httpx_response(json_data={"status": "ok"})
        )
        result = await qb_client.add_torrent_file(b"\x00torrent", "test.torrent")
        assert result is not None

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

    # -- delete_task -----------------------------------------------------

    async def test_delete_task_deletes_files_by_default(self, qb_client):
        qb_client._request_with_retry = AsyncMock(
            return_value=_httpx_response(json_data={})
        )
        result = await qb_client.delete_task("abc123hash")
        assert result is True
        call_data = qb_client._request_with_retry.call_args[1]["data"]
        assert call_data["hashes"] == "abc123hash"
        assert call_data["deleteFiles"] == "true"

    async def test_delete_task_keep_files(self, qb_client):
        qb_client._request_with_retry = AsyncMock(
            return_value=_httpx_response(json_data={})
        )
        result = await qb_client.delete_task("abc123hash", delete_files=False)
        assert result is True
        call_data = qb_client._request_with_retry.call_args[1]["data"]
        assert call_data["deleteFiles"] == "false"


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
            return_value={"result": "success", "arguments": {"torrent-added": {"id": 42}}}
        )
        result = await tr_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result == "42"
        tr_client._rpc_request.assert_awaited_once_with(
            "torrent-add", {"filename": "magnet:?xt=urn:btih:abc"}
        )

    async def test_add_torrent_url_failure(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            side_effect=ConnectionError("rpc fail")
        )
        result = await tr_client.add_torrent_url("magnet:?xt=urn:btih:abc")
        assert result is None

    # -- add_torrent_file ------------------------------------------------

    async def test_add_torrent_file_success(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={"result": "success", "arguments": {"torrent-added": {"id": 99}}}
        )
        torrent_data = b"\x00\x01\x02torrent_content"
        result = await tr_client.add_torrent_file(torrent_data, "test.torrent")
        assert result == "99"

        expected_metainfo = base64.b64encode(torrent_data).decode("ascii")
        tr_client._rpc_request.assert_awaited_once_with(
            "torrent-add", {"metainfo": expected_metainfo}
        )

    async def test_add_torrent_file_failure(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            side_effect=ConnectionError("rpc fail")
        )
        result = await tr_client.add_torrent_file(b"\x00", "test.torrent")
        assert result is None

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

    # -- delete_task -----------------------------------------------------

    async def test_delete_task_deletes_files_by_default(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={"result": "success", "arguments": {}}
        )
        result = await tr_client.delete_task("42")
        assert result is True
        call_args = tr_client._rpc_request.call_args[0]
        assert call_args[0] == "torrent-remove"
        assert call_args[1]["delete-local-data"] is True

    async def test_delete_task_keep_files(self, tr_client):
        tr_client._rpc_request = AsyncMock(
            return_value={"result": "success", "arguments": {}}
        )
        result = await tr_client.delete_task("42", delete_files=False)
        assert result is True
        call_args = tr_client._rpc_request.call_args[0]
        assert call_args[1]["delete-local-data"] is False

    # -- close -----------------------------------------------------------

    async def test_close(self, tr_client):
        tr_client.client.aclose = AsyncMock()
        await tr_client.close()
        tr_client.client.aclose.assert_awaited_once()
