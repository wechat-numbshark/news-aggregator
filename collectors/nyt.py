from __future__ import annotations
import aiohttp
from datetime import datetime, timezone
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector, RawArticle
from config.settings import settings

NYT_TOP_STORIES_URL = "https://api.nytimes.com/svc/topstories/v2/{section}.json"


class NYTCollector(BaseCollector):
    """
    NYT Top Stories API 采集器。
    免费，500次/天，5次/分钟。
    返回标题+摘要+URL，不含全文。
    申请: https://developer.nytimes.com
    """

    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self.section = source_config.get("nyt_section", "home")
        self.api_key = settings.nyt_api_key
        self.timeout = aiohttp.ClientTimeout(total=20)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    async def fetch(self) -> list[RawArticle]:
        if not self.api_key:
            raise Exception("NYT_API_KEY 未配置 — 免费申请: developer.nytimes.com")


        url = NYT_TOP_STORIES_URL.format(section=self.section)
        params = {"api-key": self.api_key}
        proxy = settings.http_proxy or None

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, params=params, proxy=proxy) as resp:
                    if resp.status == 429:
                        logger.warning(f"[{self.source_id}] NYT 限速")
                        return []
                    if resp.status != 200:
                        logger.warning(f"[{self.source_id}] NYT HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[{self.source_id}] NYT请求失败: {e}")
            return []

        articles = []
        for item in data.get("results", []):
            title = (item.get("title") or "").strip()
            url   = (item.get("url") or "").strip()
            if not title or not url:
                continue

            summary = (item.get("abstract") or "").strip() or None
            image = self._extract_image(item.get("multimedia", []))
            pub_at = self._parse_date(item.get("published_date", ""))

            a = self._make_article(
                title=title,
                url=url,
                summary=summary,
                image_url=image,
                published_at=pub_at,
                raw_data={
                    "section": item.get("section"),
                    "subsection": item.get("subsection"),
                    "byline": item.get("byline"),
                    "des_facet": item.get("des_facet", []),
                },
            )
            articles.append(a)

        logger.info(f"[{self.source_id}] NYT {self.section} 采集 {len(articles)} 条")
        return articles

    @staticmethod
    def _extract_image(multimedia: list) -> str | None:
        for m in multimedia:
            if m.get("format") in ("Super Jumbo", "Large Thumbnail", "Normal"):
                return m.get("url")
        return multimedia[0].get("url") if multimedia else None

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            return None
