"""NexusPHP 通用实现 — 基于 RSS 搜索接口"""

import logging
import re
from typing import List

import feedparser
import httpx

from bot.pt.base import PTSiteBase, TorrentResult

logger = logging.getLogger(__name__)


def _bytes_to_human(n: int) -> str:
    """将字节数转换为人类可读格式 (KB/MB/GB/TB)"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def _parse_size_from_title(title: str) -> str:
    """尝试从标题中匹配文件大小，如 '14.37 GB'"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB|PB)", title, re.IGNORECASE)
    if m:
        return f"{m.group(1)} {m.group(2).upper()}"
    return ""


class NexusPHPSite(PTSiteBase):
    """NexusPHP 站点通用实现，通过 RSS 接口搜索和下载种子"""

    def __init__(self, base_url: str, passkey: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.passkey = passkey
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "PTBot/1.0"},
        )

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    async def search(self, keyword: str) -> List[TorrentResult]:
        """通过 RSS 接口搜索种子"""
        url = (
            f"{self.base_url}/torrentrss.php"
            f"?passkey={self.passkey}"
            f"&search={keyword}"
            f"&rows=50"
            f"&linktype=dl"
        )
        logger.debug("NexusPHP search url: %s", url.replace(self.passkey, "***"))

        resp = await self._client.get(url)
        resp.raise_for_status()

        feed = feedparser.parse(resp.text)
        results: List[TorrentResult] = []

        for entry in feed.entries:
            title: str = entry.get("title", "")
            # torrent 下载链接：优先取 enclosure link，否则取 entry.link
            torrent_url = ""
            size = ""
            for link in entry.get("links", []):
                if link.get("type", "").startswith("application") or link.get("rel") == "enclosure":
                    torrent_url = link.get("href", "")
                    length = link.get("length")
                    if length:
                        try:
                            size = _bytes_to_human(int(length))
                        except (ValueError, TypeError):
                            pass
                    break

            if not torrent_url:
                torrent_url = entry.get("link", "")

            if not size:
                size = _parse_size_from_title(title)

            detail_link = entry.get("link", "")

            results.append(
                TorrentResult(
                    title=title,
                    torrent_url=torrent_url,
                    size=size or "N/A",
                    seeders=0,
                    leechers=0,
                    link=detail_link,
                )
            )

        logger.info("NexusPHP search '%s' returned %d results", keyword, len(results))
        return results

    # ------------------------------------------------------------------
    # 下载种子
    # ------------------------------------------------------------------
    async def download_torrent(self, torrent_url: str) -> bytes:
        """下载 .torrent 文件，返回原始字节"""
        resp = await self._client.get(torrent_url)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # 连接测试
    # ------------------------------------------------------------------
    async def test_connection(self) -> bool:
        """用一个简单搜索检测连接是否正常"""
        try:
            url = (
                f"{self.base_url}/torrentrss.php"
                f"?passkey={self.passkey}"
                f"&search=test"
                f"&rows=1"
                f"&linktype=dl"
            )
            resp = await self._client.get(url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            # 只要解析成功且没有 bozo 错误即视为连接正常
            return not feed.bozo
        except Exception:
            logger.exception("NexusPHP connection test failed")
            return False

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    async def close(self) -> None:
        """关闭 httpx 客户端"""
        await self._client.aclose()
