"""
处理管道：从 DB 拉取未翻译的外文文章 → 翻译 → 更新 DB
"""
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, and_

from processors.translator import translator
from storage.db import get_session
from storage.models import Article
from config.settings import settings


async def process_pending(batch_size: int = 50) -> int:
    """处理待翻译的外文文章，返回处理数量"""
    async with get_session() as session:
        # 取未翻译的外文文章
        stmt = (
            select(Article)
            .where(
                and_(
                    Article.is_translated == False,
                    Article.original_lang != "zh",
                    Article.is_filtered_out == False,
                )
            )
            .order_by(Article.collected_at.desc())
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        articles = result.scalars().all()

    if not articles:
        logger.debug("没有待翻译的文章")
        return 0

    logger.info(f"待翻译文章: {len(articles)} 篇")

    # 准备翻译批次
    items = [
        {
            "id": str(a.id),
            "title": a.title_original,
            "lang": a.original_lang,
            "summary": a.summary_zh,  # 可能有英文摘要
        }
        for a in articles
    ]

    translations = await translator.translate_batch(items, concurrency=5)
    trans_map = {t["id"]: t for t in translations}

    # 写回 DB
    updated = 0
    async with get_session() as session:
        for article in articles:
            trans = trans_map.get(str(article.id))
            if not trans:
                continue

            db_article = await session.get(Article, article.id)
            if not db_article:
                continue

            db_article.title_zh      = trans.get("title_zh") or article.title_original
            db_article.summary_zh    = trans.get("summary_zh") or ""
            db_article.is_translated = True
            db_article.translated_at = datetime.now(timezone.utc)

            # 合并分类（来源预设 + AI判断）
            ai_cats = trans.get("categories", [])
            existing = set(db_article.categories or [])
            db_article.categories = list(existing | set(ai_cats))

            # 重要程度
            importance = trans.get("importance", 0)
            if isinstance(importance, int):
                db_article.importance = min(max(importance, 0), 10)

            updated += 1

    logger.info(f"翻译完成: {updated} 篇")
    return updated
