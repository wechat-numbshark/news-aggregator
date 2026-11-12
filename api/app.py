"""
新闻聚合 Web 服务
运行: python3 api/app.py
访问: http://localhost:8099
"""
from __future__ import annotations

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from api.source_manager import load_sources, test_source, SOURCES_PATH
import yaml

app = FastAPI(title="新闻聚合", docs_url=None, redoc_url=None)

# ── 状态缓存（内存，重启清空）─────────────────────────────
_test_results: dict[str, dict] = {}
_testing_ids: set[str] = set()
_collect_running = False
_collect_last_result: dict = {}


# ── 数据库初始化（懒加载）────────────────────────────────
_db_ready = False

async def ensure_db():
    global _db_ready
    if not _db_ready:
        from storage.db import init_db
        await init_db()
        _db_ready = True


# ── 静态页面 ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════
# 来源管理 API
# ═══════════════════════════════════════════════════════════

@app.get("/api/sources")
async def get_sources():
    sources = load_sources()
    result = []
    for s in sources:
        cached = _test_results.get(s.id, {})
        is_testing = s.id in _testing_ids
        result.append({
            "id": s.id,
            "name": s.name,
            "flag": s.flag,
            "country": s.country,
            "lang": s.lang_label,
            "type": s.source_type,
            "url": s.url,
            "categories": s.categories,
            "interval": s.interval_label,
            "status": "testing" if is_testing else cached.get("status", "pending"),
            "count": cached.get("count", 0),
            "titles": cached.get("titles", []),
            "error": cached.get("error", ""),
            "latency_ms": cached.get("latency_ms", 0),
        })
    return result


@app.post("/api/sources/{source_id}/test")
async def test_one(source_id: str):
    sources = load_sources()
    cfg = next((s for s in sources if s.id == source_id), None)
    if not cfg:
        return JSONResponse({"error": "未找到"}, status_code=404)
    if source_id in _testing_ids:
        return {"message": "正在测试中"}
    asyncio.create_task(_run_test(source_id))
    return {"message": "开始测试"}


@app.post("/api/sources/test-all")
async def test_all():
    sources = load_sources()
    pending = [s for s in sources if s.id not in _testing_ids]
    for s in pending:
        _testing_ids.add(s.id)

    async def _run_all():
        sem = asyncio.Semaphore(6)
        gdelt_lock = asyncio.Lock()

        async def _one(s):
            if s.source_type == "gdelt":
                async with gdelt_lock:
                    await _run_test(s.id)
                    await asyncio.sleep(6)
            else:
                async with sem:
                    await _run_test(s.id)

        await asyncio.gather(*[_one(s) for s in pending])

    asyncio.create_task(_run_all())
    return {"message": f"开始测试 {len(pending)} 个来源"}


async def _run_test(source_id: str):
    _testing_ids.add(source_id)
    try:
        sources = load_sources()
        cfg_obj = next((s for s in sources if s.id == source_id), None)
        if not cfg_obj:
            return
        with open(SOURCES_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        full_cfg = None
        for group in raw.values():
            if isinstance(group, list):
                for item in group:
                    if item.get("id") == source_id:
                        full_cfg = item
                        break
        if not full_cfg:
            return
        result = await test_source(full_cfg)
        _test_results[source_id] = result
    finally:
        _testing_ids.discard(source_id)


@app.get("/api/stats")
async def stats():
    total = len(load_sources())
    ok = sum(1 for v in _test_results.values() if v.get("status") == "ok")
    error = sum(1 for v in _test_results.values() if v.get("status") == "error")
    skipped = sum(1 for v in _test_results.values() if v.get("status") == "skipped")
    pending = total - ok - error - skipped
    return {"total": total, "ok": ok, "error": error, "skipped": skipped, "pending": pending}


# ═══════════════════════════════════════════════════════════
# 采集 API
# ═══════════════════════════════════════════════════════════

@app.post("/api/collect")
async def collect():
    """触发一次性全量采集"""
    global _collect_running
    if _collect_running:
        return {"message": "采集正在进行中", "running": True}
    asyncio.create_task(_do_collect())
    return {"message": "采集已启动", "running": True}


@app.get("/api/collect/status")
async def collect_status():
    return {"running": _collect_running, "last": _collect_last_result}


async def _do_collect():
    global _collect_running, _collect_last_result
    _collect_running = True
    try:
        await ensure_db()
        from collectors.runner import run_all, sync_sources_to_db
        await sync_sources_to_db()
        result = await run_all(concurrency=4)
        _collect_last_result = result
    except Exception as e:
        _collect_last_result = {"error": str(e)}
    finally:
        _collect_running = False


# ═══════════════════════════════════════════════════════════
# 文章查询 API
# ═══════════════════════════════════════════════════════════

def _article_to_dict(a) -> dict:
    return {
        "id": str(a.id),
        "source_id": a.source_id,
        "source_name": a.source_name,
        "source_country": a.source_country,
        "original_lang": a.original_lang,
        "author": a.author,
        "title_original": a.title_original,
        "title_zh": a.title_zh,
        "summary_zh": a.summary_zh,
        "content_url": a.content_url,
        "image_url": a.image_url,
        "categories": a.categories or [],
        "importance": a.importance,
        "is_translated": a.is_translated,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "collected_at": a.collected_at.isoformat() if a.collected_at else None,
    }


def _build_article_query(Article, country, lang, category, keyword, translated, date_str=""):
    from sqlalchemy import select, or_, cast, String
    from datetime import datetime, timezone, timedelta

    q = select(Article).where(Article.is_filtered_out == False)

    if date_str:
        # 按采集日期筛选（本地时区当天 00:00 ~ 23:59）
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end   = day.replace(hour=23, minute=59, second=59, microsecond=999999)
            q = q.where(Article.collected_at >= day_start, Article.collected_at <= day_end)
        except ValueError:
            pass

    if country:
        q = q.where(Article.source_country == country)
    if lang:
        q = q.where(Article.original_lang == lang)
    if translated == "yes":
        q = q.where(Article.is_translated == True)
    elif translated == "no":
        q = q.where(Article.is_translated == False)
    if keyword:
        kw = f"%{keyword}%"
        q = q.where(or_(
            Article.title_original.ilike(kw),
            Article.title_zh.ilike(kw),
            Article.summary_zh.ilike(kw),
        ))
    if category:
        q = q.where(cast(Article.categories, String).ilike(f"%{category}%"))

    return q


@app.get("/api/articles")
async def get_articles(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    country: str = Query(""),
    lang: str = Query(""),
    category: str = Query(""),
    keyword: str = Query(""),
    translated: str = Query(""),
):
    await ensure_db()
    from sqlalchemy import select, func as sqlfunc
    from storage.db import get_session
    from storage.models import Article

    async with get_session() as session:
        q = _build_article_query(Article, country, lang, category, keyword, translated)
        total = (await session.execute(select(sqlfunc.count()).select_from(q.subquery()))).scalar()
        rows = (await session.execute(
            q.order_by(Article.collected_at.desc()).offset((page - 1) * size).limit(size)
        )).scalars().all()

    return {"total": total, "page": page, "size": size, "items": [_article_to_dict(a) for a in rows]}


@app.get("/api/articles/daily")
async def get_daily_articles(
    date: str = Query(""),          # YYYY-MM-DD，空则取今天
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    country: str = Query(""),
    lang: str = Query(""),
    category: str = Query(""),
    keyword: str = Query(""),
    translated: str = Query(""),
):
    """按日期返回文章，默认今天"""
    await ensure_db()
    from sqlalchemy import select, func as sqlfunc
    from storage.db import get_session
    from storage.models import Article
    from datetime import datetime

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    async with get_session() as session:
        q = _build_article_query(Article, country, lang, category, keyword, translated, date_str=date)
        total = (await session.execute(select(sqlfunc.count()).select_from(q.subquery()))).scalar()
        rows = (await session.execute(
            q.order_by(Article.collected_at.desc()).offset((page - 1) * size).limit(size)
        )).scalars().all()

    return {"total": total, "page": page, "size": size, "date": date,
            "items": [_article_to_dict(a) for a in rows]}


@app.get("/api/articles/daily/stats")
async def daily_stats(date: str = Query("")):
    """返回某天的文章统计（各国/语言分布）"""
    await ensure_db()
    from sqlalchemy import select, func as sqlfunc
    from storage.db import get_session
    from storage.models import Article
    from datetime import datetime

    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    try:
        day = datetime.strptime(date, "%Y-%m-%d")
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end   = day.replace(hour=23, minute=59, second=59, microsecond=999999)
    except ValueError:
        return {"date": date, "total": 0, "translated": 0, "by_country": [], "by_lang": []}

    async with get_session() as session:
        base_where = [
            Article.is_filtered_out == False,
            Article.collected_at >= day_start,
            Article.collected_at <= day_end,
        ]

        total = (await session.execute(
            select(sqlfunc.count()).where(*base_where)
        )).scalar()

        translated = (await session.execute(
            select(sqlfunc.count()).where(*base_where, Article.is_translated == True)
        )).scalar()

        by_country = (await session.execute(
            select(Article.source_country, sqlfunc.count())
            .where(*base_where)
            .group_by(Article.source_country)
            .order_by(sqlfunc.count().desc())
        )).all()

        by_lang = (await session.execute(
            select(Article.original_lang, sqlfunc.count())
            .where(*base_where)
            .group_by(Article.original_lang)
            .order_by(sqlfunc.count().desc())
        )).all()

    return {
        "date": date,
        "total": total,
        "translated": translated,
        "by_country": [{"country": r[0], "count": r[1]} for r in by_country],
        "by_lang": [{"lang": r[0], "count": r[1]} for r in by_lang],
    }


@app.get("/api/articles/available-dates")
async def available_dates():
    """返回数据库中有文章的日期列表（最近30天）"""
    await ensure_db()
    from sqlalchemy import select, func as sqlfunc, cast, Date
    from storage.db import get_session
    from storage.models import Article

    async with get_session() as session:
        rows = (await session.execute(
            select(cast(Article.collected_at, Date), sqlfunc.count())
            .where(Article.is_filtered_out == False)
            .group_by(cast(Article.collected_at, Date))
            .order_by(cast(Article.collected_at, Date).desc())
            .limit(30)
        )).all()

    return [{"date": str(r[0]), "count": r[1]} for r in rows]


@app.get("/api/articles/summary")
async def article_summary():
    """返回文章统计汇总（国家/语言/分类分布）"""
    await ensure_db()
    from sqlalchemy import select, func as sqlfunc, cast, String
    from storage.db import get_session
    from storage.models import Article

    async with get_session() as session:
        total = (await session.execute(
            select(sqlfunc.count()).where(Article.is_filtered_out == False)
        )).scalar()

        translated = (await session.execute(
            select(sqlfunc.count()).where(Article.is_translated == True, Article.is_filtered_out == False)
        )).scalar()

        by_country = (await session.execute(
            select(Article.source_country, sqlfunc.count())
            .where(Article.is_filtered_out == False)
            .group_by(Article.source_country)
            .order_by(sqlfunc.count().desc())
        )).all()

        by_lang = (await session.execute(
            select(Article.original_lang, sqlfunc.count())
            .where(Article.is_filtered_out == False)
            .group_by(Article.original_lang)
            .order_by(sqlfunc.count().desc())
        )).all()

    return {
        "total": total,
        "translated": translated,
        "by_country": [{"country": r[0], "count": r[1]} for r in by_country],
        "by_lang": [{"lang": r[0], "count": r[1]} for r in by_lang],
    }


# ═══════════════════════════════════════════════════════════
# 简报 API
# ═══════════════════════════════════════════════════════════

_briefing_running = False
_briefing_last: dict = {}


@app.post("/api/briefing/generate")
async def briefing_generate(date: str = Query("")):
    """触发生成简报（异步后台任务）"""
    global _briefing_running
    if _briefing_running:
        return {"message": "简报正在生成中", "running": True}
    from datetime import datetime as _dt
    if not date:
        date = _dt.now().strftime("%Y-%m-%d")
    asyncio.create_task(_do_briefing(date))
    return {"message": f"开始生成 {date} 的简报", "running": True, "date": date}


@app.get("/api/briefing/status")
async def briefing_status():
    return {"running": _briefing_running, "last": _briefing_last}


@app.post("/api/briefing/push")
async def briefing_push(date: str = Query("")):
    """手动推送指定日期简报到飞书"""
    await ensure_db()
    from sqlalchemy import select
    from storage.db import get_session
    from storage.models import Briefing
    from datetime import datetime as _dt

    if not date:
        date = _dt.now().strftime("%Y-%m-%d")

    async with get_session() as session:
        row = (await session.execute(
            select(Briefing).where(Briefing.date == date)
        )).scalar_one_or_none()

    if not row:
        return JSONResponse({"error": f"暂无 {date} 的简报，请先生成"}, status_code=404)

    asyncio.create_task(_do_push(row.content))
    return {"message": f"正在推送 {date} 的简报到飞书...", "date": date}


async def _do_push(content: dict):
    try:
        from processors.feishu import push_briefing_to_feishu
        await push_briefing_to_feishu(content)
    except Exception as e:
        from loguru import logger
        logger.error(f"[飞书] 手动推送失败: {e}")


@app.get("/api/briefing/list")
async def briefing_list():
    """返回已生成简报的日期列表"""
    await ensure_db()
    from sqlalchemy import select
    from storage.db import get_session
    from storage.models import Briefing

    async with get_session() as session:
        rows = (await session.execute(
            select(Briefing.date, Briefing.article_count, Briefing.top_count,
                   Briefing.generated_at, Briefing.duration_sec)
            .order_by(Briefing.date.desc())
            .limit(30)
        )).all()

    return [
        {"date": r.date, "article_count": r.article_count, "top_count": r.top_count,
         "generated_at": r.generated_at.isoformat() if r.generated_at else None,
         "duration_sec": r.duration_sec}
        for r in rows
    ]


@app.get("/api/briefing/{date}")
async def briefing_get(date: str):
    """获取指定日期的简报"""
    await ensure_db()
    from sqlalchemy import select
    from storage.db import get_session
    from storage.models import Briefing

    async with get_session() as session:
        row = (await session.execute(
            select(Briefing).where(Briefing.date == date)
        )).scalar_one_or_none()

    if not row:
        return JSONResponse({"error": f"暂无 {date} 的简报"}, status_code=404)

    return row.content


async def _do_briefing(date_str: str):
    global _briefing_running, _briefing_last
    _briefing_running = True
    try:
        await ensure_db()
        from processors.briefing import generate_briefing
        result = await generate_briefing(date_str)
        _briefing_last = {"date": date_str, "status": "ok",
                          "article_count": result.get("article_count"),
                          "top_count": result.get("top_count")}
    except Exception as e:
        from loguru import logger
        logger.exception(f"[简报] 生成失败: {e}")
        _briefing_last = {"date": date_str, "status": "error", "error": str(e)}
    finally:
        _briefing_running = False


@app.get("/api/articles/{article_id}/content")
async def get_article_content(article_id: str):
    """按需抓取文章正文，首次抓取后缓存到数据库"""
    await ensure_db()
    import uuid as _uuid
    from sqlalchemy import select
    from storage.db import get_session
    from storage.models import Article

    try:
        uid = _uuid.UUID(article_id)
    except ValueError:
        return JSONResponse({"error": "invalid id"}, status_code=400)

    async with get_session() as session:
        row = (await session.execute(
            select(Article).where(Article.id == uid)
        )).scalar_one_or_none()

    if not row:
        return JSONResponse({"error": "文章不存在"}, status_code=404)

    # 已有缓存直接返回
    if row.content_text:
        return {"id": article_id, "content": row.content_text, "cached": True}

    # 按需抓取
    if not row.content_url:
        return JSONResponse({"error": "无原文链接"}, status_code=422)

    try:
        import httpx
        import trafilatura
        import re

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        fetch_url = row.content_url
        html = ""

        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            resp = await client.get(fetch_url, headers=headers)
            resp.raise_for_status()
            html = resp.text
            final_url = str(resp.url)

            # Google News 跳转页：解析真实 URL
            if "news.google.com" in final_url:
                # 尝试从 HTML 中找 data-n-au 属性或 meta refresh
                m = re.search(r'data-n-au="([^"]+)"', html)
                if not m:
                    m = re.search(r'<meta[^>]+url=([^\s"\'>;]+)', html, re.I)
                if not m:
                    # 找 window.location 跳转
                    m = re.search(r'window\.location\s*=\s*["\']([^"\']+)["\']', html)
                if m:
                    real_url = m.group(1).replace("&amp;", "&")
                    resp2 = await client.get(real_url, headers=headers)
                    resp2.raise_for_status()
                    html = resp2.text

        if not html:
            return JSONResponse({"error": "页面内容为空"}, status_code=422)

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_recall=True,
        )
        if not text:
            # 备用：用 trafilatura 宽松模式
            text = trafilatura.extract(html, no_fallback=True, favor_recall=True)
        if not text:
            return JSONResponse({"error": "无法提取正文（页面可能需要登录或被反爬保护）"}, status_code=422)

        # 缓存到数据库
        async with get_session() as session:
            obj = (await session.execute(
                select(Article).where(Article.id == uid)
            )).scalar_one()
            obj.content_text = text
            await session.commit()

        return {"id": article_id, "content": text, "cached": False}

    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": f"HTTP {e.response.status_code}"}, status_code=422)
    except httpx.TimeoutException:
        return JSONResponse({"error": "抓取超时（25s），原网站响应过慢"}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)


@app.get("/api/schedule/status")
async def schedule_status():
    return {"collect_hour": 7, "collect_minute": 0, "next_run": _scheduler_next_run()}


def _scheduler_next_run() -> str:
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        job = _scheduler.get_job("daily_collect")
        if job and job.next_run_time:
            return job.next_run_time.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return "未知"


# ── 定时采集（每天 07:00）────────────────────────────────────
from apscheduler.schedulers.asyncio import AsyncIOScheduler

_scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def startup():
    await ensure_db()
    _scheduler.add_job(
        _do_collect,
        trigger="cron",
        hour=7, minute=0,
        id="daily_collect",
        replace_existing=True,
        misfire_grace_time=600,
    )

    async def _daily_briefing():
        from datetime import datetime as _dt
        await _do_briefing(_dt.now().strftime("%Y-%m-%d"))

    _scheduler.add_job(
        _daily_briefing,
        trigger="cron",
        hour=8, minute=0,
        id="daily_briefing",
        replace_existing=True,
        misfire_grace_time=600,
    )

    _scheduler.start()
    from loguru import logger
    logger.info("定时任务已启动：采集 07:00，简报 08:00")
    asyncio.create_task(_startup_catchup())


async def _startup_catchup():
    """启动时检查今日任务是否因重启而错过，错过则补跑。"""
    from datetime import datetime as _dt
    from loguru import logger

    now = _dt.now()
    today = now.strftime("%Y-%m-%d")

    if now.hour < 7:
        return

    await ensure_db()

    from sqlalchemy import select, func
    from storage.db import get_session
    from storage.models import Source

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with get_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Source).where(
                Source.last_fetched_at >= today_start
            )
        )
        fetched_count = result.scalar() or 0

    if fetched_count == 0:
        logger.info("[补跑] 今日未采集，启动采集...")
        await _do_collect()
        logger.info("[补跑] 采集完成")
    else:
        logger.info(f"[补跑] 今日已采集（{fetched_count}个源），跳过")

    if now.hour < 8:
        return

    from storage.models import Briefing
    async with get_session() as session:
        result = await session.execute(
            select(func.count()).select_from(Briefing).where(Briefing.date == today)
        )
        briefing_count = result.scalar() or 0

    if briefing_count == 0:
        logger.info("[补跑] 今日无简报，启动生成+推送...")
        await _do_briefing(today)
        logger.info("[补跑] 简报完成")
    else:
        logger.info(f"[补跑] 今日简报已存在，跳过")


@app.on_event("shutdown")
async def shutdown():
    _scheduler.shutdown(wait=False)
    from storage.db import close_db
    from storage.cache import close_redis
    await close_redis()
    await close_db()


if __name__ == "__main__":
    print("新闻聚合系统: http://localhost:8099")
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
