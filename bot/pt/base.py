"""PT 站抽象基类 — 接口契约，由 Teammate 1 实现"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TorrentResult:
    """搜索结果数据类"""
    title: str          # 种子标题
    torrent_url: str    # 种子下载链接（含 passkey）
    size: str           # 文件大小（如 "14.37 GB"）
    seeders: int = 0    # 做种数
    leechers: int = 0   # 下载数
    link: str = ""      # 详情页链接


class PTSiteBase(ABC):
    """PT 站抽象基类"""

    @abstractmethod
    async def search(self, keyword: str) -> List[TorrentResult]:
        """搜索种子，返回结果列表"""
        ...

    @abstractmethod
    async def download_torrent(self, torrent_url: str) -> bytes:
        """下载 .torrent 文件，返回字节内容"""
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试连接是否正常"""
        ...

    async def close(self):
        """关闭连接（可选覆盖）"""
        pass
