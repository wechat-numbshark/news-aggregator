"""
主入口：APScheduler 定时任务调度
  - 每30分钟: 采集所有来源
  - 每10分钟: 翻译处理外文
  - 每15分钟: 飞书推送
"""
import asyncio
import signal
import sys
import os
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from storage.db import init_db, close_db
from storage.cache import close_redis
from collectors.runner import run_all, sync_sources_to_db
from processors.pipeline import process_pending
from publishers.feishu import push_articles


def setup_logging():
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
    )
    os.makedirs("logs", exist_ok=True)
    logger.add(
        "logs/news_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        level="INFO",
        encoding="utf-8",
    )


async def job_collect():
    logger.info("▶ 开始采集任务")
    stats = await run_all(concurrency=5)
    logger.info(f"✓ 采集完成: 新增 {stats['articles_new']} 条")


async def job_translate():
    logger.info("▶ 开始翻译任务")
    count = await process_pending(batch_size=50)
    logger.info(f"✓ 翻译完成: {count} 篇")


async def job_push():
    logger.info("▶ 开始推送任务")
    count = await push_articles(batch_size=20)
    logger.info(f"✓ 推送完成: {count} 条")


async def main():
    setup_logging()
    logger.info("=" * 50)
    logger.info("新闻聚合系统启动")
    logger.info(f"翻译模型: {settings.openrouter_translate_model}")
    logger.info(f"Dry Run: {settings.dry_run}")
    logger.info("=" * 50)

    # 初始化数据库和来源表
    await init_db()
    await sync_sources_to_db()

    # 立即跑一次采集
    await job_collect()
    await job_translate()
    await job_push()

    # 启动定时调度
    scheduler = AsyncIOScheduler()
    scheduler.add_job(job_collect,   IntervalTrigger(minutes=30), id="collect",   max_instances=1, coalesce=True)
    scheduler.add_job(job_translate, IntervalTrigger(minutes=10), id="translate", max_instances=1, coalesce=True)
    scheduler.add_job(job_push,      IntervalTrigger(minutes=15), id="push",      max_instances=1, coalesce=True)
    scheduler.start()

    logger.info("调度器已启动")
    logger.info("  采集: 每30分钟")
    logger.info("  翻译: 每10分钟")
    logger.info("  推送: 每15分钟")

    # 优雅退出
    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("收到退出信号，正在关闭...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    await stop_event.wait()
    scheduler.shutdown(wait=False)
    await close_db()
    await close_redis()
    logger.info("系统已关闭")


if __name__ == "__main__":
    asyncio.run(main())
