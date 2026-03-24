"""NexusPHP 通用实现 — 基于 RSS 搜索接口"""

import asyncio
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
    """Parse NexusPHP torrents.php HTML to extract torrent entries.

    NexusPHP uses nested <table> inside the title cell (for title + subtitle
    layout).  We track table nesting depth so a nested </table> does not
    prematurely end parsing, and only treat direct-child <tr> of the
    torrents table as data rows.

    Also extracts:
    - subtitle from nested table second row
    - seeders/leechers from outer cells (pure integers after size cell)
    """

    def __init__(self):
        super().__init__()
        self.results: List[dict] = []
        self._in_torrents_table = False
        self._table_depth = 0       # nesting depth; 1 = directly in torrents table
        self._in_outer_row = False   # inside a direct-child <tr>
        self._current: dict = {}
        self._title_buf = ""
        self._cell_buf = ""
        self._subtitle_buf = ""
        self._capturing_title = False
        self._has_title = False
        self._found_size = False     # 已找到 size 列
        self._int_cells_after_size: List[str] = []  # size 之后的纯数字列
        # 副标题：嵌套表格内第二行的文本
        self._nested_row_count = 0
        self._capturing_subtitle = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)

        if tag == "table":
            if self._in_torrents_table:
                self._table_depth += 1
            elif "torrents" in attr_dict.get("class", ""):
                self._in_torrents_table = True
                self._table_depth = 1
            return

        if not self._in_torrents_table:
            return

        if tag == "tr":
            if self._table_depth == 1 and not self._in_outer_row:
                self._in_outer_row = True
                self._current = {}
                self._has_title = False
                self._capturing_title = False
                self._capturing_subtitle = False
                self._title_buf = ""
                self._subtitle_buf = ""
                self._found_size = False
                self._int_cells_after_size = []
                self._nested_row_count = 0
            elif self._in_outer_row and self._table_depth > 1:
                self._nested_row_count += 1
                # 嵌套表格第 2 行开始捕获副标题
                if self._nested_row_count >= 2 and "subtitle" not in self._current:
                    self._capturing_subtitle = True
                    self._subtitle_buf = ""
            return

        if not self._in_outer_row:
            return

        if tag == "td" and self._table_depth == 1:
            self._cell_buf = ""

        if tag == "a":
            href = attr_dict.get("href", "")
            if "details.php" in href and "id=" in href and not self._has_title:
                self._current["detail_link"] = href
                title_attr = attr_dict.get("title", "")
                if title_attr:
                    self._current["title"] = title_attr
                    self._has_title = True
                else:
                    self._capturing_title = True
                    self._title_buf = ""
            elif "download.php" in href and "id=" in href:
                if "download_link" not in self._current:
                    self._current["download_link"] = href

    def handle_data(self, data):
        if not self._in_outer_row:
            return
        if self._capturing_title:
            self._title_buf += data
        if self._capturing_subtitle:
            self._subtitle_buf += data
        if self._table_depth == 1:
            self._cell_buf += data

    def handle_endtag(self, tag):
        if not self._in_torrents_table:
            return

        if tag == "table":
            self._table_depth -= 1
            if self._table_depth <= 0:
                self._in_torrents_table = False
                if self._in_outer_row:
                    self._finalize_row()
                    self._in_outer_row = False
            return

        if tag == "a" and self._capturing_title:
            text = self._title_buf.strip()
            if text and not self._has_title:
                self._current["title"] = text
                self._has_title = True
            self._capturing_title = False

        # 副标题捕获结束（嵌套行的 </tr>）
        if tag == "tr" and self._capturing_subtitle and self._table_depth > 1:
            sub = self._subtitle_buf.strip()
            if sub:
                self._current["subtitle"] = sub
            self._capturing_subtitle = False

        if tag == "td" and self._in_outer_row and self._table_depth == 1:
            cell_text = self._cell_buf.strip()
            if cell_text and not self._found_size and re.match(
                r"^\d+(?:\.\d+)?\s*[KMGTP]?i?B$", cell_text, re.IGNORECASE
            ):
                self._current.setdefault("size", cell_text)
                self._found_size = True
            elif cell_text and self._found_size and re.match(r"^\d+$", cell_text):
                self._int_cells_after_size.append(cell_text)
            self._cell_buf = ""

        if tag == "tr" and self._in_outer_row and self._table_depth == 1:
            self._finalize_row()

    def _finalize_row(self):
        """提交当前行数据并重置状态。"""
        # 从 size 之后的纯数字列中提取 seeders/leechers
        ints = self._int_cells_after_size
        if len(ints) >= 2:
            self._current.setdefault("seeders", ints[0])
            self._current.setdefault("leechers", ints[1])
        elif len(ints) == 1:
            self._current.setdefault("seeders", ints[0])

        if self._current.get("title") and self._current.get("download_link"):
            self.results.append(self._current)
        self._in_outer_row = False
        self._current = {}


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

        seeders = 0
        try:
            seeders = int(item.get("seeders", 0))
        except (ValueError, TypeError):
            pass
        leechers = 0
        try:
            leechers = int(item.get("leechers", 0))
        except (ValueError, TypeError):
            pass

        results.append(TorrentResult(
            title=title,
            torrent_url=download_link,
            size=size or "N/A",
            seeders=seeders,
            leechers=leechers,
            link=detail_link,
            subtitle=item.get("subtitle", ""),
        ))

    return results


class NexusPHPSite(PTSiteBase):
    """NexusPHP 站点通用实现，通过 RSS 接口搜索和下载种子"""

    # 全局并发限制：同时最多 3 个 PT 站请求
    _semaphore = asyncio.Semaphore(3)

    def __init__(self, base_url: str, passkey: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.passkey = passkey
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    async def _get(self, url: str, **kwargs):
        """带并发限制的 GET 请求。"""
        async with self._semaphore:
            return await self._client.get(url, **kwargs)

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

        resp = await self._get(url)
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
        resp = await self._get(url, params=params, headers=headers)
        resp.raise_for_status()

        html = resp.text
        # 检测 Cookie 失效：被重定向到登录页面或页面包含登录表单
        if "login.php" in str(resp.url) or 'name="username"' in html or "<title>Login" in html:
            raise CookieExpiredError("Cookie 已失效，请重新设置")

        logger.debug("search_web HTML length: %d chars", len(html))
        has_table = 'class="torrents"' in html
        logger.debug("search_web torrents table found: %s", has_table)

        results = _parse_torrents_html(html, self.base_url, self.passkey)
        # 按做种数从高到低排序
        results.sort(key=lambda r: r.seeders, reverse=True)
        logger.info(
            "search_web '%s' (area=%d) parsed %d results from %d chars",
            keyword, search_area, len(results), len(html),
        )
        return results

    # ------------------------------------------------------------------
    # 下载种子
    # ------------------------------------------------------------------
    async def download_torrent(self, torrent_url: str) -> bytes:
        """下载 .torrent 文件，返回原始字节"""
        resp = await self._get(torrent_url)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # 连接测试
    # ------------------------------------------------------------------
    async def test_connection(self, cookie: str = "") -> bool:
        """检测 PT 站连接是否正常。有 Cookie 时用网页版，否则用 RSS。"""
        try:
            if cookie:
                url = f"{self.base_url}/torrents.php"
                resp = await self._get(
                    url, params={"search": "test"}, headers={"Cookie": cookie},
                )
                resp.raise_for_status()
                html = resp.text
                if "login.php" in str(resp.url) or '<title>Login' in html:
                    return False
                return True

            url = (
                f"{self.base_url}/torrentrss.php"
                f"?passkey={self.passkey}"
                f"&search=test"
                f"&rows=1"
                f"&linktype=dl"
            )
            resp = await self._get(url)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
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
