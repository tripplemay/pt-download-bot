"""Transmission RPC 客户端"""

import base64
import logging
from typing import List, Optional

import httpx

from bot.clients.base import DownloadClientBase

logger = logging.getLogger(__name__)


class TransmissionClient(DownloadClientBase):
    """Transmission 下载客户端"""

    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.rpc_url = f"{self.host}/transmission/rpc"
        self.username = username
        self.password = password
        self.session_id: str | None = None

        # 如果 username 和 password 都非空，使用 Basic Auth
        auth = None
        if username and password:
            auth = httpx.BasicAuth(username, password)

        self.client = httpx.AsyncClient(
            timeout=30.0, auth=auth, verify=host.startswith("https://"),
        )

    async def _rpc_request(self, method: str, arguments: Optional[dict] = None) -> dict:
        """发送 Transmission RPC 请求，自动处理 409 获取 session id"""
        payload = {"method": method}
        if arguments:
            payload["arguments"] = arguments

        headers = {}
        if self.session_id:
            headers["X-Transmission-Session-Id"] = self.session_id

        resp = await self.client.post(self.rpc_url, json=payload, headers=headers)

        # 409 表示需要获取 session id
        if resp.status_code == 409:
            self.session_id = resp.headers.get("X-Transmission-Session-Id", "")
            if not self.session_id:
                raise ConnectionError(
                    "Transmission 返回 409 但未提供 X-Transmission-Session-Id"
                )
            logger.info("获取 Transmission Session-Id: %s", self.session_id)
            headers["X-Transmission-Session-Id"] = self.session_id
            resp = await self.client.post(self.rpc_url, json=payload, headers=headers)

        resp.raise_for_status()
        data = resp.json()

        if data.get("result") != "success":
            raise ConnectionError(
                f"Transmission RPC 失败: {data.get('result', 'unknown error')}"
            )
        return data

    def _extract_task_id(self, data: dict) -> str:
        """从 torrent-add 响应中提取 torrent ID。"""
        args = data.get("arguments", {})
        added = args.get("torrent-added") or args.get("torrent-duplicate")
        if added and "id" in added:
            return str(added["id"])
        return ""

    async def add_torrent_url(self, url: str) -> Optional[str]:
        """通过 URL 添加种子下载任务"""
        try:
            data = await self._rpc_request("torrent-add", {"filename": url})
            task_id = self._extract_task_id(data)
            logger.info("Transmission 添加 URL 任务成功, task_id=%s", task_id)
            return task_id
        except Exception:
            logger.exception("Transmission 添加 URL 任务失败")
            return None

    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> Optional[str]:
        """通过上传 .torrent 文件添加下载任务"""
        try:
            metainfo = base64.b64encode(torrent_bytes).decode("ascii")
            data = await self._rpc_request("torrent-add", {"metainfo": metainfo})
            task_id = self._extract_task_id(data)
            logger.info("Transmission 添加文件任务成功, task_id=%s", task_id)
            return task_id
        except Exception:
            logger.exception("Transmission 添加文件任务失败")
            return None

    async def get_tasks(self) -> List[dict]:
        """获取当前下载任务列表"""
        data = await self._rpc_request(
            "torrent-get", {"fields": ["name", "status"]}
        )
        torrents = data.get("arguments", {}).get("torrents", [])
        return [{"name": t.get("name", ""), **t} for t in torrents]

    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        try:
            self.session_id = None
            await self.get_tasks()
            return True
        except Exception:
            logger.exception("Transmission 连接测试失败")
            return False

    async def close(self):
        """关闭连接"""
        await self.client.aclose()
