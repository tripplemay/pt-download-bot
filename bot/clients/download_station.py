"""群晖 Download Station API 客户端（兼容 DSM 6 v1 API 和 DSM 7 v2 API）

启动时通过 API 自检发现实际可用的端点、字段名和参数要求，
而非硬编码假设。所有发现结果缓存在实例属性中。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence, TypeVar

import httpx

from bot.clients.base import DownloadClientBase

logger = logging.getLogger(__name__)

_AUTH_ERROR_CODES = frozenset({106, 107, 119})
_TASK_CACHE_TTL = 4.0
_TASK_CACHE_MAX_ENTRIES = 128
_TASK_PAGE_LIMIT = 100
_FILE_DELETE_BATCH_SIZE = 100
_T = TypeVar("_T")


_DS7_STATUS_INFO = {
    1: ("waiting", "等待中"),
    2: ("downloading", "下载中"),
    3: ("paused", "已暂停"),
    4: ("processing", "完成处理中"),
    5: ("completed", "已完成"),
    6: ("processing", "校验中"),
    7: ("completed", "准备做种"),
    8: ("completed", "做种中"),
    9: ("waiting", "等待文件托管服务"),
    10: ("processing", "解压中"),
    11: ("waiting", "预处理中"),
    12: ("waiting", "等待预处理"),
    13: ("processing", "下载完成，正在收尾"),
    14: ("processing", "后处理中"),
    15: ("waiting", "等待验证码"),
    101: ("error", "下载错误"),
    102: ("error", "链接已失效"),
    103: ("error", "下载目录不存在"),
    104: ("error", "无权访问下载目录"),
    105: ("error", "磁盘空间不足"),
    106: ("error", "用户配额已满"),
    107: ("error", "连接超时"),
    108: ("error", "文件超过文件系统大小限制"),
    109: ("error", "文件超过临时目录大小限制"),
    110: ("error", "文件超过目标目录大小限制"),
    111: ("error", "加密文件名过长"),
    112: ("error", "文件名过长"),
    113: ("error", "种子任务重复"),
    114: ("error", "文件不存在"),
    115: ("error", "需要高级账号"),
    116: ("error", "不支持的任务类型"),
    117: ("error", "不支持该 FTP 加密类型"),
    118: ("error", "解压失败"),
    119: ("error", "解压密码错误"),
    120: ("error", "压缩文件无效"),
    121: ("error", "解压配额不足"),
    122: ("error", "解压磁盘空间不足"),
    123: ("error", "种子文件无效"),
    124: ("error", "需要下载账号"),
    125: ("error", "服务暂不可用，请稍后重试"),
    126: ("error", "加密任务错误"),
    127: ("error", "缺少 Python 运行环境"),
    128: ("error", "私有视频无法下载"),
    129: ("error", "解压目录不存在"),
    130: ("error", "NZB 文章缺失"),
    131: ("error", "ED2K 链接重复"),
    132: ("error", "目标文件重复"),
    133: ("error", "PAR2 修复失败"),
    134: ("error", "下载账号或密码无效"),
}


def get_ds7_status_info(status) -> tuple[str, str]:
    """返回 DSM 7 Task v2 状态的 (规范状态, 中文说明)。"""
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "unknown", "未知状态"

    info = _DS7_STATUS_INFO.get(code)
    if info:
        return info
    if code >= 101:
        return "error", f"下载错误 ({code})"
    return "unknown", f"未知状态 ({code})"


def normalize_ds7_task(task: dict) -> dict:
    """补充 /status 使用的稳定状态字段，同时保留 DS 原始响应。"""
    normalized = {"title": task.get("title", ""), **task}
    state, label = get_ds7_status_info(task.get("status"))
    normalized["state"] = state
    normalized["status_label"] = label
    return normalized


class DownloadStationAPIError(ConnectionError):
    """Download Station 返回的业务或权限错误。"""

    def __init__(self, code: int | None, payload: dict):
        self.code = code
        self.payload = payload
        super().__init__(f"DS 请求失败: {payload}")


@dataclass(frozen=True)
class DS7TaskPage:
    tasks: tuple[dict, ...]
    total: int
    offset: int


@dataclass(frozen=True)
class DS7FileManifest:
    task_id: str
    destination: str
    paths: tuple[str, ...]
    total_size: int
    fingerprint: str
    real_paths: tuple[str, ...] = ()
    file_sizes: tuple[int, ...] = ()


def _create_ds7_file_manifest(
    *,
    task_id: str,
    destination: str,
    paths: Sequence[str],
    total_size: int,
    real_paths: Sequence[str] = (),
    file_sizes: Sequence[int] = (),
) -> DS7FileManifest:
    path_tuple = tuple(paths)
    real_path_tuple = tuple(real_paths)
    file_size_tuple = tuple(file_sizes)
    if real_path_tuple and len(real_path_tuple) != len(path_tuple):
        raise ValueError("真实路径清单长度不一致")
    if file_size_tuple and len(file_size_tuple) != len(path_tuple):
        raise ValueError("文件大小清单长度不一致")
    fingerprint_data = json.dumps(
        {
            "task_id": task_id,
            "destination": destination,
            "paths": path_tuple,
            "total_size": total_size,
            "real_paths": real_path_tuple,
            "file_sizes": file_size_tuple,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DS7FileManifest(
        task_id=task_id,
        destination=destination,
        paths=path_tuple,
        total_size=total_size,
        fingerprint=hashlib.sha256(fingerprint_data).hexdigest(),
        real_paths=real_path_tuple,
        file_sizes=file_size_tuple,
    )


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_ds7_file_manifest(task: dict, items: Sequence[dict]) -> DS7FileManifest:
    """从 DS7 返回的 BT 文件清单构建精确、受限的 File Station 路径。"""
    task_id = str(task.get("id") or "")
    if not task_id or str(task.get("type") or "").lower() != "bt":
        raise ValueError("仅支持带任务 ID 的 BT 任务")

    detail = (task.get("additional") or {}).get("detail") or {}
    destination = str(detail.get("destination") or "").strip()
    # DSM 7 may return a File Station shared-folder name (for example
    # "MOVIE") rather than an absolute path. Normalize it before applying
    # the same component validation used for absolute destinations.
    if destination and not destination.startswith("/"):
        destination = "/" + destination
    if (
        not destination
        or not destination.startswith("/")
        or destination.startswith("//")
        or "\x00" in destination
        or "\\" in destination
    ):
        raise ValueError("下载目录无效")

    destination_parts = tuple(destination.split("/")[1:])
    if not destination_parts or any(part in ("", ".", "..") for part in destination_parts):
        raise ValueError("下载目录不安全")
    base_path = "/" + "/".join(destination_parts)

    paths = []
    file_sizes = []
    total_size = 0
    for item in items:
        name = str(item.get("name") or item.get("filename") or "")
        if not name or name.startswith("/") or "\x00" in name or "\\" in name:
            raise ValueError("任务文件路径无效")
        relative_parts = tuple(name.split("/"))
        if any(part in ("", ".", "..") for part in relative_parts):
            raise ValueError("任务文件路径越界")

        downloaded = max(0, _safe_int(item.get("size_downloaded")))
        size = max(0, _safe_int(item.get("size")))
        if item.get("wanted") is False and downloaded == 0:
            continue

        paths.append(base_path + "/" + "/".join(relative_parts))
        file_sizes.append(size)
        total_size += downloaded or size

    unique_paths = tuple(dict.fromkeys(paths))
    if not unique_paths:
        raise ValueError("任务没有可验证的已下载文件")
    if len(unique_paths) != len(paths):
        raise ValueError("任务文件清单包含重复路径")

    return _create_ds7_file_manifest(
        task_id=task_id,
        destination=base_path,
        paths=unique_paths,
        total_size=total_size,
        file_sizes=file_sizes,
    )


def _map_file_station_destination(destination: str, shares: Sequence[dict]) -> str:
    """Map a DS real/virtual destination to one unambiguous File Station path."""
    candidates: list[tuple[int, str]] = []
    for share in shares:
        virtual_path = str(share.get("path") or "").rstrip("/")
        real_path = str((share.get("additional") or {}).get("real_path") or "").rstrip("/")
        if not virtual_path.startswith("/") or virtual_path.startswith("//"):
            continue
        for source_path in dict.fromkeys((virtual_path, real_path)):
            if not source_path or not source_path.startswith("/"):
                continue
            if destination != source_path and not destination.startswith(source_path + "/"):
                continue
            mapped = virtual_path + destination[len(source_path):]
            candidates.append((len(source_path), mapped))

    if not candidates:
        raise ValueError("无法将下载目录映射到 File Station 共享文件夹")
    longest_prefix = max(length for length, _ in candidates)
    mapped_paths = {
        mapped for length, mapped in candidates if length == longest_prefix
    }
    if len(mapped_paths) != 1:
        raise ValueError("下载目录对应多个 File Station 共享文件夹")

    mapped = mapped_paths.pop()
    parts = mapped.split("/")[1:] if mapped.startswith("/") else []
    if (
        not parts
        or mapped.startswith("//")
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError("File Station 下载目录映射无效")
    return mapped


def _map_file_station_real_destination(
    destination: str,
    shares: Sequence[dict],
) -> str:
    candidates: list[tuple[int, str]] = []
    for share in shares:
        virtual_path = str(share.get("path") or "").rstrip("/")
        real_path = str((share.get("additional") or {}).get("real_path") or "").rstrip("/")
        if not virtual_path or not real_path:
            continue
        if destination != virtual_path and not destination.startswith(virtual_path + "/"):
            continue
        candidates.append((
            len(virtual_path),
            real_path + destination[len(virtual_path):],
        ))

    if not candidates:
        raise ValueError("File Station 未返回下载目录的真实路径")
    longest_prefix = max(length for length, _ in candidates)
    real_paths = {
        path for length, path in candidates if length == longest_prefix
    }
    if len(real_paths) != 1:
        raise ValueError("File Station 下载目录真实路径不唯一")
    real_path = real_paths.pop()
    parts = real_path.split("/")[1:] if real_path.startswith("/") else []
    if (
        not parts
        or real_path.startswith("//")
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError("File Station 下载目录真实路径无效")
    return real_path


def _remap_file_manifest(
    manifest: DS7FileManifest,
    destination: str,
    real_destination: str,
) -> DS7FileManifest:
    source_prefix = manifest.destination + "/"
    if not all(path.startswith(source_prefix) for path in manifest.paths):
        raise ValueError("文件清单不属于下载目录")
    mapped_paths = tuple(
        destination + path[len(manifest.destination):]
        for path in manifest.paths
    )
    real_paths = tuple(
        real_destination + path[len(manifest.destination):]
        for path in manifest.paths
    )
    return _create_ds7_file_manifest(
        task_id=manifest.task_id,
        destination=destination,
        paths=mapped_paths,
        total_size=manifest.total_size,
        real_paths=real_paths,
        file_sizes=manifest.file_sizes,
    )


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
        self._file_station_sid: str | None = None
        self.client = httpx.AsyncClient(
            timeout=30.0, verify=host.startswith("https://"),
        )
        self._api_url = f"{self.host}/webapi/entry.cgi"
        self._profile: Optional[_APIProfile] = None  # None = 未检测
        self._task_cache: dict[tuple, tuple[float, object]] = {}
        self._task_cache_locks: dict[tuple, asyncio.Lock] = {}
        self._task_cache_generation = 0
        # Lazily constructed inside the running loop (Python 3.9 binds locks
        # created during synchronous startup to the wrong loop).
        self._login_lock: asyncio.Lock | None = None
        self._file_station_login_lock: asyncio.Lock | None = None
        self._profile_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------

    async def _login_session(self, session: str) -> str:
        params = {
            "api": "SYNO.API.Auth",
            "version": "6",
            "method": "login",
            "account": self.username,
            "passwd": self.password,
            "session": session,
            "format": "sid",
        }
        resp = await self.client.get(self._api_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise ConnectionError(
                f"{session} 登录失败: {data.get('error', {})}"
            )
        return data["data"]["sid"]

    async def _login(self) -> None:
        self.sid = await self._login_session("DownloadStation")
        logger.info("Download Station 登录成功")

    async def _login_file_station(self) -> None:
        self._file_station_sid = await self._login_session("FileStation")
        logger.info("File Station 登录成功")

    async def _refresh_login(self, observed_sid: str | None) -> None:
        """Refresh the DS session once for all callers that observed one SID."""
        if self._login_lock is None:
            self._login_lock = asyncio.Lock()
        async with self._login_lock:
            if self.sid is not None and self.sid != observed_sid:
                return
            self.sid = None
            await self._login()

    async def _ensure_login(self) -> None:
        if self.sid is not None:
            return
        await self._refresh_login(None)

    async def _refresh_file_station_login(
        self,
        observed_sid: str | None,
    ) -> None:
        """Refresh the File Station session once for callers sharing one SID."""
        if self._file_station_login_lock is None:
            self._file_station_login_lock = asyncio.Lock()
        async with self._file_station_login_lock:
            if (
                self._file_station_sid is not None
                and self._file_station_sid != observed_sid
            ):
                return
            self._file_station_sid = None
            await self._login_file_station()

    async def _ensure_file_station_login(self) -> None:
        if self._file_station_sid is not None:
            return
        await self._refresh_file_station_login(None)

    # ------------------------------------------------------------------
    # API 自检（首次调用时执行一次）
    # ------------------------------------------------------------------

    async def _ensure_profile(self) -> None:
        if self._profile is not None:
            return
        if self._profile_lock is None:
            self._profile_lock = asyncio.Lock()
        async with self._profile_lock:
            if self._profile is not None:
                return
            await self._ensure_login()
            self._profile = await self._run_api_probe()

    async def is_ds7(self) -> bool:
        """Return whether the discovered Download Station API is DSM 7 v2."""
        await self._ensure_profile()
        return self._profile is not None and self._profile.version == 2

    async def _refresh_profile_for_connection_test(self) -> None:
        """Refresh login/profile once for concurrent connection tests."""
        requested_sid = self.sid
        requested_profile = self._profile
        if self._profile_lock is None:
            self._profile_lock = asyncio.Lock()
        async with self._profile_lock:
            if self.sid != requested_sid or self._profile is not requested_profile:
                await self._ensure_login()
                if self._profile is None:
                    self._profile = await self._run_api_probe()
                return

            await self._refresh_login(requested_sid)
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

    @staticmethod
    def _set_request_sid(kwargs: dict, sid: str) -> None:
        for field in ("params", "data"):
            payload = kwargs.get(field)
            if isinstance(payload, dict):
                payload["_sid"] = sid

    async def _session_api_request(
        self,
        method: str,
        *,
        sid_attr: str,
        login: Callable[[], Awaitable[None]],
        refresh_login: Callable[[str | None], Awaitable[None]],
        session_label: str,
        **kwargs,
    ) -> dict:
        if getattr(self, sid_attr) is None:
            await login()
        sid = getattr(self, sid_attr)
        if not sid:
            raise ConnectionError(f"{session_label} 登录未返回 SID")
        self._set_request_sid(kwargs, sid)
        resp = await self.client.request(method, self._api_url, **kwargs)
        resp.raise_for_status()
        data = resp.json()

        if data.get("success"):
            return data

        error = data.get("error", {})
        error_code = error.get("code")
        if error_code not in _AUTH_ERROR_CODES:
            raise DownloadStationAPIError(error_code, error)

        logger.warning(
            "%s 会话失效 (error=%s)，重新登录后重试一次",
            session_label,
            error_code,
        )
        await refresh_login(sid)
        sid = getattr(self, sid_attr)
        if not sid:
            raise ConnectionError(f"{session_label} 重新登录未返回 SID")
        self._set_request_sid(kwargs, sid)
        resp = await self.client.request(method, self._api_url, **kwargs)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            error = data.get("error", {})
            raise DownloadStationAPIError(error.get("code"), error)
        return data

    async def _api_request(self, method: str, **kwargs) -> dict:
        return await self._session_api_request(
            method,
            sid_attr="sid",
            login=self._ensure_login,
            refresh_login=self._refresh_login,
            session_label="Download Station",
            **kwargs,
        )

    async def _file_station_request(self, method: str, **kwargs) -> dict:
        return await self._session_api_request(
            method,
            sid_attr="_file_station_sid",
            login=self._ensure_file_station_login,
            refresh_login=self._refresh_file_station_login,
            session_label="File Station",
            **kwargs,
        )

    async def _require_ds7(self) -> None:
        await self._ensure_login()
        await self._ensure_profile()
        if self._profile.version != 2:
            raise RuntimeError("此操作仅支持 DSM 7 Download Station")

    def invalidate_task_cache(self) -> None:
        self._task_cache_generation += 1
        self._task_cache.clear()

    async def _cached_task_value(
        self,
        key: tuple,
        loader: Callable[[], Awaitable[_T]],
        *,
        force_refresh: bool,
    ) -> _T:
        requested_at = time.monotonic()
        requested_generation = self._task_cache_generation
        now = requested_at
        cached = self._task_cache.get(key)
        if not force_refresh and cached and now - cached[0] < _TASK_CACHE_TTL:
            return cached[1]  # type: ignore[return-value]

        lock = self._task_cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if requested_generation != self._task_cache_generation:
                requested_generation = self._task_cache_generation
                requested_at = time.monotonic()
            now = time.monotonic()
            cached = self._task_cache.get(key)
            if not force_refresh and cached and now - cached[0] < _TASK_CACHE_TTL:
                return cached[1]  # type: ignore[return-value]
            # A concurrent forced refresh that started after this call may already
            # have populated the cache while we waited for the per-key lock.
            if force_refresh and cached and cached[0] >= requested_at:
                return cached[1]  # type: ignore[return-value]

            value = await loader()
            if requested_generation != self._task_cache_generation:
                return value
            if len(self._task_cache) >= _TASK_CACHE_MAX_ENTRIES:
                oldest_key = min(self._task_cache, key=lambda item: self._task_cache[item][0])
                self._task_cache.pop(oldest_key, None)
                oldest_lock = self._task_cache_locks.get(oldest_key)
                if oldest_lock is not None and not oldest_lock.locked():
                    self._task_cache_locks.pop(oldest_key, None)
            self._task_cache[key] = (time.monotonic(), value)
            return value

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
            self.invalidate_task_cache()
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
                error = data.get("error", {})
                if error.get("code") not in _AUTH_ERROR_CODES:
                    raise DownloadStationAPIError(error.get("code"), error)
                logger.warning("DS 上传时会话失效，重新登录重试")
                await self._refresh_login(form_data.get("_sid"))
                form_data["_sid"] = self.sid
                files = {"file": (filename, torrent_bytes, "application/x-bittorrent")}
                resp = await self.client.post(self._api_url, data=form_data, files=files)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("success"):
                    error = data.get("error", {})
                    raise DownloadStationAPIError(error.get("code"), error)

            task_id = self._extract_task_id(data)
            self.invalidate_task_cache()
            logger.info("DS 添加文件任务成功: %s, task_id=%s", filename, task_id)
            return task_id
        except Exception:
            logger.exception("DS 添加文件任务失败")
            return None

    # ------------------------------------------------------------------
    # 任务列表
    # ------------------------------------------------------------------

    async def get_tasks_page(
        self,
        offset: int,
        limit: int,
        *,
        statuses: Sequence[int] | None = None,
        status_inverse: bool = False,
        sort_by: str = "task_id",
        order: str = "DESC",
        additional: Sequence[str] = ("detail", "transfer"),
        force_refresh: bool = False,
    ) -> DS7TaskPage:
        """使用 DS7 原生分页读取一个稳定任务页。"""
        await self._require_ds7()
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), _TASK_PAGE_LIMIT))
        status_tuple = tuple(int(status) for status in statuses) if statuses else ()
        additional_tuple = tuple(additional)
        order = "ASC" if str(order).upper() == "ASC" else "DESC"
        key = (
            "page", offset, limit, status_tuple, bool(status_inverse),
            sort_by, order, additional_tuple,
        )

        async def load() -> DS7TaskPage:
            params = {
                "api": "SYNO.DownloadStation2.Task",
                "version": "2",
                "method": "list",
                "offset": str(offset),
                "limit": str(limit),
                "sort_by": sort_by,
                "order": order,
                "additional": json.dumps(additional_tuple),
                "_sid": self.sid,
            }
            if status_tuple:
                params["status"] = json.dumps(status_tuple)
                params["status_inverse"] = "true" if status_inverse else "false"

            data = await self._api_request("GET", params=params)
            payload = data.get("data", {})
            tasks = tuple(
                normalize_ds7_task(task)
                for task in payload.get(self._profile.list_task_key, [])
            )
            total = max(0, _safe_int(
                payload.get("total", payload.get("total_count")), len(tasks),
            ))
            return DS7TaskPage(tasks=tasks, total=total, offset=offset)

        return await self._cached_task_value(
            key, load, force_refresh=force_refresh,
        )

    async def _get_task_chunk(
        self,
        task_ids: tuple[str, ...],
        additional: tuple[str, ...],
    ) -> tuple[dict, ...]:
        params = {
            "api": "SYNO.DownloadStation2.Task",
            "version": "2",
            "method": "get",
            "id": json.dumps(task_ids),
            "additional": json.dumps(additional),
            "_sid": self.sid,
        }
        try:
            data = await self._api_request("GET", params=params)
        except DownloadStationAPIError as exc:
            if exc.code != 404:
                raise
            if len(task_ids) == 1:
                return ()
            tasks = []
            for task_id in task_ids:
                tasks.extend(await self._get_task_chunk((task_id,), additional))
            return tuple(tasks)

        payload = data.get("data", {})
        return tuple(
            normalize_ds7_task(task)
            for task in payload.get(self._profile.list_task_key, [])
        )

    async def get_tasks_by_ids(
        self,
        task_ids: Sequence[str],
        *,
        additional: Sequence[str] = ("detail", "transfer"),
        force_refresh: bool = False,
    ) -> List[dict]:
        """用 Task.get 定向读取 Bot 跟踪的任务，不扫描其他 DS 任务。"""
        unique_ids = tuple(dict.fromkeys(str(task_id) for task_id in task_ids if task_id))
        if not unique_ids:
            return []
        await self._require_ds7()
        additional_tuple = tuple(additional)
        tasks = []
        for index in range(0, len(unique_ids), 50):
            chunk = unique_ids[index:index + 50]
            key = ("ids", chunk, additional_tuple)

            async def load(chunk=chunk):
                return await self._get_task_chunk(chunk, additional_tuple)

            tasks.extend(await self._cached_task_value(
                key, load, force_refresh=force_refresh,
            ))
        return tasks

    async def get_task(
        self,
        task_id: str,
        *,
        additional: Sequence[str] = ("detail", "transfer"),
        force_refresh: bool = False,
    ) -> dict | None:
        tasks = await self.get_tasks_by_ids(
            [task_id], additional=additional, force_refresh=force_refresh,
        )
        return tasks[0] if tasks else None

    async def get_task_statistics(self, *, force_refresh: bool = False) -> dict:
        await self._require_ds7()
        key = ("statistics",)

        async def load() -> dict:
            params = {
                "api": "SYNO.DownloadStation2.Task.Statistic",
                "version": "1",
                "method": "get",
                "type": json.dumps(["emule"]),
                "type_inverse": "true",
                "_sid": self.sid,
            }
            data = await self._api_request("GET", params=params)
            payload = data.get("data", {})
            return {
                "download_rate": max(0, _safe_int(payload.get("download_rate"))),
                "upload_rate": max(0, _safe_int(payload.get("upload_rate"))),
            }

        return await self._cached_task_value(
            key, load, force_refresh=force_refresh,
        )

    async def get_tasks(self) -> List[dict]:
        await self._ensure_login()
        await self._ensure_profile()
        if self._profile.version != 2:
            params = {
                "api": self._profile.list_api,
                "version": self._profile.list_version,
                "method": "list",
                "_sid": self.sid,
            }
            data = await self._api_request("GET", params=params)
            tasks = data.get("data", {}).get(self._profile.list_task_key, [])
            return [{"title": task.get("title", ""), **task} for task in tasks]

        offset = 0
        tasks: List[dict] = []
        seen_ids = set()
        for _ in range(100):
            page = await self.get_tasks_page(offset, _TASK_PAGE_LIMIT)
            added = 0
            for task in page.tasks:
                task_id = str(task.get("id") or "")
                if task_id and task_id in seen_ids:
                    continue
                if task_id:
                    seen_ids.add(task_id)
                tasks.append(task)
                added += 1
            if len(tasks) >= page.total or len(page.tasks) < _TASK_PAGE_LIMIT:
                break
            if added == 0:
                logger.warning("DS 任务分页未产生新任务，停止继续读取 (offset=%d)", offset)
                break
            offset += len(page.tasks)
        return tasks

    async def _task_action(self, task_id: str, method: str) -> bool:
        await self._require_ds7()
        if method not in ("pause", "resume"):
            raise ValueError("不支持的任务操作")
        try:
            await self._api_request("POST", data={
                "api": "SYNO.DownloadStation2.Task",
                "version": "2",
                "method": method,
                "id": json.dumps([task_id]),
                "_sid": self.sid,
            })
            self.invalidate_task_cache()
            return True
        except Exception:
            logger.exception("DS %s 任务失败: %s", method, task_id)
            return False

    async def pause_task(self, task_id: str) -> bool:
        return await self._task_action(task_id, "pause")

    async def resume_task(self, task_id: str) -> bool:
        return await self._task_action(task_id, "resume")

    async def wait_for_task_status(
        self,
        task_id: str,
        statuses: Sequence[int],
        *,
        timeout: float = 5.0,
        poll_interval: float = 0.25,
    ) -> dict | None:
        expected = {int(status) for status in statuses}
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            task = await self.get_task(task_id, force_refresh=True)
            if task is None:
                return None
            if _safe_int(task.get("status"), -1) in expected:
                return task
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(max(0.0, poll_interval))

    # ------------------------------------------------------------------
    # 删除任务
    # ------------------------------------------------------------------

    async def get_bt_task_files(self, task_id: str) -> List[dict]:
        """读取 BT 任务的完整文件清单。"""
        await self._require_ds7()
        offset = 0
        items = []
        expected_total: int | None = None
        for _ in range(1000):
            params = {
                "api": "SYNO.DownloadStation2.Task.BT.File",
                "version": "2",
                "method": "list",
                "task_id": task_id,
                "offset": str(offset),
                "limit": str(_TASK_PAGE_LIMIT),
                "sort_by": "name",
                "order": "ASC",
                "_sid": self.sid,
            }
            data = await self._api_request("GET", params=params)
            payload = data.get("data", {})
            batch = payload.get("items", [])
            total = _safe_int(payload.get("total"), -1)
            if total < 0:
                raise RuntimeError("Download Station 未返回 BT 文件总数")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise RuntimeError("Download Station BT 文件总数在分页期间发生变化")
            items.extend(batch)
            if len(items) > expected_total:
                raise RuntimeError("Download Station 返回了超出总数的 BT 文件")
            if len(items) == expected_total:
                break
            if not batch or len(batch) < _TASK_PAGE_LIMIT:
                break
            offset += len(batch)
        if expected_total is None or len(items) != expected_total:
            raise RuntimeError(
                "Download Station BT 文件清单不完整: "
                f"已读取 {len(items)}/{expected_total if expected_total is not None else '?'}"
            )
        return items

    async def _resolve_file_station_manifest(
        self,
        manifest: DS7FileManifest,
    ) -> DS7FileManifest:
        data = await self._file_station_request("GET", params={
            "api": "SYNO.FileStation.List",
            "version": "2",
            "method": "list_share",
            "offset": "0",
            "limit": "0",
            "onlywritable": "true",
            "additional": json.dumps(["real_path"]),
        })
        shares = data.get("data", {}).get("shares", [])
        destination = _map_file_station_destination(
            manifest.destination,
            shares,
        )
        real_destination = _map_file_station_real_destination(
            destination,
            shares,
        )
        return _remap_file_manifest(
            manifest,
            destination,
            real_destination,
        )

    async def _verify_file_manifest_paths(self, manifest: DS7FileManifest) -> None:
        """要求 File Station 对清单中的每个精确路径都返回文件信息。"""
        for index in range(0, len(manifest.paths), _FILE_DELETE_BATCH_SIZE):
            paths = manifest.paths[index:index + _FILE_DELETE_BATCH_SIZE]
            params = {
                "api": "SYNO.FileStation.List",
                "version": "2",
                "method": "getinfo",
                "path": json.dumps(paths, ensure_ascii=False),
                "additional": json.dumps(["real_path", "size"]),
            }
            data = await self._file_station_request("GET", params=params)
            files = data.get("data", {}).get("files", [])
            returned = {
                str(item.get("path"))
                for item in files
                if item.get("path")
            }
            if returned != set(paths):
                raise ValueError("File Station 无法确认全部下载文件")
            if any(item.get("isdir") is True for item in files):
                raise ValueError("File Station 文件清单中包含目录")
            file_by_path = {str(item.get("path")): item for item in files}
            if manifest.real_paths:
                expected_real_paths = dict(zip(manifest.paths, manifest.real_paths))
                if any(
                    str((file_by_path[path].get("additional") or {}).get("real_path") or "")
                    != expected_real_paths[path]
                    for path in paths
                ):
                    raise ValueError("File Station 文件真实路径与下载任务不一致")
            if manifest.file_sizes:
                expected_sizes = dict(zip(manifest.paths, manifest.file_sizes))
                if any(
                    _safe_int((file_by_path[path].get("additional") or {}).get("size"), -1)
                    != expected_sizes[path]
                    for path in paths
                ):
                    raise ValueError("File Station 文件大小与下载任务不一致")

    async def prepare_file_manifest(self, task: dict) -> DS7FileManifest:
        items = await self.get_bt_task_files(str(task.get("id") or ""))
        manifest = build_ds7_file_manifest(task, items)
        manifest = await self._resolve_file_station_manifest(manifest)
        await self._verify_file_manifest_paths(manifest)
        return manifest

    async def delete_file_manifest(
        self,
        manifest: DS7FileManifest,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.5,
    ) -> bool:
        """删除经过验证的精确文件路径，并轮询每个 File Station 任务。"""
        try:
            await self._verify_file_manifest_paths(manifest)
            deadline = time.monotonic() + timeout
            for index in range(0, len(manifest.paths), _FILE_DELETE_BATCH_SIZE):
                paths = manifest.paths[index:index + _FILE_DELETE_BATCH_SIZE]
                start = await self._file_station_request("POST", data={
                    "api": "SYNO.FileStation.Delete",
                    "version": "2",
                    "method": "start",
                    "path": json.dumps(paths, ensure_ascii=False),
                    "recursive": "false",
                    "accurate_progress": "false",
                })
                operation_id = start.get("data", {}).get("taskid")
                if not operation_id:
                    raise RuntimeError("File Station 未返回删除任务 ID")
                try:
                    while True:
                        status = await self._file_station_request("GET", params={
                            "api": "SYNO.FileStation.Delete",
                            "version": "2",
                            "method": "status",
                            "taskid": json.dumps(operation_id),
                        })
                        status_data = status.get("data", {})
                        if status_data.get("finished") is True:
                            total = _safe_int(status_data.get("total"), -1)
                            processed = _safe_int(status_data.get("processed_num"), -1)
                            if total != len(paths) or processed != total:
                                raise RuntimeError(
                                    "File Station 未确认全部文件删除完成"
                                )
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("File Station 删除文件超时")
                        await asyncio.sleep(poll_interval)
                finally:
                    try:
                        await self._file_station_request("POST", data={
                            "api": "SYNO.FileStation.Delete",
                            "version": "2",
                            "method": "stop",
                            "taskid": json.dumps(operation_id),
                        })
                    except Exception:
                        logger.warning("停止 File Station 删除任务失败: %s", operation_id)
            return True
        except Exception:
            logger.exception("File Station 精确文件删除失败: %s", manifest.task_id)
            return False

    async def delete_task(self, task_id: str, delete_files: bool = True) -> bool:
        try:
            await self._ensure_login()
            await self._ensure_profile()
            p = self._profile

            if delete_files and p.version == 2:
                task = await self.get_task(task_id, force_refresh=True)
                if not task:
                    return True
                status = _safe_int(task.get("status"), -1)
                if status not in (5, 8) or str(task.get("type") or "").lower() != "bt":
                    raise ValueError("仅已完成或做种中的 BT 任务支持安全删除文件")
                manifest = await self.prepare_file_manifest(task)
                if status == 8 and not await self.pause_task(task_id):
                    raise RuntimeError("停止做种失败")
                if status == 8 and not await self.wait_for_task_status(
                    task_id, (3,),
                ):
                    raise RuntimeError("等待做种任务暂停超时")
                if not await self.delete_file_manifest(manifest):
                    raise RuntimeError("下载文件删除失败")

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
            self.invalidate_task_cache()
            logger.info("DS 删除任务成功: %s", task_id)
            return True
        except Exception:
            # 任务可能已被其他人删除，检查是否还存在
            try:
                if self._profile and self._profile.version == 2:
                    task_missing = await self.get_task(task_id, force_refresh=True) is None
                else:
                    tasks = await self.get_tasks()
                    task_missing = not any(str(task.get("id")) == task_id for task in tasks)
                if task_missing:
                    logger.info("DS 任务已不存在，视为删除成功: %s", task_id)
                    self.invalidate_task_cache()
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
            await self._refresh_profile_for_connection_test()
            await self.get_tasks()
            return True
        except Exception:
            logger.exception("DS 连接测试失败")
            return False

    async def close(self):
        await self.client.aclose()
