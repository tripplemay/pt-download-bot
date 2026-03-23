"""TMDB API 客户端 — 将中文影片名翻译为英文。

TMDB API 免费注册：https://www.themoviedb.org/settings/api
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

class TMDBClient:
    """调用 TMDB API 将中文片名翻译为英文。"""

    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=10.0)

    async def search_movie_name(self, chinese_name: str) -> Optional[str]:
        """用中文片名查询英文片名，返回 original_title。"""
        url = f"{self.BASE_URL}/search/movie"
        params = {
            "api_key": self.api_key,
            "query": chinese_name,
            "language": "zh-CN",
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0].get("original_title")
        except Exception:
            logger.warning("TMDB 电影搜索失败: %s", chinese_name, exc_info=True)
        return None

    async def search_tv_name(self, chinese_name: str) -> Optional[str]:
        """用中文剧集名查询英文名，返回 original_name。"""
        url = f"{self.BASE_URL}/search/tv"
        params = {
            "api_key": self.api_key,
            "query": chinese_name,
            "language": "zh-CN",
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0].get("original_name")
        except Exception:
            logger.warning("TMDB 剧集搜索失败: %s", chinese_name, exc_info=True)
        return None

    async def translate(self, chinese_name: str) -> Optional[str]:
        """综合搜索：先搜电影，没结果再搜剧集。"""
        result = await self.search_movie_name(chinese_name)
        if not result:
            result = await self.search_tv_name(chinese_name)
        return result

    async def close(self):
        await self._client.aclose()
