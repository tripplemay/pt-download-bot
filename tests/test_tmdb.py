"""Tests for the TMDB API client."""

from unittest.mock import AsyncMock, MagicMock, patch

from bot.tmdb import TMDBClient


def _make_client():
    """Create a TMDBClient with httpx.AsyncClient patched out."""
    with patch("bot.tmdb.httpx.AsyncClient"):
        client = TMDBClient(api_key="fake-api-key")
    client._client = AsyncMock()
    return client


def _mock_response(json_data: dict):
    """Build a mock httpx response that returns the given JSON."""
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status = lambda: None
    return resp


# ===================================================================
# search_movie_name
# ===================================================================

class TestSearchMovieName:

    async def test_search_movie_name_success(self):
        client = _make_client()
        client._client.get.return_value = _mock_response(
            {"results": [{"original_title": "The Crown", "popularity": 85.5}]}
        )

        result = await client.search_movie_name("王冠")

        assert result == {"name": "The Crown", "popularity": 85.5}

    async def test_search_movie_name_no_results(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({"results": []})

        result = await client.search_movie_name("不存在的电影")

        assert result is None

    async def test_search_movie_name_error(self):
        client = _make_client()
        client._client.get.side_effect = Exception("connection error")

        result = await client.search_movie_name("王冠")

        assert result is None


# ===================================================================
# search_tv_name
# ===================================================================

class TestSearchTvName:

    async def test_search_tv_name_success(self):
        client = _make_client()
        client._client.get.return_value = _mock_response(
            {"results": [{"original_name": "The Crown", "popularity": 72.3}]}
        )

        result = await client.search_tv_name("王冠")

        assert result == {"name": "The Crown", "popularity": 72.3}

    async def test_search_tv_name_no_results(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({"results": []})

        result = await client.search_tv_name("不存在的剧集")

        assert result is None


# ===================================================================
# translate
# ===================================================================

class TestTranslate:

    async def test_translate_movie_found(self):
        client = _make_client()
        # Both calls return the same mock; movie hit + TV hit with same name
        client._client.get.return_value = _mock_response(
            {"results": [{"original_title": "The Crown", "original_name": "The Crown", "popularity": 85.5}]}
        )

        result = await client.translate("王冠")

        assert "The Crown" in result

    async def test_translate_tv_fallback(self):
        client = _make_client()
        # First call (movie) returns nothing, second call (TV) returns a hit
        client._client.get.side_effect = [
            _mock_response({"results": []}),
            _mock_response({"results": [{"original_name": "The Crown", "popularity": 72.3}]}),
        ]

        result = await client.translate("王冠")

        assert result == ["The Crown"]

    async def test_translate_nothing_found(self):
        client = _make_client()
        client._client.get.side_effect = [
            _mock_response({"results": []}),
            _mock_response({"results": []}),
        ]

        result = await client.translate("不存在")

        assert result == []


# ===================================================================
# close
# ===================================================================

class TestClose:

    async def test_close(self):
        client = _make_client()

        await client.close()

        client._client.aclose.assert_awaited_once()
