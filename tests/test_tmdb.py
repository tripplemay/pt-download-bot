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
                {"original_title": "Movie A", "title": "电影A", "release_date": "2020-05-01", "vote_average": 8.0, "popularity": 90.0},
                {"original_title": "Movie B", "title": "电影B", "release_date": "2019-03-15", "vote_average": 7.5, "popularity": 50.0},
            ],
            "crew": [],
        })

        result = await client.get_person_credits(12345, role="actor", media="movie")

        assert len(result) == 2
        assert result[0]["title"] == "Movie A"
        assert result[0]["title_cn"] == "电影A"
        assert result[0]["year"] == 2020
        assert result[0]["rating"] == 8.0
        assert result[1]["title"] == "Movie B"

    async def test_director_credits(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "cast": [
                {"original_title": "Acted In", "title": "参演片", "release_date": "2018-01-01", "vote_average": 6.0, "popularity": 80.0},
            ],
            "crew": [
                {"original_title": "Directed A", "title": "导演A", "release_date": "2020-06-01", "vote_average": 8.5, "popularity": 95.0, "job": "Director"},
                {"original_title": "Produced B", "title": "制片B", "release_date": "2019-01-01", "vote_average": 7.0, "popularity": 70.0, "job": "Producer"},
            ],
        })

        result = await client.get_person_credits(12345, role="director", media="movie")

        assert len(result) == 1
        assert result[0]["title"] == "Directed A"
        titles = [r["title"] for r in result]
        assert "Produced B" not in titles
        assert "Acted In" not in titles

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
            {"original_title": f"Movie {i}", "title": f"电影{i}", "release_date": f"20{i:02d}-01-01", "vote_average": 7.0, "popularity": float(100 - i)}
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
                {"original_name": "TV Show A", "name": "电视剧A", "first_air_date": "2021-09-01", "vote_average": 8.2, "popularity": 90.0},
                {"original_name": "TV Show B", "name": "电视剧B", "first_air_date": "2020-03-01", "vote_average": 7.8, "popularity": 50.0},
            ],
            "crew": [],
        })

        result = await client.get_person_credits(12345, role="actor", media="tv")

        assert len(result) == 2
        assert result[0]["title"] == "TV Show A"
        assert result[0]["title_cn"] == "电视剧A"
        assert result[0]["year"] == 2021
        assert result[1]["title"] == "TV Show B"

    async def test_all_media_combined(self):
        """media='all' queries both movie and tv endpoints."""
        client = _make_client()
        # First call: movie_credits, second call: tv_credits
        client._client.get.side_effect = [
            _mock_response({
                "cast": [{"original_title": "Movie X", "title": "电影X", "release_date": "2022-01-01", "vote_average": 8.0, "popularity": 90.0}],
                "crew": [],
            }),
            _mock_response({
                "cast": [{"original_name": "TV Show Y", "name": "电视剧Y", "first_air_date": "2021-06-01", "vote_average": 7.5, "popularity": 80.0}],
                "crew": [],
            }),
        ]

        result = await client.get_person_credits(12345, role="actor", media="all")

        titles = [r["title"] for r in result]
        assert "Movie X" in titles
        assert "TV Show Y" in titles


# ===================================================================
# discover
# ===================================================================

class TestDiscover:

    async def test_discover_movies_by_year(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_title": "Film A", "title": "影片A", "release_date": "2024-03-01", "vote_average": 7.5},
                {"original_title": "Film B", "title": "影片B", "release_date": "2024-06-15", "vote_average": 8.0},
            ]
        })

        result = await client.discover(media="movie", year=2024)

        assert len(result) == 2
        assert result[0]["title"] == "Film A"
        assert result[0]["title_cn"] == "影片A"
        assert result[0]["year"] == 2024
        assert result[1]["title"] == "Film B"
        # Verify year param was passed
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["primary_release_year"] == 2024

    async def test_discover_with_genre(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_title": "Sci-Fi Film", "title": "科幻片", "release_date": "2024-01-01", "vote_average": 7.0},
            ]
        })

        result = await client.discover(media="movie", genre="sci-fi")

        assert len(result) == 1
        assert result[0]["title"] == "Sci-Fi Film"
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
                {"original_name": "TV Drama A", "name": "电视剧A", "first_air_date": "2024-04-01", "vote_average": 8.0},
            ]
        })

        result = await client.discover(media="tv", year=2024)

        assert len(result) == 1
        assert result[0]["title"] == "TV Drama A"
        assert result[0]["title_cn"] == "电视剧A"
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["first_air_date_year"] == 2024

    async def test_discover_with_region(self):
        client = _make_client()
        client._client.get.return_value = _mock_response({
            "results": [
                {"original_title": "Korean Film", "title": "韩国片", "release_date": "2024-01-01", "vote_average": 7.5},
            ]
        })

        result = await client.discover(media="movie", region="KR")

        assert len(result) == 1
        assert result[0]["title"] == "Korean Film"
        call_kwargs = client._client.get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs.kwargs["params"]
        assert params["with_origin_country"] == "KR"

    async def test_discover_error(self):
        client = _make_client()
        client._client.get.side_effect = Exception("API error")

        result = await client.discover(media="movie")

        assert result == []
