"""
每日新闻简报生成器 - 三阶段漏斗
Stage 0: 规则预筛（今日文章，按importance排序，最多1500篇）
Stage 1: 批量评分（qwen3-235b:free，每批60条，取Top 80）
Stage 2: 深度分析（gemini-2.0-flash:free，输出结构化JSON简报）
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

SCORING_MODEL  = "openai/gpt-oss-20b:free"
ANALYSIS_MODEL = "openai/gpt-oss-120b:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

STAGE1_BATCH  = 60
TOP_N         = 80
MAX_ARTICLES  = 600


# ── HTTP 调用 OpenRouter ──────────────────────────────────────

async def _call_openrouter(model: str, prompt: str, client: httpx.AsyncClient,
                           timeout: int = 120, max_tokens: int = 4096) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://news-aggregator.local",
        "X-Title":       "News Briefing",
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    # 429 自动重试（最多3次，间隔递增）
    for attempt in range(3):
        resp = await client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            logger.warning(f"[简报] 429限速，{wait}s后重试 (attempt {attempt+1}/3)")
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"OpenRouter API错误: {data['error']}")
        if not data.get("choices"):
            raise RuntimeError(f"响应无choices字段: {str(data)[:200]}")
        text = data["choices"][0]["message"]["content"].strip()
        # 去掉 <think>...</think> 思考链（部分模型输出）
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text
    raise RuntimeError("OpenRouter 429 重试3次仍失败")


# ── Stage 0：规则预筛 ─────────────────────────────────────────

async def stage0_filter(date_str: str) -> list[dict]:
    """
    两层配额筛选，保证分类和国家双维度均衡：
      Layer 1: 按分类目标篇数分配（低分类先抢，防被高分类挤走）+ 国家内均衡
      Layer 2: 剩余槽按国家比例补充，保国家多样性
    """
    import math
    from sqlalchemy import select, func as sqlfunc
    from storage.db import get_session
    from storage.models import Article
    from datetime import datetime

    # ── 可调参数 ──────────────────────────────────────────────────────────
    # 每分类目标篇数（多标签匹配：只要 categories 含该分类就算）
    # 处理顺序：从低重要性到高重要性，低分类先锁定名额
    CAT_TARGETS = {
        "general":     40,   # 低分类先抢，保证普通新闻有出现
        "tech":        60,
        "macro":       90,
        "startup":     70,
        "opportunity": 70,
        "stock_us":    90,
        "stock_cn":    70,
    }
    # 总目标 490，剩余 110 留给国家配额补充
    # 每国在单分类内最多贡献的比例上限（防单国垄断某类）
    PER_COUNTRY_CAP_RATIO = 0.5
    # 国家配额补充时每国保底篇数
    MIN_PER_COUNTRY = 10
    # ─────────────────────────────────────────────────────────────────────

    _CAT_IMP = {"stock_cn": 8, "stock_us": 8, "opportunity": 7,
                "startup": 7, "macro": 6, "tech": 5, "general": 3}

    def primary_cat(categories):
        if not categories:
            return "general"
        return max(categories, key=lambda c: _CAT_IMP.get(c, 3))

    # ── 拉全量当天文章（importance 降序）─────────────────────────
    day = datetime.strptime(date_str, "%Y-%m-%d")
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = day.replace(hour=23, minute=59, second=59, microsecond=999999)

    async with get_session() as session:
        rows = (await session.execute(
            select(Article.id, Article.title_original, Article.source_name,
                   Article.source_country, Article.original_lang,
                   Article.categories, Article.importance, Article.content_url,
                   Article.summary_zh)
            .where(
                Article.is_filtered_out == False,
                Article.collected_at >= day_start,
                Article.collected_at <= day_end,
                Article.categories != None,
                sqlfunc.array_length(Article.categories, 1) > 0,
            )
            .order_by(Article.importance.desc())
        )).all()

    used_ids: set = set()
    selected_by_cat: dict[str, list] = {cat: [] for cat in CAT_TARGETS}

    # ── Layer 1: 按分类目标分配（低分类先处理）───────────────────
    # CAT_TARGETS 已按从低到高顺序定义（general → stock_cn）
    for cat, target in CAT_TARGETS.items():
        # 多标签候选池：含该 tag 且未被任何分类选过
        pool = [r for r in rows if cat in (r.categories or []) and str(r.id) not in used_ids]
        if not pool:
            continue

        # 每国上限 = max(target * cap_ratio, 2)，防单国垄断
        per_country_cap = max(math.ceil(target * PER_COUNTRY_CAP_RATIO), 2)
        country_taken: dict[str, int] = {}

        for r in pool:
            if len(selected_by_cat[cat]) >= target:
                break
            c = r.source_country or "OTHER"
            if country_taken.get(c, 0) >= per_country_cap:
                continue
            selected_by_cat[cat].append(r)
            country_taken[c] = country_taken.get(c, 0) + 1
            used_ids.add(str(r.id))

    # ── Layer 2: 国家配额补充剩余槽 ──────────────────────────────
    remaining_target = MAX_ARTICLES - sum(len(v) for v in selected_by_cat.values())
    layer2_selected = []
    if remaining_target > 0:
        leftover = [r for r in rows if str(r.id) not in used_ids]
        by_country: dict[str, list] = {}
        for r in leftover:
            by_country.setdefault(r.source_country or "OTHER", []).append(r)

        n_countries = len(by_country)
        total_left  = len(leftover)
        country_remain = max(0, remaining_target - n_countries * MIN_PER_COUNTRY)

        country_quotas: dict[str, int] = {}
        for c, arts in by_country.items():
            prop = len(arts) / total_left if total_left else 1 / n_countries
            country_quotas[c] = min(MIN_PER_COUNTRY + math.floor(prop * country_remain), len(arts))

        fill_allocated = sum(country_quotas.values())
        for c in sorted(by_country, key=lambda x: -len(by_country[x])):
            if fill_allocated >= remaining_target:
                break
            add = min(len(by_country[c]) - country_quotas[c], remaining_target - fill_allocated)
            country_quotas[c] += add
            fill_allocated += add

        for c, arts in by_country.items():
            layer2_selected.extend(arts[:country_quotas[c]])

    # ── 合并、去重、排序 ─────────────────────────────────────────
    all_selected = []
    for cat_arts in selected_by_cat.values():
        all_selected.extend(cat_arts)
    all_selected.extend(layer2_selected)

    # 按 importance 降序排列
    all_selected.sort(key=lambda r: r.importance, reverse=True)

    # ── 构建 article_cat_map：记录每篇文章在 Layer1 中的"意图分类" ──
    # 用于让 AI 看到最准确的分类标签（而非 primary_cat 可能的高分类覆盖）
    article_cat_map: dict[str, str] = {}
    for cat, arts in selected_by_cat.items():
        for r in arts:
            article_cat_map[str(r.id)] = cat   # Layer1 意图分类优先
    # Layer2 补充的文章，回退到 primary_cat
    for r in layer2_selected:
        if str(r.id) not in article_cat_map:
            article_cat_map[str(r.id)] = primary_cat(r.categories)

    # ── 构建输出，统计分类/国家 ──────────────────────────────────
    articles = []
    cat_total: dict[str, int] = {}
    country_total: dict[str, int] = {}

    for r in all_selected:
        # 用意图分类作为 cat 字段，让 AI 读到正确的类别
        effective_cat = article_cat_map.get(str(r.id), primary_cat(r.categories))
        cat_total[effective_cat]  = cat_total.get(effective_cat, 0) + 1
        c = r.source_country or "OTHER"
        country_total[c]          = country_total.get(c, 0) + 1
        articles.append({
            "id":      str(r.id),
            "title":   r.title_original[:200] if r.title_original else "",
            "src":     r.source_name or "",
            "country": c,
            "lang":    r.original_lang or "",
            "cat":     effective_cat,
            "url":     r.content_url or "",
            "zh":      r.summary_zh or "",
        })

    l1_dist = {cat: len(arts) for cat, arts in selected_by_cat.items() if arts}
    logger.info(
        f"[简报] Stage0 筛选: {len(articles)} 篇 | "
        f"分类: {cat_total} | "
        f"L2补充: {len(layer2_selected)} | "
        f"国家: {country_total}"
    )
    return articles


# ── Stage 1：批量评分 ─────────────────────────────────────────

STAGE1_PROMPT = """对下方每条新闻，输出重要性评分(1-10)和≤20字中文摘要。
评分标准：与股市/宏观/创业/科技突破/地缘政治的相关度。
仅输出JSON数组，不要其他内容。

格式：[{{"id":"1","s":8,"zh":"一句话摘要"}},...]

新闻：
{news_json}"""


async def stage1_score(articles: list[dict], client: httpx.AsyncClient) -> list[dict]:
    """分批评分，返回带score+zh字段的文章列表，按score降序取Top N"""
    scored: dict[str, dict] = {}

    batches = [articles[i:i+STAGE1_BATCH] for i in range(0, len(articles), STAGE1_BATCH)]
    logger.info(f"[简报] Stage1 共 {len(batches)} 批，每批最多 {STAGE1_BATCH} 条")

    async def _score_batch(batch: list[dict], batch_idx: int):
        news_input = [{"id": str(i), "title": a["title"], "src": a["src"], "cat": a["cat"]}
                      for i, a in enumerate(batch)]
        prompt = STAGE1_PROMPT.format(news_json=json.dumps(news_input, ensure_ascii=False))
        try:
            raw = await _call_openrouter(SCORING_MODEL, prompt, client, timeout=90)
            # 去掉可能的markdown代码块
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            try:
                results = json.loads(raw)
            except json.JSONDecodeError:
                from json_repair import repair_json
                results = json.loads(repair_json(raw))
            for item in results:
                idx = int(item["id"])
                if 0 <= idx < len(batch):
                    art = batch[idx].copy()
                    art["score"] = int(item.get("s", 5))
                    if item.get("zh"):
                        art["zh"] = item["zh"]
                    scored[art["id"]] = art
            logger.info(f"[简报] 批次 {batch_idx+1}/{len(batches)} 完成，得分 {len(results)} 条")
        except Exception as e:
            logger.warning(f"[简报] 批次 {batch_idx+1} 失败: {e}")
            # 失败批次给默认分5
            for art in batch:
                a = art.copy()
                a["score"] = 5
                scored[a["id"]] = a

    # 并发执行，最多2批同时跑（避免免费模型限速）
    sem = asyncio.Semaphore(2)
    async def _limited(batch, idx):
        async with sem:
            await _score_batch(batch, idx)

    await asyncio.gather(*[_limited(b, i) for i, b in enumerate(batches)])

    top = sorted(scored.values(), key=lambda x: x.get("score", 0), reverse=True)[:TOP_N]
    logger.info(f"[简报] Stage1 完成，Top {len(top)} 篇")
    return top


# ── Stage 2：深度分析 ─────────────────────────────────────────

STAGE2_PROMPT = """你是顶级投资分析师，擅长从全球新闻中发现中国投资者容易忽视的机会。

基于以下精选新闻生成今日中文简报。要求：
1. headlines：3-5条真正重大的全球事件
2. market.a_share：3条对A股最相关的信号（方向+分析）
3. market.us_share：3条美股信号
4. market.macro：2条宏观信号
5. opportunities：3-5条潜在机会（重点挖掘：供应链机会、产业趋势、表面不起眼但AI视角发现的隐性商机）
6. personal：3条具体可执行的个人行动建议
7. summary：3-5句话的今日总结

每条必须带 sources:[{{"name":"来源名","url":"文章URL"}}]。
严格输出JSON，不要markdown代码块，不要其他文字。

JSON结构：
{{
  "summary": "...",
  "headlines": [{{"title":"...","summary":"...","impact":"...","importance":9,"sources":[{{"name":"","url":""}}]}}],
  "market": {{
    "a_share": [{{"signal":"...","direction":"利好/利空/中性","analysis":"...","sources":[{{"name":"","url":""}}]}}],
    "us_share": [...],
    "macro":    [...]
  }},
  "opportunities": [{{"title":"...","detail":"...","ai_insight":"...","actionable":"...","sources":[{{"name":"","url":""}}]}}],
  "personal":      [{{"action":"...","reason":"...","timeframe":"..."}}]
}}

今日精选新闻（{n}条）：
{articles_json}"""


async def stage2_analyze(top_articles: list[dict], client: httpx.AsyncClient) -> dict:
    """深度分析，返回结构化简报JSON"""
    articles_input = [
        {"title": a["title"], "zh": a.get("zh", ""), "src": a["src"],
         "url": a["url"], "score": a.get("score", 5), "cat": a["cat"]}
        for a in top_articles
    ]
    prompt = STAGE2_PROMPT.format(
        n=len(articles_input),
        articles_json=json.dumps(articles_input, ensure_ascii=False, indent=None)
    )

    raw = await _call_openrouter(ANALYSIS_MODEL, prompt, client, timeout=240, max_tokens=8192)
    raw = raw.strip()

    # 去掉 markdown 代码块
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            if part.startswith("json"):
                raw = part[4:].strip()
                break
            elif part.strip().startswith("{"):
                raw = part.strip()
                break

    # 提取最外层 JSON 对象（防止前后有多余文字）
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end+1]

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[简报] Stage2 JSON解析失败({e})，尝试 json_repair 修复...")
        try:
            from json_repair import repair_json
            result = json.loads(repair_json(raw))
            logger.info("[简报] Stage2 json_repair 修复成功")
        except Exception as e2:
            logger.error(f"[简报] Stage2 修复失败: {e2}，重试一次...")
            raw2 = await _call_openrouter(ANALYSIS_MODEL, prompt, client, timeout=240, max_tokens=8192)
            raw2 = raw2.strip()
            if "```" in raw2:
                for part in raw2.split("```"):
                    if part.startswith("json"):
                        raw2 = part[4:].strip(); break
                    elif part.strip().startswith("{"):
                        raw2 = part.strip(); break
            s, en = raw2.find("{"), raw2.rfind("}")
            if s != -1 and en > s:
                raw2 = raw2[s:en+1]
            try:
                result = json.loads(raw2)
            except json.JSONDecodeError:
                from json_repair import repair_json
                result = json.loads(repair_json(raw2))

    logger.info("[简报] Stage2 深度分析完成")
    return result


# ── 主入口 ───────────────────────────────────────────────────

async def generate_briefing(date_str: Optional[str] = None) -> dict:
    """生成指定日期的简报，返回完整简报dict（含元数据）"""
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    if not settings.openrouter_api_key:
        raise ValueError("OPENROUTER_API_KEY 未配置")

    t0 = time.monotonic()
    logger.info(f"[简报] 开始生成 {date_str} 的简报")

    async with httpx.AsyncClient() as client:
        # Stage 0
        articles = await stage0_filter(date_str)
        if not articles:
            raise ValueError(f"日期 {date_str} 没有可用文章")

        # Stage 1
        top_articles = await stage1_score(articles, client)

        # Stage 2
        sections = await stage2_analyze(top_articles, client)

    duration = int(time.monotonic() - t0)

    briefing = {
        "date":           date_str,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "article_count":  len(articles),
        "top_count":      len(top_articles),
        "model_scoring":  SCORING_MODEL,
        "model_analysis": ANALYSIS_MODEL,
        "sections":       sections,
    }

    # 存库
    await _save_briefing(date_str, briefing, len(articles), len(top_articles), duration)

    logger.info(f"[简报] 生成完成，耗时 {duration}s")

    # 推送飞书
    try:
        from processors.feishu import push_briefing_to_feishu
        await push_briefing_to_feishu(briefing)
    except Exception as e:
        logger.warning(f"[飞书] 推送失败（不影响简报）: {e}")

    return briefing


async def _save_briefing(date_str: str, content: dict, article_count: int,
                          top_count: int, duration_sec: int):
    from storage.db import get_session
    from storage.models import Briefing
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with get_session() as session:
        stmt = pg_insert(Briefing).values(
            date=date_str,
            content=content,
            article_count=article_count,
            top_count=top_count,
            model_scoring=SCORING_MODEL,
            model_analysis=ANALYSIS_MODEL,
            duration_sec=duration_sec,
        ).on_conflict_do_update(
            index_elements=["date"],
            set_=dict(
                content=content,
                article_count=article_count,
                top_count=top_count,
                generated_at=datetime.now(timezone.utc),
                duration_sec=duration_sec,
            )
        )
        await session.execute(stmt)
        await session.commit()
    logger.info(f"[简报] 已保存到数据库: {date_str}")
