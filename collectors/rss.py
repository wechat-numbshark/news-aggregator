from __future__ import annotations

import feedparser
import aiohttp
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector, RawArticle
from config.settings import settings


class RSSCollector(BaseCollector):
    """通用 RSS/Atom 采集器，支持新华社、人民日报、Reuters、NHK 等"""

    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self.url = source_config["url"]
        self.timeout = aiohttp.ClientTimeout(total=30)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def fetch(self) -> "list[RawArticle]":
        proxy = settings.http_proxy or None
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        try:
            async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
                async with session.get(self.url, proxy=proxy, ssl=False) as resp:
                    if resp.status != 200:
                        logger.warning(f"[{self.source_id}] HTTP {resp.status}")
                        return []
                    content = await resp.read()
        except Exception as e:
            logger.error(f"[{self.source_id}] 请求失败: {e}")
            return []

        feed = feedparser.parse(content)
        if feed.bozo and not feed.entries:
            logger.warning(f"[{self.source_id}] RSS解析失败: {feed.bozo_exception}")
            return []

        articles = []
        for entry in feed.entries:
            title = self._clean_text(getattr(entry, "title", ""))
            link  = getattr(entry, "link", "") or getattr(entry, "id", "")
            if not title or not link:
                continue

            summary = self._clean_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            )
            image = self._extract_image(entry)
            pub_at = self._parse_date(entry)
            author = self._extract_author(entry)

            articles.append(self._make_article(
                title=title,
                url=link,
                author=author,
                summary=summary or None,
                image_url=image,
                published_at=pub_at,
                raw_data={"feed_title": getattr(feed.feed, "title", "")},
            ))

        logger.info(f"[{self.source_id}] 采集 {len(articles)} 条")
        return articles

    @staticmethod
    def _clean_text(text: str) -> str:
        if not text:
            return ""
        from bs4 import BeautifulSoup
        return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()

    @staticmethod
    def _extract_author(entry) -> str | None:
        author = getattr(entry, "author", None)
        if author:
            return author.strip()[:256]
        tags = getattr(entry, "tags", [])
        for tag in tags:
            if hasattr(tag, "term") and tag.get("scheme", "").endswith("author"):
                return tag.term
        return None

    @staticmethod
    def _extract_image(entry) -> str | None:
        # 尝试从媒体内容获取图片
        media = getattr(entry, "media_content", [])
        if media:
            for m in media:
                if m.get("medium") == "image" or m.get("type", "").startswith("image"):
                    return m.get("url")
        # 从 enclosures 获取
        for enc in getattr(entry, "enclosures", []):
            if enc.get("type", "").startswith("image"):
                return enc.get("href") or enc.get("url")
        return None

    @staticmethod
    def _parse_date(entry) -> datetime | None:
        for field in ("published_parsed", "updated_parsed"):
            val = getattr(entry, field, None)
            if val:
                try:
                    return datetime(*val[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
        for field in ("published", "updated"):
            val = getattr(entry, field, None)
            if val:
                try:
                    return parsedate_to_datetime(val).astimezone(timezone.utc)
                except Exception:
                    pass
        return None
