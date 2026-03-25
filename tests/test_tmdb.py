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


# ===================================================================
# search_person
# ===================================================================

class TestSearchPerson:

    async def test_search_person_found(self):
        client = _make_client()
        client._client.get.return_value = _mock_response(
            {"results": [{"id": 12345, "name": "G-Dragon"}]}
        )

        result = await client.search_person("权志龙")

        assert result == 12345

    async def test_search_person_not_found(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({"results": []})

        result = await client.search_person("不存在的人")

        assert result is None

    async def test_search_person_error(self):
        client = _make_client()
        client._client.get.side_effect = Exception("connection error")

        result = await client.search_person("权志龙")

        assert result is None


# ===================================================================
# get_person_credits
# ===================================================================

class TestGetPersonCredits:

    async def test_actor_movie_credits(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "cast": [
                {"original_title": "Movie A", "popularity": 90.0},
                {"original_title": "Movie B", "popularity": 50.0},
            ],
            "crew": [],
        })

        result = await client.get_person_credits(12345, role="actor", media="movie")

        assert result == ["Movie A", "Movie B"]

    async def test_director_credits(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "cast": [
                {"original_title": "Acted In", "popularity": 80.0},
            ],
            "crew": [
                {"original_title": "Directed A", "popularity": 95.0, "job": "Director"},
                {"original_title": "Produced B", "popularity": 70.0, "job": "Producer"},
            ],
        })

        result = await client.get_person_credits(12345, role="director", media="movie")

        assert result == ["Directed A"]
        assert "Produced B" not in result
        assert "Acted In" not in result

    async def test_empty_credits(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "cast": [],
            "crew": [],
        })

        result = await client.get_person_credits(12345, role="actor", media="movie")

        assert result == []

    async def test_limit_20(self):
        """Should return at most 20 titles even when there are more."""
        client = _make_client()
        cast = [
            {"original_title": f"Movie {i}", "popularity": float(100 - i)}
            for i in range(30)
        ]
        client._client.get.return_value = _mock_response({
            "cast": cast,
            "crew": [],
        })

        result = await client.get_person_credits(12345, role="actor", media="movie")

        assert len(result) == 20

    async def test_tv_credits(self):
        """TV credits should use original_name key."""
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "cast": [
                {"original_name": "TV Show A", "popularity": 90.0},
                {"original_name": "TV Show B", "popularity": 50.0},
            ],
            "crew": [],
        })

        result = await client.get_person_credits(12345, role="actor", media="tv")

        assert result == ["TV Show A", "TV Show B"]

    async def test_all_media_combined(self):
        """media='all' queries both movie and tv endpoints."""
        client = _make_client()
        # First call: movie_credits, second call: tv_credits
        client._client.get.side_effect = [
            _mock_response({
                "cast": [{"original_title": "Movie X", "popularity": 90.0}],
                "crew": [],
            }),
            _mock_response({
                "cast": [{"original_name": "TV Show Y", "popularity": 80.0}],
                "crew": [],
            }),
        ]

        result = await client.get_person_credits(12345, role="actor", media="all")

        assert "Movie X" in result
        assert "TV Show Y" in result


# ===================================================================
# discover
# ===================================================================

class TestDiscover:

    async def test_discover_movies_by_year(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_title": "Film A"},
                {"original_title": "Film B"},
            ]
        })

        result = await client.discover(media="movie", year=2024)

        assert result == ["Film A", "Film B"]
        # Verify year param was passed
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["primary_release_year"] == 2024

    async def test_discover_with_genre(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_title": "Sci-Fi Film"},
            ]
        })

        result = await client.discover(media="movie", genre="sci-fi")

        assert result == ["Sci-Fi Film"]
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["with_genres"] == 878

    async def test_discover_empty(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({"results": []})

        result = await client.discover(media="movie", year=1800)

        assert result == []

    async def test_discover_tv(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_name": "TV Drama A"},
            ]
        })

        result = await client.discover(media="tv", year=2024)

        assert result == ["TV Drama A"]
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["first_air_date_year"] == 2024

    async def test_discover_with_region(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_title": "Korean Film"},
            ]
        })

        result = await client.discover(media="movie", region="KR")

        assert result == ["Korean Film"]
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["with_origin_country"] == "KR"

    async def test_discover_error(self):
        client = _make_client()
        client._client.get.side_effect = Exception("API error")

        result = await client.discover(media="movie")

        assert result == []
