"""群晖 Download Station API 客户端（兼容 DSM 6 v1 API 和 DSM 7 v2 API）

启动时通过 API 自检发现实际可用的端点、字段名和参数要求，
而非硬编码假设。所有发现结果缓存在实例属性中。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

from bot.clients.base import DownloadClientBase

logger = logging.getLogger(__name__)


@dataclass
class _APIProfile:
    """API 自检发现的接口配置"""
    version: int = 0                     # 2 = DSM 7, 1 = DSM 6
    # 任务列表
    list_api: str = ""                   # e.g. "SYNO.DownloadStation2.Task"
    list_version: str = "2"
    list_task_key: str = "task"          # 响应中任务列表的 key
    # 创建任务
    create_api: str = ""
    create_version: str = "2"
    create_url_field: str = "url"        # v2="url", v1="uri"
    destination: str = ""                # v2 必需的下载目录
    destination_required: bool = False


class DownloadStationClient(DownloadClientBase):
    """群晖 Download Station 下载客户端

    首次使用时执行 API 自检（_run_api_probe），自动发现：
    - v1 还是 v2 可用
    - list 接口的响应字段名（task vs tasks）
    - create 接口的 URL 字段名（url vs uri）
    - destination 是否必需及其默认值
    """

    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.sid: str | None = None
        self.client = httpx.AsyncClient(
            timeout=30.0, verify=host.startswith("https://"),
        )
        self._api_url = f"{self.host}/webapi/entry.cgi"
        self._profile: Optional[_APIProfile] = None  # None = 未检测

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------

    async def _login(self) -> None:
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

    # ------------------------------------------------------------------
    # API 自检（首次调用时执行一次）
    # ------------------------------------------------------------------

    async def _ensure_profile(self) -> None:
        if self._profile is not None:
            return
        await self._ensure_login()
        self._profile = await self._run_api_probe()

    async def _run_api_probe(self) -> _APIProfile:
        """探测 DS API 实际行为，返回可用配置。"""
        profile = _APIProfile()

        # --- 1. 尝试 v2 list ---
        v2_list = await self._probe_request({
            "api": "SYNO.DownloadStation2.Task",
            "version": "2", "method": "list",
            "offset": "0", "limit": "1", "_sid": self.sid,
        })
        if v2_list is not None:
            profile.version = 2
            profile.list_api = "SYNO.DownloadStation2.Task"
            profile.list_version = "2"
            profile.create_api = "SYNO.DownloadStation2.Task"
            profile.create_version = "2"
            # v2 API 字段名固定为 "url"（已验证）
            profile.create_url_field = "url"

            # 发现任务列表 key：尝试 "task"（单数）和 "tasks"（复数）
            v2_data = v2_list.get("data", {})
            if "task" in v2_data:
                profile.list_task_key = "task"
            elif "tasks" in v2_data:
                profile.list_task_key = "tasks"
            else:
                profile.list_task_key = "task"
            logger.info("自检: v2 list 可用, 任务key='%s'", profile.list_task_key)

            # 获取默认下载目录
            profile.destination = await self._probe_destination()
            profile.destination_required = True

            logger.info(
                "自检完成: v2, url_field='%s', destination='%s'",
                profile.create_url_field, profile.destination,
            )
            return profile

        # --- 2. 降级到 v1 ---
        v1_list = await self._probe_request({
            "api": "SYNO.DownloadStation.Task",
            "version": "1", "method": "list", "_sid": self.sid,
        })
        if v1_list is not None:
            profile.version = 1
            profile.list_api = "SYNO.DownloadStation.Task"
            profile.list_version = "1"
            profile.create_api = "SYNO.DownloadStation.Task"
            profile.create_version = "1"
            profile.list_task_key = "tasks"
            profile.create_url_field = "uri"
            profile.destination_required = False
            logger.info("自检完成: v1 API")
            return profile

        # --- 3. 都不行，用 v2 默认值（create 可能仍可用）---
        logger.warning("自检: list 接口均不可用，使用 v2 默认配置")
        profile.version = 2
        profile.list_api = "SYNO.DownloadStation2.Task"
        profile.list_version = "2"
        profile.create_api = "SYNO.DownloadStation2.Task"
        profile.create_version = "2"
        profile.create_url_field = "url"
        profile.destination = await self._probe_destination()
        profile.destination_required = True
        return profile

    async def _probe_request(self, params: dict) -> Optional[dict]:
        """发送探测请求，成功返回 JSON dict，失败返回 None。"""
        try:
            resp = await self.client.get(self._api_url, params=params)
            data = resp.json()
            if data.get("success"):
                return data
            logger.debug("探测失败: %s → %s", params.get("api"), data)
        except Exception as e:
            logger.debug("探测异常: %s → %s", params.get("api"), e)
        return None

    async def _probe_destination(self) -> str:
        """查询默认下载目录。"""
        for api in (
            "SYNO.DownloadStation2.Settings.Location",
            "SYNO.DownloadStation2.Settings.Global",
        ):
            result = await self._probe_request({
                "api": api, "version": "1",
                "method": "get", "_sid": self.sid,
            })
            if result:
                dest = result.get("data", {}).get("default_destination", "")
                if dest:
                    logger.info("自检: 默认下载目录='%s' (via %s)", dest, api)
                    return dest

        logger.warning("自检: 无法获取默认下载目录")
        return ""

    # ------------------------------------------------------------------
    # 通用请求（带 SID 过期重试）
    # ------------------------------------------------------------------

    async def _api_request(self, method: str, **kwargs) -> dict:
        await self._ensure_login()

        resp = await self.client.request(method, self._api_url, **kwargs)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            error_code = data.get("error", {}).get("code")
            logger.warning("DS 请求失败 (error=%s)，重新登录", error_code)
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
                    f"DS 请求失败: {data.get('error', {})}"
                )
        return data

    # ------------------------------------------------------------------
    # 添加任务
    # ------------------------------------------------------------------

    def _extract_task_id(self, data: dict) -> str:
        """从 create 响应中提取 task_id，无则返回空字符串。"""
        task_ids = data.get("data", {}).get("task_id", [])
        if task_ids and isinstance(task_ids, list):
            return task_ids[0]
        return ""

    async def add_torrent_url(self, url: str) -> Optional[str]:
        try:
            await self._ensure_profile()
            p = self._profile

            form_data = {
                "api": p.create_api,
                "version": p.create_version,
                "method": "create",
                "_sid": self.sid,
            }

            if p.version == 2:
                form_data[p.create_url_field] = json.dumps([url])
                form_data["type"] = "url"
                form_data["create_list"] = "false"
                if p.destination_required and p.destination:
                    form_data["destination"] = p.destination
            else:
                form_data["uri"] = url

            data = await self._api_request("POST", data=form_data)
            task_id = self._extract_task_id(data)
            logger.info("DS 添加 URL 任务成功, task_id=%s", task_id)
            return task_id
        except Exception:
            logger.exception("DS 添加 URL 任务失败")
            return None

    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> Optional[str]:
        try:
            await self._ensure_login()
            await self._ensure_profile()
            p = self._profile

            form_data = {
                "api": p.create_api,
                "version": p.create_version,
                "method": "create",
                "_sid": self.sid,
            }

            if p.version == 2:
                form_data["type"] = "file"
                form_data["create_list"] = "false"
                if p.destination_required and p.destination:
                    form_data["destination"] = p.destination

            files = {"file": (filename, torrent_bytes, "application/x-bittorrent")}
            resp = await self.client.post(self._api_url, data=form_data, files=files)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("success"):
                logger.warning("DS 上传失败，重新登录重试")
                self.sid = None
                await self._login()
                form_data["_sid"] = self.sid
                files = {"file": (filename, torrent_bytes, "application/x-bittorrent")}
                resp = await self.client.post(self._api_url, data=form_data, files=files)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success"):
                    raise ConnectionError(f"DS 上传失败: {data.get('error', {})}")

            task_id = self._extract_task_id(data)
            logger.info("DS 添加文件任务成功: %s, task_id=%s", filename, task_id)
            return task_id
        except Exception:
            logger.exception("DS 添加文件任务失败")
            return None

    # ------------------------------------------------------------------
    # 任务列表
    # ------------------------------------------------------------------

    async def get_tasks(self) -> List[dict]:
        await self._ensure_login()
        await self._ensure_profile()
        p = self._profile

        params = {
            "api": p.list_api,
            "version": p.list_version,
            "method": "list",
            "_sid": self.sid,
        }
        if p.version == 2:
            params["offset"] = "0"
            params["limit"] = "100"
            params["additional"] = '["detail","transfer"]'

        data = await self._api_request("GET", params=params)
        tasks = data.get("data", {}).get(p.list_task_key, [])
        return [{"title": t.get("title", ""), **t} for t in tasks]

    # ------------------------------------------------------------------
    # 删除任务
    # ------------------------------------------------------------------

    async def delete_task(self, task_id: str, delete_files: bool = True) -> bool:
        # 注意：Download Station API 不支持通过接口控制是否删除文件，
        # delete_files 参数在此客户端中被忽略。是否删除文件取决于 DS 面板设置。
        try:
            await self._ensure_login()
            await self._ensure_profile()
            p = self._profile

            if p.version == 2:
                form_data = {
                    "api": "SYNO.DownloadStation2.Task",
                    "version": "2",
                    "method": "delete",
                    "id": json.dumps([task_id]),
                    "force_complete": "false",
                    "_sid": self.sid,
                }
            else:
                form_data = {
                    "api": "SYNO.DownloadStation.Task",
                    "version": "1",
                    "method": "delete",
                    "id": task_id,
                    "_sid": self.sid,
                }

            await self._api_request("POST", data=form_data)
            logger.info("DS 删除任务成功: %s", task_id)
            return True
        except Exception:
            # 任务可能已被其他人删除，检查是否还存在
            try:
                tasks = await self.get_tasks()
                if not any(t.get("id") == task_id for t in tasks):
                    logger.info("DS 任务已不存在，视为删除成功: %s", task_id)
                    return True
            except Exception:
                pass
            logger.exception("DS 删除任务失败: %s", task_id)
            return False

    # ------------------------------------------------------------------
    # 连接测试
    # ------------------------------------------------------------------

    async def test_connection(self) -> bool:
        try:
            self.sid = None
            self._profile = None
            await self._login()
            await self._ensure_profile()
            await self.get_tasks()
            return True
        except Exception:
            logger.exception("DS 连接测试失败")
            return False

    async def close(self):
        await self.client.aclose()
