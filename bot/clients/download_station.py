"""群晖 Download Station API 客户端"""

import logging
from typing import List

import httpx

from bot.clients.base import DownloadClientBase

logger = logging.getLogger(__name__)


class DownloadStationClient(DownloadClientBase):
    """群晖 Download Station 下载客户端"""

    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.sid: str | None = None
        self.client = httpx.AsyncClient(timeout=30.0, verify=False)

    async def _login(self) -> None:
        """登录 Download Station，获取 SID"""
        url = (
            f"{self.host}/webapi/auth.cgi"
            f"?api=SYNO.API.Auth&version=3&method=login"
            f"&account={self.username}&passwd={self.password}"
            f"&session=DownloadStation&format=cookie"
        )
        resp = await self.client.get(url)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise ConnectionError(
                f"Download Station 登录失败: {data.get('error', {})}"
            )
        self.sid = data["data"]["sid"]
        logger.info("Download Station 登录成功")

    async def _ensure_login(self) -> None:
        """确保已登录"""
        if self.sid is None:
            await self._login()

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> dict:
        """发送请求，如果 SID 过期则自动重新登录重试"""
        await self._ensure_login()

        resp = await self.client.request(method, url, **kwargs)
        resp.raise_for_status()
        data = resp.json()

        # 如果请求失败（SID 过期），重新登录再试一次
        if not data.get("success"):
            error_code = data.get("error", {}).get("code")
            # error code 105 = SID not found / expired
            logger.warning(
                "Download Station 请求失败 (error=%s)，尝试重新登录", error_code
            )
            self.sid = None
            await self._login()
            # 重新构建包含新 SID 的请求参数
            if "params" in kwargs:
                kwargs["params"]["_sid"] = self.sid
            if "data" in kwargs and isinstance(kwargs["data"], dict):
                kwargs["data"]["_sid"] = self.sid
            resp = await self.client.request(method, url, **kwargs)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                raise ConnectionError(
                    f"Download Station 请求失败: {data.get('error', {})}"
                )
        return data

    async def add_torrent_url(self, url: str) -> bool:
        """通过 URL 添加种子下载任务"""
        try:
            api_url = f"{self.host}/webapi/DownloadStation/task.cgi"
            form_data = {
                "api": "SYNO.DownloadStation.Task",
                "version": "1",
                "method": "create",
                "uri": url,
                "_sid": self.sid,
            }
            await self._request_with_retry("POST", api_url, data=form_data)
            logger.info("Download Station 添加 URL 任务成功: %s", url)
            return True
        except Exception:
            logger.exception("Download Station 添加 URL 任务失败")
            return False

    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> bool:
        """通过上传 .torrent 文件添加下载任务"""
        try:
            await self._ensure_login()
            api_url = f"{self.host}/webapi/DownloadStation/task.cgi"
            form_data = {
                "api": "SYNO.DownloadStation.Task",
                "version": "1",
                "method": "create",
                "_sid": self.sid,
            }
            files = {"file": (filename, torrent_bytes, "application/x-bittorrent")}

            resp = await self.client.post(api_url, data=form_data, files=files)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("success"):
                # SID 过期，重新登录重试
                logger.warning("Download Station 上传失败，尝试重新登录")
                self.sid = None
                await self._login()
                form_data["_sid"] = self.sid
                files = {
                    "file": (filename, torrent_bytes, "application/x-bittorrent")
                }
                resp = await self.client.post(api_url, data=form_data, files=files)
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

    async def get_tasks(self) -> List[dict]:
        """获取当前下载任务列表"""
        await self._ensure_login()
        api_url = f"{self.host}/webapi/DownloadStation/task.cgi"
        params = {
            "api": "SYNO.DownloadStation.Task",
            "version": "1",
            "method": "list",
            "_sid": self.sid,
        }
        data = await self._request_with_retry("GET", api_url, params=params)
        tasks = data.get("data", {}).get("tasks", [])
        return [{"title": t.get("title", ""), **t} for t in tasks]

    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        try:
            self.sid = None
            await self._login()
            await self.get_tasks()
            return True
        except Exception:
            logger.exception("Download Station 连接测试失败")
            return False

    async def close(self):
        """关闭连接"""
        await self.client.aclose()
