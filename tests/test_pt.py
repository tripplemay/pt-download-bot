"""Comprehensive tests for the PT site module (base + NexusPHP)."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.pt.base import PTSiteBase, TorrentResult
from bot.pt.nexusphp import NexusPHPSite, _bytes_to_human, _parse_size_from_title

# ---------------------------------------------------------------------------
# RSS XML helpers
# ---------------------------------------------------------------------------

RSS_WITH_ENCLOSURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>Test Movie 2024 1080p BluRay</title>
<link>https://example.com/details.php?id=123</link>
<enclosure url="https://example.com/download.php?id=123&amp;passkey=abc" length="15437930496" type="application/x-bittorrent"/>
</item>
<item>
<title>Another Movie 2024 720p WEB-DL</title>
<link>https://example.com/details.php?id=456</link>
<enclosure url="https://example.com/download.php?id=456&amp;passkey=abc" length="4294967296" type="application/x-bittorrent"/>
</item>
</channel>
</rss>
"""

RSS_WITHOUT_LENGTH = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>Some Release 14.37 GB 1080p BluRay</title>
<link>https://example.com/details.php?id=789</link>
<enclosure url="https://example.com/download.php?id=789&amp;passkey=abc" type="application/x-bittorrent"/>
</item>
</channel>
</rss>
"""

RSS_NO_ENCLOSURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>Plain Entry 2.5 TB</title>
<link>https://example.com/details.php?id=999</link>
</item>
</channel>
</rss>
"""

RSS_EMPTY = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
</channel>
</rss>
"""

RSS_VALID_SIMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>test</title>
<link>https://example.com/details.php?id=1</link>
</item>
</channel>
</rss>
"""


# ===================================================================
# TorrentResult dataclass
# ===================================================================

class TestTorrentResult:

    def test_fields(self):
        r = TorrentResult(
            title="My Torrent",
            torrent_url="https://example.com/dl?id=1",
            size="14.37 GB",
            seeders=10,
            leechers=2,
            link="https://example.com/details?id=1",
        )
        assert r.title == "My Torrent"
        assert r.torrent_url == "https://example.com/dl?id=1"
        assert r.size == "14.37 GB"
        assert r.seeders == 10
        assert r.leechers == 2
        assert r.link == "https://example.com/details?id=1"

    def test_defaults(self):
        r = TorrentResult(title="t", torrent_url="u", size="1 GB")
        assert r.seeders == 0
        assert r.leechers == 0
        assert r.link == ""


# ===================================================================
# _bytes_to_human
# ===================================================================

class TestBytesToHuman:

    def test_zero(self):
        assert _bytes_to_human(0) == "0.00 B"

    def test_kb(self):
        assert _bytes_to_human(1024) == "1.00 KB"

    def test_mb(self):
        assert _bytes_to_human(1048576) == "1.00 MB"

    def test_gb(self):
        assert _bytes_to_human(1073741824) == "1.00 GB"

    def test_tb(self):
        assert _bytes_to_human(1099511627776) == "1.00 TB"

    def test_pb(self):
        assert _bytes_to_human(1125899906842624) == "1.00 PB"

    def test_fractional_gb(self):
        # 14.37 GB = 14.37 * 1073741824
        n = int(14.37 * 1073741824)
        result = _bytes_to_human(n)
        assert result.endswith("GB")
        assert result.startswith("14.3")

    def test_small_bytes(self):
        assert _bytes_to_human(512) == "512.00 B"


# ===================================================================
# _parse_size_from_title
# ===================================================================

class TestParseSizeFromTitle:

    def test_gb(self):
        assert _parse_size_from_title("Movie 14.37 GB 1080p") == "14.37 GB"

    def test_tb(self):
        assert _parse_size_from_title("Remux 1.5 TB complete") == "1.5 TB"

    def test_no_size(self):
        assert _parse_size_from_title("No size info here") == ""

    def test_case_insensitive(self):
        assert _parse_size_from_title("Movie 14.37 gb 1080p") == "14.37 GB"

    def test_mb(self):
        assert _parse_size_from_title("Small File 350 MB") == "350 MB"

    def test_kb(self):
        assert _parse_size_from_title("Tiny 512 KB subtitle") == "512 KB"

    def test_bytes(self):
        assert _parse_size_from_title("Tiny 100 B file") == "100 B"

    def test_pb(self):
        assert _parse_size_from_title("Huge 2.5 PB archive") == "2.5 PB"


# ===================================================================
# NexusPHPSite — helpers
# ===================================================================

def _make_site() -> NexusPHPSite:
    """Create a NexusPHPSite with the real httpx client patched out."""
    with patch("bot.pt.nexusphp.httpx.AsyncClient"):
        site = NexusPHPSite(base_url="https://example.com", passkey="testkey123")
    return site


def _mock_response(text: str = "", content: bytes = b"", status_code: int = 200):
    """Build a mock httpx.Response."""
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.content = content
    resp.raise_for_status = lambda: None
    return resp


def _mock_error_response(status_code: int = 500):
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        message="Server Error",
        request=httpx.Request("GET", "https://example.com"),
        response=resp,
    )
    return resp


# ===================================================================
# NexusPHPSite.search
# ===================================================================

class TestNexusPHPSearch:

    async def test_search_with_enclosure(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_WITH_ENCLOSURE)

        results = await site.search("test")

        assert len(results) == 2
        r0 = results[0]
        assert r0.title == "Test Movie 2024 1080p BluRay"
        assert "download.php?id=123" in r0.torrent_url
        # 15437930496 bytes ≈ 14.38 GB
        assert "GB" in r0.size
        assert r0.link == "https://example.com/details.php?id=123"
        assert r0.seeders == 0
        assert r0.leechers == 0

        r1 = results[1]
        assert r1.title == "Another Movie 2024 720p WEB-DL"
        assert "4.00 GB" == r1.size

    async def test_search_without_length_falls_back_to_title(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_WITHOUT_LENGTH)

        results = await site.search("test")

        assert len(results) == 1
        assert results[0].size == "14.37 GB"

    async def test_search_no_enclosure_uses_entry_link(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_NO_ENCLOSURE)

        results = await site.search("test")

        assert len(results) == 1
        r = results[0]
        # Falls back to entry.link as torrent_url
        assert r.torrent_url == "https://example.com/details.php?id=999"
        assert r.size == "2.5 TB"

    async def test_search_empty_feed(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_EMPTY)

        results = await site.search("nothing")

        assert results == []

    async def test_search_http_error(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_error_response(500)

        with pytest.raises(httpx.HTTPStatusError):
            await site.search("fail")

    async def test_search_builds_correct_url(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_EMPTY)

        await site.search("my keyword")

        called_url = site._client.get.call_args[0][0]
        assert "passkey=testkey123" in called_url
        assert "search=my keyword" in called_url
        assert "rows=50" in called_url
        assert "linktype=dl" in called_url
        assert called_url.startswith("https://example.com/torrentrss.php")

    async def test_search_result_size_na_when_no_size_anywhere(self):
        rss = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>No Size Info</title>
<link>https://example.com/details.php?id=1</link>
</item>
</channel>
</rss>
"""
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=rss)

        results = await site.search("x")
        assert results[0].size == "N/A"


# ===================================================================
# NexusPHPSite.download_torrent
# ===================================================================

class TestNexusPHPDownload:

    async def test_download_returns_bytes(self):
        torrent_bytes = b"\xd8\x06torrent-content-here"
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(content=torrent_bytes)

        result = await site.download_torrent("https://example.com/download.php?id=1")

        assert result == torrent_bytes
        site._client.get.assert_called_once_with("https://example.com/download.php?id=1")

    async def test_download_http_error(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_error_response(403)

        with pytest.raises(httpx.HTTPStatusError):
            await site.download_torrent("https://example.com/download.php?id=1")


# ===================================================================
# NexusPHPSite.test_connection
# ===================================================================

class TestNexusPHPTestConnection:

    async def test_connection_success(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_VALID_SIMPLE)

        result = await site.test_connection()

        assert result is True

    async def test_connection_http_exception(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.side_effect = httpx.ConnectError("Connection refused")

        result = await site.test_connection()

        assert result is False

    async def test_connection_server_error(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_error_response(500)

        result = await site.test_connection()

        assert result is False

    async def test_connection_builds_correct_url(self):
        site = _make_site()
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=RSS_VALID_SIMPLE)

        await site.test_connection()

        called_url = site._client.get.call_args[0][0]
        assert "search=test" in called_url
        assert "rows=1" in called_url


# ===================================================================
# NexusPHPSite.close
# ===================================================================

class TestNexusPHPClose:

    async def test_close_calls_aclose(self):
        site = _make_site()
        site._client = AsyncMock()

        await site.close()

        site._client.aclose.assert_awaited_once()


# ===================================================================
# NexusPHPSite.__init__
# ===================================================================

class TestNexusPHPInit:

    def test_strips_trailing_slash(self):
        with patch("bot.pt.nexusphp.httpx.AsyncClient"):
            site = NexusPHPSite(base_url="https://example.com/", passkey="key")
        assert site.base_url == "https://example.com"

    def test_stores_passkey(self):
        with patch("bot.pt.nexusphp.httpx.AsyncClient"):
            site = NexusPHPSite(base_url="https://example.com", passkey="mykey")
        assert site.passkey == "mykey"

    def test_creates_async_client(self):
        with patch("bot.pt.nexusphp.httpx.AsyncClient") as mock_cls:
            site = NexusPHPSite(base_url="https://example.com", passkey="key")
        mock_cls.assert_called_once()
        assert site._client is mock_cls.return_value


# ===================================================================
# PTSiteBase is abstract
# ===================================================================

class TestPTSiteBase:

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            PTSiteBase()

    def test_nexusphp_is_subclass(self):
        assert issubclass(NexusPHPSite, PTSiteBase)


# ===================================================================
# _TorrentsPageParser & _parse_torrents_html
# ===================================================================

from bot.pt.nexusphp import _TorrentsPageParser, _parse_torrents_html

SAMPLE_TORRENTS_HTML = '''
<table class="torrents">
<tr>
  <td class="rowfollow">1</td>
  <td>
    <a href="details.php?id=123">Test Movie 2024 BluRay 1080p</a>
    <br/>中文副标题
    <a href="download.php?id=123"><img class="download" /></a>
  </td>
  <td>14.37 GB</td>
</tr>
<tr>
  <td class="rowfollow">2</td>
  <td>
    <a href="details.php?id=456">Another Movie 2024 4K</a>
    <a href="download.php?id=456"><img class="download" /></a>
  </td>
  <td>35.66 GB</td>
</tr>
</table>
'''


class TestTorrentsPageParser:

    def test_parse_html_extracts_entries(self):
        results = _parse_torrents_html(
            SAMPLE_TORRENTS_HTML, "https://example.com", "pk123"
        )
        assert len(results) == 2

    def test_parse_html_title_correct(self):
        results = _parse_torrents_html(
            SAMPLE_TORRENTS_HTML, "https://example.com", "pk123"
        )
        assert results[0].title == "Test Movie 2024 BluRay 1080p"

    def test_parse_html_download_link_absolute(self):
        results = _parse_torrents_html(
            SAMPLE_TORRENTS_HTML, "https://example.com", "pk123"
        )
        for r in results:
            assert r.torrent_url.startswith("https://example.com/")

    def test_parse_html_passkey_appended(self):
        results = _parse_torrents_html(
            SAMPLE_TORRENTS_HTML, "https://example.com", "pk123"
        )
        for r in results:
            assert "passkey=pk123" in r.torrent_url

    def test_parse_html_size_extracted(self):
        results = _parse_torrents_html(
            SAMPLE_TORRENTS_HTML, "https://example.com", "pk123"
        )
        assert results[0].size == "14.37 GB"
        assert results[1].size == "35.66 GB"

    def test_parse_html_empty_table(self):
        html = '<table class="torrents"></table>'
        results = _parse_torrents_html(html, "https://example.com", "pk123")
        assert results == []

    def test_parse_html_no_table(self):
        html = "<html><body>No torrents here</body></html>"
        results = _parse_torrents_html(html, "https://example.com", "pk123")
        assert results == []


# ===================================================================
# NexusPHPSite.search_web
# ===================================================================

class TestSearchWeb:

    async def test_search_web_with_cookie(self):
        with patch("bot.pt.nexusphp.httpx.AsyncClient"):
            site = NexusPHPSite(
                base_url="https://example.com",
                passkey="testkey123",
                cookie="uid=1; pass=abc",
            )
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=SAMPLE_TORRENTS_HTML)

        results = await site.search_web("test")

        assert len(results) == 2
        assert results[0].title == "Test Movie 2024 BluRay 1080p"

    async def test_search_web_no_cookie(self):
        site = _make_site()
        site._client = AsyncMock()

        results = await site.search_web("test")

        assert results == []
        site._client.get.assert_not_called()

    async def test_search_web_passes_search_area(self):
        with patch("bot.pt.nexusphp.httpx.AsyncClient"):
            site = NexusPHPSite(
                base_url="https://example.com",
                passkey="testkey123",
                cookie="uid=1; pass=abc",
            )
        site._client = AsyncMock()
        site._client.get.return_value = _mock_response(text=SAMPLE_TORRENTS_HTML)

        await site.search_web("test", search_area=1)

        call_kwargs = site._client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["search_area"] == "1"


# ===================================================================
# _contains_chinese
# ===================================================================

from bot.handlers.search import _contains_chinese


class TestContainsChinese:

    def test_chinese_text(self):
        assert _contains_chinese("王冠") is True

    def test_english_text(self):
        assert _contains_chinese("Crown") is False

    def test_mixed_text(self):
        assert _contains_chinese("王冠 Crown") is True

    def test_empty_text(self):
        assert _contains_chinese("") is False

    def test_numbers(self):
        assert _contains_chinese("123") is False
