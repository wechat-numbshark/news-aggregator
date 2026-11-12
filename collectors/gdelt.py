from __future__ import annotations
import aiohttp
from datetime import datetime, timezone
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector, RawArticle
from config.settings import settings

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"


class GDELTCollector(BaseCollector):
    """
    GDELT DOC 2.0 API 采集器。
    完全免费，每15分钟更新，覆盖100+语言，65种机器翻译到英文。
    适合全球事件补充兜底。
    文档: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
    """

    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self.query = source_config.get("gdelt_query", "")
        self.mode = source_config.get("gdelt_mode", "ArtList")
        self.max_records = source_config.get("max_records", 25)
        self.timespan = source_config.get("gdelt_timespan", "1w")
        self.timeout = aiohttp.ClientTimeout(total=30)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=15))
    async def fetch(self) -> list[RawArticle]:
        params = {
            "query": self.query,
            "mode": self.mode,
            "maxrecords": self.max_records,
            "format": "json",
            "timespan": self.timespan,
            "sort": "DateDesc",
        }
        proxy = settings.http_proxy or None

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(GDELT_API, params=params, proxy=proxy) as resp:
                    if resp.status == 429:
                        logger.warning(f"[gdelt:{self.source_id}] 限速(429)，等待后重试")
                        raise Exception("GDELT限速(429)，正常运行时15分钟间隔不会触发")
                    if resp.status != 200:
                        logger.warning(f"[gdelt:{self.source_id}] HTTP {resp.status}")
                        return []
                    data = await resp.json(content_type=None)
        except Exception as e:
            logger.error(f"[gdelt:{self.source_id}] 请求失败: {e}")
            return []

        articles = []
        items = data.get("articles", [])
        for item in items:
            title = (item.get("title") or "").strip()
            url   = (item.get("url") or "").strip()
            if not title or not url:
                continue

            lang = self._map_lang(item.get("language", ""))
            pub_at = self._parse_date(item.get("seendate", ""))

            articles.append(self._make_article(
                title=title,
                url=url,
                published_at=pub_at,
                raw_data={
                    "domain": item.get("domain"),
                    "language": item.get("language"),
                    "country": item.get("sourcecountry"),
                    "socialimage": item.get("socialimage"),
                },
            ))
            # 用 GDELT 返回的实际语言覆盖
            articles[-1].original_lang = lang
            # 图片
            if item.get("socialimage"):
                articles[-1].image_url = item["socialimage"]

        logger.info(f"[{self.source_id}] GDELT 采集 {len(articles)} 条，query='{self.query}'")
        return articles

    @staticmethod
    def _map_lang(gdelt_lang: str) -> str:
        mapping = {
            "Chinese": "zh", "English": "en", "Japanese": "ja",
            "Korean": "ko", "German": "de", "French": "fr",
            "Spanish": "es", "Russian": "ru",
        }
        return mapping.get(gdelt_lang, gdelt_lang.lower()[:2] if gdelt_lang else "en")

    @staticmethod
    def _parse_date(seendate: str) -> datetime | None:
        # GDELT格式: 20240520T123000Z
        try:
            return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            return None
