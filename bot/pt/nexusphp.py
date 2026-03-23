"""NexusPHP 通用实现 — 基于 RSS 搜索接口"""

import logging
import re
from html.parser import HTMLParser
from typing import List

import feedparser
import httpx

from bot.pt.base import PTSiteBase, TorrentResult

logger = logging.getLogger(__name__)


class CookieExpiredError(Exception):
    """PT 站 Cookie 已失效。"""
    pass


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


class _TorrentsPageParser(HTMLParser):
    """Parse NexusPHP torrents.php HTML to extract torrent entries."""

    def __init__(self):
        super().__init__()
        self.results: List[dict] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_count = 0
        self._current = {}
        self._capture_text = False
        self._text_buf = ""
        self._found_title_link = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)

        if tag == "table" and "torrents" in attr_dict.get("class", ""):
            self._in_table = True
            return

        if not self._in_table:
            return

        if tag == "tr":
            self._in_row = True
            self._cell_count = 0
            self._current = {}
            self._found_title_link = False
            return

        if tag == "td" and self._in_row:
            self._in_cell = True
            self._cell_count += 1
            self._text_buf = ""
            self._capture_text = True
            return

        if tag == "a" and self._in_row:
            href = attr_dict.get("href", "")
            if "details.php" in href and "id=" in href and not self._found_title_link:
                self._found_title_link = True
                self._current["detail_link"] = href
                self._capture_text = True
                self._text_buf = ""
            elif "download.php" in href and "id=" in href:
                self._current["download_link"] = href

    def handle_data(self, data):
        if self._capture_text:
            self._text_buf += data

    def handle_endtag(self, tag):
        if not self._in_table:
            return

        if tag == "a" and self._found_title_link and self._text_buf.strip() and "title" not in self._current:
            self._current["title"] = self._text_buf.strip()
            self._text_buf = ""

        if tag == "td" and self._in_cell:
            self._in_cell = False
            self._capture_text = False
            cell_text = self._text_buf.strip()
            # Try to detect size cells (e.g., "14.37 GB", "1.5 TB", "100.5 GiB")
            if cell_text and re.match(r"^\d+(?:\.\d+)?\s*[KMGTP]?i?B$", cell_text, re.IGNORECASE):
                self._current.setdefault("size", cell_text)
            self._text_buf = ""

        if tag == "tr" and self._in_row:
            self._in_row = False
            if self._current.get("title") and self._current.get("download_link"):
                self.results.append(self._current)
            self._current = {}

        if tag == "table" and self._in_table:
            self._in_table = False


def _parse_torrents_html(html: str, base_url: str, passkey: str) -> List[TorrentResult]:
    """解析 torrents.php 页面 HTML，提取种子列表。"""
    parser = _TorrentsPageParser()
    parser.feed(html)

    results = []
    for item in parser.results:
        title = item.get("title", "")
        download_link = item.get("download_link", "")

        # Ensure download link is absolute and has passkey
        if download_link and not download_link.startswith("http"):
            download_link = f"{base_url}/{download_link.lstrip('/')}"
        if "passkey" not in download_link and passkey:
            sep = "&" if "?" in download_link else "?"
            download_link = f"{download_link}{sep}passkey={passkey}"

        detail_link = item.get("detail_link", "")
        if detail_link and not detail_link.startswith("http"):
            detail_link = f"{base_url}/{detail_link.lstrip('/')}"

        size = item.get("size", "")
        if not size:
            size = _parse_size_from_title(title)

        results.append(TorrentResult(
            title=title,
            torrent_url=download_link,
            size=size or "N/A",
            link=detail_link,
        ))

    return results


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
    # 网页搜索（降级方案）
    # ------------------------------------------------------------------
    async def search_web(self, keyword: str, cookie: str, search_area: int = 0) -> List[TorrentResult]:
        """通过网页版 torrents.php 搜索（需要 Cookie）。

        search_area: 0=标题, 1=简介/副标题
        返回结果比 RSS 完整得多（可达 100+ 条）。

        Raises:
            CookieExpiredError: Cookie 已失效
        """
        url = f"{self.base_url}/torrents.php"
        params = {
            "search": keyword,
            "search_area": str(search_area),
        }
        headers = {"Cookie": cookie}
        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()

        html = resp.text
        # 检测 Cookie 失效：被重定向到登录页面或页面包含登录表单
        if "login.php" in str(resp.url) or 'name="username"' in html or "<title>Login" in html:
            raise CookieExpiredError("Cookie 已失效，请重新设置")

        return _parse_torrents_html(html, self.base_url, self.passkey)

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
