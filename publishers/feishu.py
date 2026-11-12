from __future__ import annotations
"""
飞书 Webhook 推送。
不同分类推送到不同的飞书群，使用富文本卡片格式。
分类 → Webhook 映射:
  stock_cn  → FEISHU_WEBHOOK_STOCK_CN
  stock_us  → FEISHU_WEBHOOK_STOCK_US
  startup / opportunity → FEISHU_WEBHOOK_STARTUP
  其他      → FEISHU_WEBHOOK_GENERAL
"""
import asyncio
from datetime import datetime, timezone

import aiohttp
from loguru import logger
from sqlalchemy import select, and_

from config.settings import settings
from storage.db import get_session
from storage.models import Article, PushLog

CATEGORY_WEBHOOK_MAP = {
    "stock_cn":   lambda: settings.feishu_webhook_stock_cn,
    "stock_us":   lambda: settings.feishu_webhook_stock_us,
    "startup":    lambda: settings.feishu_webhook_startup,
    "opportunity":lambda: settings.feishu_webhook_startup,
    "macro":      lambda: settings.feishu_webhook_general,
    "tech":       lambda: settings.feishu_webhook_general,
    "general":    lambda: settings.feishu_webhook_general,
}

CATEGORY_LABEL = {
    "stock_cn":    "📈 A股",
    "stock_us":    "🌐 外股",
    "startup":     "🚀 创业",
    "opportunity": "💡 机会",
    "macro":       "🏛️ 宏观",
    "tech":        "💻 科技",
    "general":     "📰 资讯",
}

FLAG_MAP = {
    "CN": "🇨🇳", "US": "🇺🇸", "JP": "🇯🇵",
    "GB": "🇬🇧", "DE": "🇩🇪", "GLOBAL": "🌍",
}


def _build_card(article: Article) -> dict:
    """构建飞书互动卡片消息"""
    flag = FLAG_MAP.get(article.source_country, "🌐")
    cats = article.categories or []
    cat_labels = " ".join(CATEGORY_LABEL.get(c, c) for c in cats[:3])
    importance_stars = "⭐" * min(article.importance or 0, 5) if (article.importance or 0) >= 7 else ""

    title = article.title_zh or article.title_original
    summary = article.summary_zh or ""

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": f"{importance_stars}{title}"},
                "template": _importance_color(article.importance or 0),
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "content": (
                            f"{flag} **{article.source_name}** | {cat_labels}\n"
                            f"{summary}\n"
                            f"🕐 {_format_time(article.published_at)}"
                        ),
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "阅读原文"},
                                "type": "primary",
                                "multi_url": {"url": article.content_url or ""},
                            }
                        ],
                    },
                ]
            },
        },
    }


def _importance_color(importance: int) -> str:
    if importance >= 8:
        return "red"
    elif importance >= 6:
        return "orange"
    elif importance >= 4:
        return "blue"
    return "default"


def _format_time(dt: datetime | None) -> str:
    if not dt:
        return "未知时间"
    local = dt.astimezone()
    return local.strftime("%m-%d %H:%M")


def _pick_webhook(categories: list[str]) -> str | None:
    """根据分类选择 webhook，优先级: stock_cn > stock_us > startup > opportunity > 其他"""
    priority = ["stock_cn", "stock_us", "startup", "opportunity", "macro", "tech", "general"]
    for cat in priority:
        if cat in categories:
            url = CATEGORY_WEBHOOK_MAP.get(cat, lambda: "")()
            if url:
                return url
    return settings.feishu_webhook_general or None


async def _send_one(session: aiohttp.ClientSession, webhook_url: str, payload: dict) -> tuple[bool, str]:
    try:
        async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            text = await resp.text()
            success = resp.status == 200 and '"StatusCode":0' in text
            return success, text[:200]
    except Exception as e:
        return False, str(e)


async def push_articles(batch_size: int = 20) -> int:
    """推送待推送的文章到飞书，返回推送数量"""
    if settings.dry_run:
        logger.info("[dry_run] 跳过飞书推送")
        return 0

    async with get_session() as session:
        stmt = (
            select(Article)
            .where(
                and_(
                    Article.is_translated == True,
                    Article.is_pushed == False,
                    Article.is_filtered_out == False,
                )
            )
            .order_by(Article.importance.desc(), Article.published_at.desc())
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        articles = result.scalars().all()

    if not articles:
        logger.debug("没有待推送文章")
        return 0

    pushed = 0
    async with aiohttp.ClientSession() as http_session:
        for article in articles:
            webhook = _pick_webhook(article.categories or [])
            if not webhook:
                logger.warning(f"文章 {article.id} 无对应 webhook，跳过")
                continue

            card = _build_card(article)
            success, resp_text = await _send_one(http_session, webhook, card)

            async with get_session() as db_session:
                db_article = await db_session.get(Article, article.id)
                if db_article:
                    db_article.is_pushed = True
                    db_article.pushed_at = datetime.now(timezone.utc)

                db_session.add(PushLog(
                    article_id=article.id,
                    channel=webhook[-20:],  # 只存末尾作标识
                    success=success,
                    response=resp_text,
                ))

            if success:
                pushed += 1
            else:
                logger.warning(f"推送失败: {article.title_zh or article.title_original[:30]}, resp={resp_text}")

            await asyncio.sleep(0.2)  # 避免飞书限速

    logger.info(f"飞书推送完成: {pushed}/{len(articles)}")
    return pushed
