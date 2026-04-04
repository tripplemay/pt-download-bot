"""下载客户端抽象基类 — 接口契约"""

from abc import ABC, abstractmethod
from typing import List, Optional


class DownloadClientBase(ABC):
    """下载客户端抽象基类

    add_torrent_url / add_torrent_file 返回值：
    - 成功且有 task_id: 返回 task_id 字符串（如 "dbid_123"）
    - 成功但无 task_id: 返回 ""（空字符串）
    - 失败: 返回 None

    调用方必须用 `if result is not None:` 判断成功（空字符串也是成功）。
    """

    @abstractmethod
    async def add_torrent_url(self, url: str) -> Optional[str]:
        """通过 URL 添加种子下载任务，返回 task_id 或 None"""
        ...

    @abstractmethod
    async def add_torrent_file(self, torrent_bytes: bytes, filename: str) -> Optional[str]:
        """通过上传 .torrent 文件添加下载任务，返回 task_id 或 None"""
        ...

    @abstractmethod
    async def get_tasks(self) -> List[dict]:
        """获取当前下载任务列表，每项至少包含 'title' 或 'name' 字段"""
        ...

    @abstractmethod
    async def delete_task(self, task_id: str, delete_files: bool = True) -> bool:
        """删除指定任务，返回是否成功。delete_files=True 时同时删除本地文件。"""
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        ...

    async def close(self):
        """关闭连接（可选覆盖）"""
        pass
