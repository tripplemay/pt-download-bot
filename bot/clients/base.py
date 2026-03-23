"""下载客户端抽象基类 — 接口契约，由 Teammate 2 实现"""

from abc import ABC, abstractmethod
from typing import List, Optional


class DownloadClientBase(ABC):
    """下载客户端抽象基类"""

    @abstractmethod
    async def add_torrent_url(self, url: str) -> bool:
        """通过 URL 添加种子下载任务，返回是否成功"""
        ...

    @abstractmethod
    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> bool:
        """通过上传 .torrent 文件添加下载任务，返回是否成功"""
        ...

    @abstractmethod
    async def get_tasks(self) -> List[dict]:
        """获取当前下载任务列表，每项至少包含 'title' 或 'name' 字段"""
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        ...

    async def close(self):
        """关闭连接（可选覆盖）"""
        pass
