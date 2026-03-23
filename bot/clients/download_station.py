"""群晖 Download Station API 客户端（兼容 DSM 6 v1 API 和 DSM 7 v2 API）"""

from __future__ import annotations

import json
import logging
from typing import List

import httpx

from bot.clients.base import DownloadClientBase

logger = logging.getLogger(__name__)


class DownloadStationClient(DownloadClientBase):
    """群晖 Download Station 下载客户端

    自动检测 API 版本：优先使用 DownloadStation2 (DSM 7)，
    不可用时降级到 DownloadStation (DSM 6)。
    """

    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.sid: str | None = None
        self.client = httpx.AsyncClient(timeout=30.0, verify=False)
        self._api_url = f"{self.host}/webapi/entry.cgi"
        self._use_v2: bool | None = None  # None = 未检测
        self._destination: str = ""

    async def _login(self) -> None:
        """登录 Download Station，获取 SID"""
        params = {
            "api": "SYNO.API.Auth",
            "version": "6",
            "method": "login",
            "account": self.username,
            "passwd": self.password,
            "session": "DownloadStation",
            "format": "sid",
        }
        resp = await self.client.get(self._api_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise ConnectionError(
                f"Download Station 登录失败: {data.get('error', {})}"
            )
        self.sid = data["data"]["sid"]
        logger.info("Download Station 登录成功")

    async def _ensure_login(self) -> None:
        if self.sid is None:
            await self._login()

    async def _detect_api_version(self) -> None:
        """检测 v2 API 是否可用，并获取默认下载目录"""
        if self._use_v2 is not None:
            return
        await self._ensure_login()
        params = {
            "api": "SYNO.DownloadStation2.Task",
            "version": "2",
            "method": "list",
            "offset": "0",
            "limit": "1",
            "_sid": self.sid,
        }
        resp = await self.client.get(self._api_url, params=params)
        data = resp.json()
        if data.get("success"):
            self._use_v2 = True
            logger.info("Download Station 使用 v2 API (DSM 7)")
            await self._fetch_default_destination()
        else:
            self._use_v2 = False
            self._destination = ""
            logger.info("Download Station 使用 v1 API (DSM 6)")

    async def _fetch_default_destination(self) -> None:
        """查询 Download Station 默认下载目录"""
        params = {
            "api": "SYNO.DownloadStation2.Settings.Location",
            "version": "1",
            "method": "get",
            "_sid": self.sid,
        }
        try:
            resp = await self.client.get(self._api_url, params=params)
            data = resp.json()
            if data.get("success"):
                self._destination = data["data"].get("default_destination", "")
                logger.info("Download Station 默认下载目录: %s", self._destination)
                return
        except Exception:
            logger.warning("获取默认下载目录失败，尝试备用方式")

        # 备用：从全局设置获取
        params2 = {
            "api": "SYNO.DownloadStation2.Settings.Global",
            "version": "1",
            "method": "get",
            "_sid": self.sid,
        }
        try:
            resp = await self.client.get(self._api_url, params=params2)
            data = resp.json()
            if data.get("success"):
                self._destination = data["data"].get("default_destination", "")
                logger.info("Download Station 下载目录(Global): %s", self._destination)
                return
        except Exception:
            pass

        # 最后兜底
        self._destination = ""
        logger.warning("无法获取默认下载目录，将使用空值")

    async def _api_request(self, method: str, **kwargs) -> dict:
        """发送 API 请求，SID 过期时自动重新登录重试"""
        await self._ensure_login()

        resp = await self.client.request(method, self._api_url, **kwargs)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            error_code = data.get("error", {}).get("code")
            # SID 过期，重新登录重试
            logger.warning("Download Station 请求失败 (error=%s)，重新登录", error_code)
            self.sid = None
            await self._login()
            if "params" in kwargs:
                kwargs["params"]["_sid"] = self.sid
            if "data" in kwargs and isinstance(kwargs["data"], dict):
                kwargs["data"]["_sid"] = self.sid
            resp = await self.client.request(method, self._api_url, **kwargs)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise ConnectionError(
                    f"Download Station 请求失败: {data.get('error', {})}"
                )
        return data

    # ------------------------------------------------------------------
    # 添加任务
    # ------------------------------------------------------------------

    async def add_torrent_url(self, url: str) -> bool:
        try:
            await self._detect_api_version()

            if self._use_v2:
                form_data = {
                    "api": "SYNO.DownloadStation2.Task",
                    "version": "2",
                    "method": "create",
                    "url": json.dumps([url]),
                    "destination": self._destination,
                    "type": "url",
                    "create_list": "false",
                    "_sid": self.sid,
                }
            else:
                form_data = {
                    "api": "SYNO.DownloadStation.Task",
                    "version": "1",
                    "method": "create",
                    "uri": url,
                    "_sid": self.sid,
                }

            await self._api_request("POST", data=form_data)
            logger.info("Download Station 添加 URL 任务成功")
            return True
        except Exception:
            logger.exception("Download Station 添加 URL 任务失败")
            return False

    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> bool:
        try:
            await self._ensure_login()
            await self._detect_api_version()

            if self._use_v2:
                form_data = {
                    "api": "SYNO.DownloadStation2.Task",
                    "version": "2",
                    "method": "create",
                    "destination": self._destination,
                    "type": "file",
                    "create_list": "false",
                    "_sid": self.sid,
                }
            else:
                form_data = {
                    "api": "SYNO.DownloadStation.Task",
                    "version": "1",
                    "method": "create",
                    "_sid": self.sid,
                }

            files = {"file": (filename, torrent_bytes, "application/x-bittorrent")}
            resp = await self.client.post(self._api_url, data=form_data, files=files)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("success"):
                logger.warning("Download Station 上传失败，重新登录重试")
                self.sid = None
                await self._login()
                form_data["_sid"] = self.sid
                files = {"file": (filename, torrent_bytes, "application/x-bittorrent")}
                resp = await self.client.post(self._api_url, data=form_data, files=files)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success"):
                    raise ConnectionError(
                        f"Download Station 上传失败: {data.get('error', {})}"
                    )

            logger.info("Download Station 添加文件任务成功: %s", filename)
            return True
        except Exception:
            logger.exception("Download Station 添加文件任务失败")
            return False

    # ------------------------------------------------------------------
    # 任务列表
    # ------------------------------------------------------------------

    async def get_tasks(self) -> List[dict]:
        await self._ensure_login()
        await self._detect_api_version()

        if self._use_v2:
            params = {
                "api": "SYNO.DownloadStation2.Task",
                "version": "2",
                "method": "list",
                "offset": "0",
                "limit": "100",
                "additional": '["detail"]',
                "_sid": self.sid,
            }
            data = await self._api_request("GET", params=params)
            tasks = data.get("data", {}).get("tasks", [])
            return [{"title": t.get("title", ""), **t} for t in tasks]
        else:
            params = {
                "api": "SYNO.DownloadStation.Task",
                "version": "1",
                "method": "list",
                "_sid": self.sid,
            }
            data = await self._api_request("GET", params=params)
            tasks = data.get("data", {}).get("tasks", [])
            return [{"title": t.get("title", ""), **t} for t in tasks]

    # ------------------------------------------------------------------
    # 连接测试
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            self.sid = None
            self._use_v2 = None
            await self._login()
            await self._detect_api_version()
            await self.get_tasks()
            return True
        except Exception:
            logger.exception("Download Station 连接测试失败")
            return False

    async def close(self):
        await self.client.aclose()
