"""OpenRouter AI 客户端 — 自然语言意图解析"""

import datetime
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# LLM 输出的三种模式
MODE_TMDB = "tmdb"          # 需要调 TMDB API
MODE_RECOMMEND = "recommend" # LLM 直接推荐片名
MODE_DIRECT = "direct"       # 直接搜索关键词

SYSTEM_PROMPT = """你是一个影视搜索助手。用户会用自然语言描述他们想找的影片。

你的任务是分析用户意图，输出 JSON 格式的结构化指令。有三种模式：

### 模式 1: tmdb — 需要查询 TMDB 数据库
当用户提到具体的人物（演员、导演）、年份、国家、类型组合时使用。

人物作品查询：
{"mode": "tmdb", "action": "person_credits", "person": "人物名", "role": "actor 或 director", "media": "movie 或 tv 或 all"}

按条件发现：
{"mode": "tmdb", "action": "discover", "media": "movie 或 tv", "year": 2024, "genre": "类型英文", "region": "国家代码如KR/US/JP"}
（year/genre/region 均可选，省略则不限制）

### 模式 2: recommend — 你直接推荐片名
当用户描述主观偏好、要求推荐、或问"类似XX的"、问某个类别的热门内容时使用。

{"mode": "recommend", "titles": [{"title": "英文原名", "title_cn": "中文名", "year": 年份, "media": "movie 或 tv"}], "reason": "简短说明推荐理由"}

titles 要求：
- title: 英文原名（original title）
- title_cn: 中文名（如果有）
- year: 首播/上映年份
- media: "movie" 或 "tv"
- 最多 10 部，按相关性排序

### 模式 3: direct — 直接搜索
当用户输入本身就是片名时使用。

{"mode": "direct", "keyword": "片名"}

### 规则
- 只输出 JSON，不要解释
- person 字段用用户使用的语言（中文输入就用中文）
- genre 使用英文：action, comedy, drama, thriller, horror, sci-fi, romance, animation, documentary, crime, fantasy, mystery, adventure, war, history, music, family, western
- 优先使用 tmdb 模式（数据更准确），只有主观推荐类才用 recommend 模式
- 注意用户提到的时间范围（"今年"、"最近"、"90年代"等），结合当前日期判断"""


class AIClient:
    """OpenRouter API 客户端"""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, model: str = "deepseek/deepseek-v3.2"):
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(timeout=30.0)

    async def parse_intent(self, user_input: str) -> Optional[dict]:
        """解析用户自然语言输入，返回结构化意图 dict。失败返回 None。"""
        try:
            # 动态注入当前日期
            today = datetime.date.today().isoformat()
            system_content = f"当前日期：{today}\n\n{SYSTEM_PROMPT}"

            resp = await self._client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": user_input},
                    ],
                    "temperature": 0,
                    "max_tokens": 1000,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # 提取 JSON（LLM 可能包裹在 markdown code block 中）
            if content.startswith("```"):
                # Remove ```json and ```
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            return json.loads(content)
        except json.JSONDecodeError:
            logger.warning("AI 返回非 JSON: %s", content[:200] if 'content' in dir() else "N/A")
            return None
        except Exception:
            logger.exception("AI 意图解析失败")
            return None

    async def close(self):
        await self._client.aclose()
