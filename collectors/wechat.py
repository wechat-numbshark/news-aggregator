from __future__ import annotations
import aiohttp
from datetime import datetime, timezone
from loguru import logger

from collectors.base import BaseCollector, RawArticle
from config.settings import settings


class WeChatCollector(BaseCollector):
    """
    微信公众号采集器，通过 WeWe-RSS 实例获取。

    WeWe-RSS 基于微信读书 API，需要自行部署:
    https://github.com/cooderl/wewe-rss

    部署后会提供 RSS 接口:
      GET {base_url}/feeds/{account_id}.xml  → 单个公众号 RSS
      GET {base_url}/feeds/all.xml           → 全部订阅合集

    账号ID获取: 在 WeWe-RSS Web界面添加公众号后，URL中会显示ID
    """

    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self.base_url = settings.wewrss_base_url.rstrip("/")
        self.auth_code = settings.wewrss_auth_code
        self.accounts = source_config.get("accounts", [])
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def fetch(self) -> list[RawArticle]:
        if not self.base_url or self.base_url == "http://localhost:4000":
            logger.warning("[wechat] WeWe-RSS 未配置，跳过。配置 WEWRSS_BASE_URL 后生效")
            return []

        if not self.accounts:
            logger.warning("[wechat] 未配置任何公众号账号ID，跳过")
            return []

        articles = []
        for account_id in self.accounts:
            result = await self._fetch_account(account_id)
            articles.extend(result)

        logger.info(f"[wechat] 共采集 {len(articles)} 条，账号数={len(self.accounts)}")
        return articles

    async def _fetch_account(self, account_id: str) -> list[RawArticle]:
        url = f"{self.base_url}/feeds/{account_id}.xml"
        headers = {}
        if self.auth_code:
            headers["Authorization"] = f"Bearer {self.auth_code}"

        import feedparser
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.warning(f"[wechat:{account_id}] HTTP {resp.status}")
                        return []
                    content = await resp.read()
        except Exception as e:
            logger.error(f"[wechat:{account_id}] 请求失败: {e}")
            return []

        feed = feedparser.parse(content)
        articles = []
        for entry in feed.entries:
            title = (getattr(entry, "title", "") or "").strip()
            link  = getattr(entry, "link", "") or getattr(entry, "id", "")
            if not title or not link:
                continue

            pub_at = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    pub_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass

            articles.append(self._make_article(
                title=title,
                url=link,
                published_at=pub_at,
                raw_data={"account_id": account_id, "feed_title": getattr(feed.feed, "title", "")},
            ))

        return articles
