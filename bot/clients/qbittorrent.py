"""qBittorrent Web API 客户端"""

import logging
from typing import List, Optional

import httpx

from bot.clients.base import DownloadClientBase

logger = logging.getLogger(__name__)


class QBittorrentClient(DownloadClientBase):
    """qBittorrent 下载客户端"""

    def __init__(self, host: str, username: str, password: str):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.logged_in = False
        self.client = httpx.AsyncClient(
            timeout=30.0, verify=host.startswith("https://"),
        )

    async def _login(self) -> None:
        """登录 qBittorrent，获取 cookie SID"""
        url = f"{self.host}/api/v2/auth/login"
        resp = await self.client.post(
            url, data={"username": self.username, "password": self.password}
        )
        resp.raise_for_status()
        # qBittorrent 返回 "Ok." 表示成功，"Fails." 表示失败
        if resp.text.strip() != "Ok.":
            raise ConnectionError(
                f"qBittorrent 登录失败: {resp.text}"
            )
        # cookie 会自动保存在 httpx client 中
        self.logged_in = True
        logger.info("qBittorrent 登录成功")

    async def _ensure_login(self) -> None:
        """确保已登录"""
        if not self.logged_in:
            await self._login()

    async def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """发送请求，如果未授权则自动重新登录重试"""
        await self._ensure_login()

        resp = await self.client.request(method, url, **kwargs)

        # 403 表示未授权 / SID 过期
        if resp.status_code == 403:
            logger.warning("qBittorrent 会话过期，尝试重新登录")
            self.logged_in = False
            await self._login()
            resp = await self.client.request(method, url, **kwargs)

        resp.raise_for_status()
        return resp

    async def add_torrent_url(self, url: str) -> Optional[str]:
        """通过 URL 添加种子下载任务"""
        try:
            api_url = f"{self.host}/api/v2/torrents/add"
            resp = await self._request_with_retry(
                "POST", api_url, data={"urls": url}
            )
            logger.info("qBittorrent 添加 URL 任务成功: %s", url)
            return ""  # qBittorrent add API 不返回 task_id
        except Exception:
            logger.exception("qBittorrent 添加 URL 任务失败")
            return None

    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> Optional[str]:
        """通过上传 .torrent 文件添加下载任务"""
        try:
            api_url = f"{self.host}/api/v2/torrents/add"
            files = {
                "torrents": (filename, torrent_bytes, "application/x-bittorrent")
            }
            resp = await self._request_with_retry("POST", api_url, files=files)
            logger.info("qBittorrent 添加文件任务成功: %s", filename)
            return ""
        except Exception:
            logger.exception("qBittorrent 添加文件任务失败")
            return None

    async def get_tasks(self) -> List[dict]:
        """获取当前下载任务列表"""
        api_url = f"{self.host}/api/v2/torrents/info"
        resp = await self._request_with_retry("GET", api_url)
        torrents = resp.json()
        return [{"name": t.get("name", ""), **t} for t in torrents]

    async def delete_task(self, task_id: str) -> bool:
        """删除任务（仅移除，不删文件）。task_id 为 torrent hash。"""
        try:
            api_url = f"{self.host}/api/v2/torrents/delete"
            await self._request_with_retry(
                "POST", api_url, data={"hashes": task_id, "deleteFiles": "false"}
            )
            logger.info("qBittorrent 删除任务成功: %s", task_id)
            return True
        except Exception:
            try:
                tasks = await self.get_tasks()
                if not any(t.get("hash") == task_id for t in tasks):
                    logger.info("qBittorrent 任务已不存在，视为删除成功: %s", task_id)
                    return True
            except Exception:
                pass
            logger.exception("qBittorrent 删除任务失败: %s", task_id)
            return False

    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        try:
            self.logged_in = False
            await self._login()
            await self.get_tasks()
            return True
        except Exception:
            logger.exception("qBittorrent 连接测试失败")
            return False

    async def close(self):
        """关闭连接"""
        await self.client.aclose()
