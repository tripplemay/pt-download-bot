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

    async def search_person(self, name: str) -> Optional[int]:
        """搜索人物，返回 person_id。"""
        url = f"{self.BASE_URL}/search/person"
        params = {
            "api_key": self.api_key,
            "query": name,
            "language": "zh-CN",
        }
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0].get("id")
        except Exception:
            logger.warning("TMDB 人物搜索失败: %s", name, exc_info=True)
        return None

    async def get_person_credits(self, person_id: int, role: str = "actor", media: str = "movie") -> list[dict]:
        """获取人物参演/执导的影视作品列表。

        Returns:
            list[dict]: 每项含 title(英文), title_cn(中文), year, rating。
            按 popularity 降序，最多 20 部。
        """
        items_all = []

        endpoints = []
        if media in ("movie", "all"):
            endpoints.append(("movie", "movie_credits"))
        if media in ("tv", "all"):
            endpoints.append(("tv", "tv_credits"))

        for media_type, credit_type in endpoints:
            url = f"{self.BASE_URL}/person/{person_id}/{credit_type}"
            params = {"api_key": self.api_key, "language": "zh-CN"}
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                if role == "director":
                    items = [c for c in data.get("crew", []) if c.get("job") == "Director"]
                else:
                    items = data.get("cast", [])

                items.sort(key=lambda x: x.get("popularity", 0), reverse=True)

                title_key = "original_title" if media_type == "movie" else "original_name"
                cn_key = "title" if media_type == "movie" else "name"
                date_key = "release_date" if media_type == "movie" else "first_air_date"

                for item in items:
                    en_name = item.get(title_key, "")
                    if not en_name:
                        continue
                    cn_name = item.get(cn_key, "")
                    year = 0
                    date_str = item.get(date_key, "")
                    if date_str and len(date_str) >= 4:
                        try:
                            year = int(date_str[:4])
                        except ValueError:
                            pass
                    rating = item.get("vote_average", 0)

                    # Deduplicate by English title
                    if not any(d["title"] == en_name for d in items_all):
                        items_all.append({
                            "title": en_name,
                            "title_cn": cn_name if cn_name != en_name else "",
                            "year": year,
                            "rating": round(rating, 1),
                        })
            except Exception:
                logger.warning("TMDB 获取人物作品失败: person_id=%d", person_id, exc_info=True)

        return items_all[:20]

    async def discover(self, media: str = "movie", year: int = None,
                       genre: str = None, region: str = None) -> list[dict]:
        """按条件发现影视作品，返回作品列表。"""
        genre_map = {
            "action": 28, "adventure": 12, "animation": 16, "comedy": 35,
            "crime": 80, "documentary": 99, "drama": 18, "family": 10751,
            "fantasy": 14, "history": 36, "horror": 27, "music": 10402,
            "mystery": 9648, "romance": 10749, "sci-fi": 878, "thriller": 53,
            "war": 10752, "western": 37,
        }
        # TV genre IDs differ slightly
        tv_genre_map = {
            "action": 10759, "adventure": 10759, "animation": 16, "comedy": 35,
            "crime": 80, "documentary": 99, "drama": 18, "family": 10751,
            "fantasy": 10765, "horror": 10765, "mystery": 9648, "romance": 10749,
            "sci-fi": 10765, "thriller": 10765, "war": 10768, "western": 37,
        }

        endpoint = "movie" if media == "movie" else "tv"
        url = f"{self.BASE_URL}/discover/{endpoint}"
        params = {
            "api_key": self.api_key,
            "language": "zh-CN",
            "sort_by": "popularity.desc",
        }

        if year:
            if endpoint == "movie":
                params["primary_release_year"] = year
            else:
                params["first_air_date_year"] = year

        if genre:
            gmap = genre_map if endpoint == "movie" else tv_genre_map
            genre_id = gmap.get(genre.lower())
            if genre_id:
                params["with_genres"] = genre_id

        if region:
            if endpoint == "movie":
                params["with_origin_country"] = region.upper()
            else:
                params["with_origin_country"] = region.upper()

        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            title_key = "original_title" if endpoint == "movie" else "original_name"
            cn_key = "title" if endpoint == "movie" else "name"
            date_key = "release_date" if endpoint == "movie" else "first_air_date"

            items = []
            for r in results:
                en_name = r.get(title_key, "")
                if not en_name:
                    continue
                cn_name = r.get(cn_key, "")
                year_val = 0
                date_str = r.get(date_key, "")
                if date_str and len(date_str) >= 4:
                    try:
                        year_val = int(date_str[:4])
                    except ValueError:
                        pass
                items.append({
                    "title": en_name,
                    "title_cn": cn_name if cn_name != en_name else "",
                    "year": year_val,
                    "rating": round(r.get("vote_average", 0), 1),
                })
            return items[:20]
        except Exception:
            logger.warning("TMDB discover 失败", exc_info=True)
            return []

    async def close(self):
        await self._client.aclose()
