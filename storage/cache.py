from __future__ import annotations
import redis.asyncio as aioredis
from loguru import logger
from config.settings import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis


async def close_redis():
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


async def is_duplicate(content_hash: str, ttl_seconds: int = 86400 * 7) -> bool:
    """判断文章是否已采集过（基于 hash，7天有效期）"""
    r = await get_redis()
    key = f"article:seen:{content_hash}"
    result = await r.set(key, "1", nx=True, ex=ttl_seconds)
    return result is None


async def mark_source_fetched(source_id: str, ttl_seconds: int = 3600):
    r = await get_redis()
    key = f"source:fetching:{source_id}"
    return await r.set(key, "1", nx=True, ex=ttl_seconds)


async def clear_source_lock(source_id: str):
    r = await get_redis()
    await r.delete(f"source:fetching:{source_id}")


async def get_stats() -> dict:
    r = await get_redis()
    keys = await r.keys("article:seen:*")
    return {"dedup_cache_size": len(keys)}
