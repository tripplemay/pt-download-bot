"""TMDB API 客户端 — 将中文影片名翻译为英文。

TMDB API 免费注册：https://www.themoviedb.org/settings/api
"""

import asyncio
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

    async def search_movie_name(self, chinese_name: str) -> Optional[dict]:
        """用中文片名查询英文片名。返回 {"name": str, "popularity": float} 或 None。"""
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
                top = results[0]
                name = top.get("original_title", "")
                popularity = top.get("popularity", 0)
                if name:
                    return {"name": name, "popularity": popularity}
        except Exception:
            logger.warning("TMDB 电影搜索失败: %s", chinese_name, exc_info=True)
        return None

    async def search_tv_name(self, chinese_name: str) -> Optional[dict]:
        """用中文剧集名查询英文名。返回 {"name": str, "popularity": float} 或 None。"""
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
                top = results[0]
                name = top.get("original_name", "")
                popularity = top.get("popularity", 0)
                if name:
                    return {"name": name, "popularity": popularity}
        except Exception:
            logger.warning("TMDB 剧集搜索失败: %s", chinese_name, exc_info=True)
        return None

    async def translate(self, chinese_name: str) -> list[str]:
        """并行搜索电影和剧集，返回所有匹配的英文名（去重）。

        Returns:
            list[str]: 英文名列表，按 popularity 降序。空列表表示未找到。
        """
        movie_task = self.search_movie_name(chinese_name)
        tv_task = self.search_tv_name(chinese_name)
        movie_result, tv_result = await asyncio.gather(movie_task, tv_task)

        # Sort by popularity descending
        candidates = []
        if movie_result:
            candidates.append(movie_result)
        if tv_result:
            candidates.append(tv_result)
        candidates.sort(key=lambda x: x["popularity"], reverse=True)

        # Deduplicate
        seen = set()
        names = []
        for c in candidates:
            name = c["name"]
            if name not in seen:
                names.append(name)
                seen.add(name)

        return names

    async def close(self):
        await self._client.aclose()
