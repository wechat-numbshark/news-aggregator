from __future__ import annotations
"""
采集层主入口：加载 sources.yaml，按 type 实例化对应 Collector，
写入数据库（去重），返回新增文章数量。
"""
import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Type

import yaml
from loguru import logger
from sqlalchemy import select

from collectors.base import BaseCollector, RawArticle
from collectors.rss import RSSCollector
from collectors.rsshub import RSSHubCollector
from collectors.gdelt import GDELTCollector
from collectors.nyt import NYTCollector
from collectors.wechat import WeChatCollector
from storage.db import get_session
from storage.cache import is_duplicate
from storage.models import Article, Source

COLLECTOR_MAP: dict[str, Type[BaseCollector]] = {
    "rss":     RSSCollector,
    "rsshub":  RSSHubCollector,
    "gdelt":   GDELTCollector,
    "nyt_api": NYTCollector,
    "wechat":  WeChatCollector,
}

_SOURCES_PATH = Path(__file__).parent.parent / "config" / "sources.yaml"


def load_all_sources() -> list[dict]:
    with open(_SOURCES_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    sources = []
    for group_sources in raw.values():
        if isinstance(group_sources, list):
            sources.extend(group_sources)
    return sources


_CAT_IMPORTANCE = {
    "stock_cn":   8,
    "stock_us":   8,
    "opportunity":7,
    "startup":    7,
    "macro":      6,
    "tech":       5,
    "general":    3,
}

def _calc_importance(categories: list[str]) -> int:
    if not categories:
        return 3
    return max(_CAT_IMPORTANCE.get(c, 3) for c in categories)


async def save_articles(articles: list[RawArticle]) -> int:
    """去重后批量保存到 DB，返回实际新增数"""
    if not articles:
        return 0

    new_count = 0
    async with get_session() as session:
        for article in articles:
            # Redis 快速去重
            if await is_duplicate(article.content_hash):
                continue

            obj = Article(
                source_id=article.source_id,
                source_name=article.source_name,
                source_country=article.source_country,
                source_type=article.source_type,
                original_lang=article.original_lang,
                author=article.author,
                title_original=article.title,
                content_url=article.content_url,
                image_url=article.image_url,
                published_at=article.published_at,
                categories=article.categories or [],
                importance=_calc_importance(article.categories or []),
                content_hash=article.content_hash,
                raw_json=article.raw_data,
                summary_zh=article.summary if article.original_lang == "zh" else None,
            )
            # 中文来源直接复制标题
            if article.original_lang == "zh":
                obj.title_zh = article.title
                obj.is_translated = True

            try:
                session.add(obj)
                await session.flush()
                new_count += 1
            except Exception:
                await session.rollback()
                # 唯一约束冲突，忽略
                continue

    return new_count


async def run_collector(source_cfg: dict) -> int:
    """运行单个来源的采集，返回新增数量"""
    source_type = source_cfg.get("type", "")
    collector_cls = COLLECTOR_MAP.get(source_type)

    if not collector_cls:
        logger.warning(f"未知来源类型: {source_type}，source_id={source_cfg.get('id')}")
        return 0

    collector = collector_cls(source_cfg)
    try:
        articles = await collector.fetch()
        new = await save_articles(articles)
        logger.info(f"[{source_cfg['id']}] 采集={len(articles)} 新增={new}")

        # 更新来源最后采集时间
        async with get_session() as session:
            src = await session.get(Source, source_cfg["id"])
            if src:
                src.last_fetched_at = datetime.now(timezone.utc)
                src.error_count = 0

        return new
    except Exception as e:
        logger.error(f"[{source_cfg['id']}] 采集异常: {e}")
        async with get_session() as session:
            src = await session.get(Source, source_cfg["id"])
            if src:
                src.error_count = (src.error_count or 0) + 1
                src.last_error = str(e)
        return 0


async def run_all(concurrency: int = 5) -> dict:
    """并发采集所有来源，返回统计"""
    sources = load_all_sources()
    logger.info(f"开始采集，共 {len(sources)} 个来源，并发={concurrency}")

    semaphore = asyncio.Semaphore(concurrency)

    async def limited(cfg):
        async with semaphore:
            return await run_collector(cfg)

    results = await asyncio.gather(*[limited(s) for s in sources], return_exceptions=True)

    total_new = sum(r for r in results if isinstance(r, int))
    errors = sum(1 for r in results if isinstance(r, Exception))

    stats = {
        "sources_total": len(sources),
        "sources_error": errors,
        "articles_new": total_new,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"采集完成: {stats}")
    return stats


async def sync_sources_to_db():
    """把 sources.yaml 中的来源同步到 DB sources 表"""
    sources = load_all_sources()
    async with get_session() as session:
        for cfg in sources:
            existing = await session.get(Source, cfg["id"])
            if not existing:
                session.add(Source(
                    id=cfg["id"],
                    name=cfg["name"],
                    country=cfg["country"],
                    source_type=cfg["type"],
                    url=cfg.get("url") or cfg.get("rsshub_path") or cfg.get("gdelt_query"),
                    fetch_interval=cfg.get("interval", 1800),
                    config_json=cfg,
                ))
    logger.info(f"来源表同步完成，共 {len(sources)} 条")
