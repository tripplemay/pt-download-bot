"""Tests for the AI client module."""

from unittest.mock import AsyncMock, MagicMock, patch

from bot.ai import AIClient


def _make_ai_client():
    """Create an AIClient with httpx.AsyncClient patched out."""
    with patch("bot.ai.httpx.AsyncClient"):
        client = AIClient("fake_key", model="test-model")
    client._client = AsyncMock()
    return client


def _mock_response(json_data: dict, status_code: int = 200):
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


# ===================================================================
# parse_intent
# ===================================================================

class TestParseIntent:

    async def test_parse_tmdb_person(self):
        """LLM returns person_credits intent."""
        client = _make_ai_client()
        client._client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": '{"mode": "tmdb", "action": "person_credits", "person": "\\u6743\\u5fd7\\u9f99", "role": "actor", "media": "movie"}'}}]
        }))

        result = await client.parse_intent("权志龙演的电影")
        assert result["mode"] == "tmdb"
        assert result["action"] == "person_credits"
        assert result["person"] == "权志龙"
        assert result["role"] == "actor"
        assert result["media"] == "movie"

    async def test_parse_recommend(self):
        """LLM returns recommend intent."""
        client = _make_ai_client()
        client._client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": '{"mode": "recommend", "titles": ["Inception", "Interstellar"], "reason": "classic sci-fi"}'}}]
        }))

        result = await client.parse_intent("类似盗梦空间的电影")
        assert result["mode"] == "recommend"
        assert result["titles"] == ["Inception", "Interstellar"]
        assert result["reason"] == "classic sci-fi"

    async def test_parse_direct(self):
        """LLM returns direct intent."""
        client = _make_ai_client()
        client._client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": '{"mode": "direct", "keyword": "Interstellar"}'}}]
        }))

        result = await client.parse_intent("Interstellar")
        assert result["mode"] == "direct"
        assert result["keyword"] == "Interstellar"

    async def test_parse_with_markdown_wrapper(self):
        """LLM wraps JSON in ```json code block — should still parse correctly."""
        client = _make_ai_client()
        client._client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": '```json\n{"mode": "direct", "keyword": "test"}\n```'}}]
        }))

        result = await client.parse_intent("test")
        assert result["mode"] == "direct"
        assert result["keyword"] == "test"

    async def test_parse_failure_invalid_json(self):
        """LLM returns non-JSON -> returns None."""
        client = _make_ai_client()
        client._client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": "I don't understand your request"}}]
        }))

        result = await client.parse_intent("gibberish")
        assert result is None

    async def test_parse_failure_api_error(self):
        """API call fails -> returns None."""
        client = _make_ai_client()
        client._client.post = AsyncMock(side_effect=Exception("connection error"))

        result = await client.parse_intent("test")
        assert result is None

    async def test_parse_tmdb_discover(self):
        """LLM returns discover intent."""
        client = _make_ai_client()
        client._client.post = AsyncMock(return_value=_mock_response({
            "choices": [{"message": {"content": '{"mode": "tmdb", "action": "discover", "media": "movie", "year": 2024, "genre": "sci-fi", "region": "KR"}'}}]
        }))

        result = await client.parse_intent("2024年韩国科幻片")
        assert result["mode"] == "tmdb"
        assert result["action"] == "discover"
        assert result["year"] == 2024
        assert result["genre"] == "sci-fi"
        assert result["region"] == "KR"


# ===================================================================
# close
# ===================================================================

class TestAIClientClose:

    async def test_close(self):
        """close() calls _client.aclose()."""
        client = _make_ai_client()

        await client.close()

        client._client.aclose.assert_awaited_once()
