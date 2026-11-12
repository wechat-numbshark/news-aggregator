"""
飞书简报推送模块

流程：
  Stage 3: 深度洞察 AI 分析（蝴蝶效应/反共识/跨行业链条）
  推送：   5张飞书交互卡片，全部含来源链接

卡片结构：
  Card 1 - 今日概览（蓝）：日期、元数据、一段话总结
  Card 2 - 重大事项（红）：5条 headline + 重要性 + 影响 + 来源
  Card 3 - 市场信号（绿）：A股/美股/宏观 三列信号
  Card 4 - 潜在机会 + 深度洞察（橙）：机会 + Stage3 AI 分析
  Card 5 - 行动建议（紫）：3条具体建议
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Optional

import httpx
from loguru import logger

from config.settings import settings

DEEP_MODEL = "openai/gpt-oss-120b:free"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Stage 3 深度洞察 Prompt ────────────────────────────────────

STAGE3_PROMPT = """你是一位洞察力极强的宏观对冲基金经理，专门发现别人看不到的机会和风险。

基于今日已筛选的全球新闻精华（由初步分析整理）：

{briefing_summary}

请从以下三个维度进行深度分析（每项200字以内，要有具体数字和逻辑链）：

1. 【蝴蝶效应】今日有哪1-2条乍看不起眼的新闻，3-6个月后可能产生超出预期的连锁影响？请点名具体新闻，说明传导路径。

2. 【反共识机会】当前市场普遍悲观/乐观的判断里，哪个最可能是错的？反向操作的逻辑和风险是什么？

3. 【跨行业暗线】今日哪个行业的新闻，会在1个月内悄悄影响到另一个看似无关的行业？请说明传导机制和受益标的。

直接输出纯文字，不要JSON，不要标题编号之外的格式。每个维度用【标题】开头，中间换行分隔。"""


async def _call_openrouter_text(prompt: str, client: httpx.AsyncClient) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://news-aggregator.local",
        "X-Title":       "News Briefing Deep Analysis",
    }
    payload = {
        "model": DEEP_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
        "max_tokens": 2048,
    }
    for attempt in range(3):
        resp = await client.post(OPENROUTER_URL, json=payload, headers=headers, timeout=120)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            logger.warning(f"[飞书] Stage3 429限速，{wait}s后重试")
            await asyncio.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        if not data.get("choices"):
            raise RuntimeError(f"无choices: {str(data)[:200]}")
        text = data["choices"][0]["message"]["content"].strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text
    raise RuntimeError("Stage3 429重试3次仍失败")


async def generate_deep_insights(briefing: dict, client: httpx.AsyncClient) -> str:
    """Stage 3：基于简报生成深度洞察"""
    s = briefing.get("sections", {})
    summary_lines = [f"总结：{s.get('summary', '')}"]
    for h in s.get("headlines", []):
        summary_lines.append(f"- {h['title']}（影响：{h.get('impact','')}）")
    for o in s.get("opportunities", []):
        summary_lines.append(f"- 机会：{o['title']}｜{o.get('detail','')[:60]}")

    prompt = STAGE3_PROMPT.format(briefing_summary="\n".join(summary_lines))
    logger.info("[飞书] Stage3 深度洞察分析中...")
    text = await _call_openrouter_text(prompt, client)
    logger.info("[飞书] Stage3 完成")
    return text


# ── 飞书卡片构建 ──────────────────────────────────────────────

def _md_link(name: str, url: str) -> str:
    if url and url.startswith("http"):
        return f"[{name}]({url})"
    return name


def _sources_md(sources: list) -> str:
    if not sources:
        return ""
    links = [_md_link(s.get("name", "来源"), s.get("url", "")) for s in sources]
    return "  来源：" + " · ".join(links)


def _direction_icon(direction: str) -> str:
    return {"利好": "📈", "利空": "📉", "中性": "➡️"}.get(direction, "•")


def _importance_bar(n) -> str:
    n = int(n) if n else 0
    if n >= 9: return "🔴🔴🔴"
    if n >= 7: return "🟠🟠"
    return "🟡"


def _send_card(webhook: str, card: dict, client: httpx.Client) -> bool:
    try:
        resp = client.post(webhook, json={"msg_type": "interactive", "card": card}, timeout=15)
        result = resp.json()
        ok = result.get("code") == 0
        if not ok:
            logger.warning(f"[飞书] 推送失败: {result}")
        return ok
    except Exception as e:
        logger.error(f"[飞书] 推送异常: {e}")
        return False


def _header(title: str, template: str = "blue") -> dict:
    return {
        "title": {"tag": "plain_text", "content": title},
        "template": template,
    }


def _div(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text}}


def _hr() -> dict:
    return {"tag": "hr"}


def _note(text: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text}]}


# ── Card 1：今日概览 ──────────────────────────────────────────

def build_card1(briefing: dict) -> dict:
    s = briefing.get("sections", {})
    date = briefing.get("date", datetime.now().strftime("%Y-%m-%d"))
    art_cnt = briefing.get("article_count", 0)
    top_cnt = briefing.get("top_count", 0)
    gen_at = briefing.get("generated_at", "")[:16].replace("T", " ")

    elements = [
        _div(f"**📅 {date}**　｜　📰 处理文章 **{art_cnt}** 篇 → 精选 **{top_cnt}** 篇\n🤖 生成时间 {gen_at}"),
        _hr(),
        _div(f"**🌐 今日总结**\n\n{s.get('summary', '（无）')}"),
        _note(f"模型：{briefing.get('model_scoring','')} + {briefing.get('model_analysis','')}"),
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"📋 每日全球新闻简报  {date}", "blue"),
        "elements": elements,
    }


# ── Card 2：重大事项 ──────────────────────────────────────────

def build_card2(briefing: dict) -> dict:
    s = briefing.get("sections", {})
    headlines = s.get("headlines", [])
    elements = []

    for i, h in enumerate(headlines):
        bar = _importance_bar(h.get("importance"))
        title_line = f"**{bar} {h['title']}**"
        summary_line = h.get("summary", "")
        impact_line = f"> 📌 影响：{h.get('impact', '')}" if h.get("impact") else ""
        src_line = _sources_md(h.get("sources", []))

        body = "\n".join(filter(None, [title_line, summary_line, impact_line, src_line]))
        elements.append(_div(body))
        if i < len(headlines) - 1:
            elements.append(_hr())

    return {
        "config": {"wide_screen_mode": True},
        "header": _header("🔥 重大事项", "red"),
        "elements": elements or [_div("暂无数据")],
    }


# ── Card 3：市场信号 ──────────────────────────────────────────

def _render_signals(signals: list, label: str) -> str:
    if not signals:
        return f"**{label}**\n暂无数据"
    lines = [f"**{label}**"]
    for sig in signals:
        icon = _direction_icon(sig.get("direction", "中性"))
        direction = sig.get("direction", "中性")
        signal_text = sig.get("signal", "")
        analysis = sig.get("analysis", "")
        src = _sources_md(sig.get("sources", []))
        lines.append(f"\n{icon} **[{direction}]** {signal_text}")
        if analysis:
            lines.append(f"  _{analysis}_")
        if src:
            lines.append(src)
    return "\n".join(lines)


def build_card3(briefing: dict) -> dict:
    mkt = briefing.get("sections", {}).get("market", {})
    a_text  = _render_signals(mkt.get("a_share", []),  "📈 A股信号")
    us_text = _render_signals(mkt.get("us_share", []), "🗽 美股信号")
    mc_text = _render_signals(mkt.get("macro", []),    "🏛 宏观信号")

    return {
        "config": {"wide_screen_mode": True},
        "header": _header("📊 市场信号", "green"),
        "elements": [
            _div(a_text),
            _hr(),
            _div(us_text),
            _hr(),
            _div(mc_text),
        ],
    }


# ── Card 4：潜在机会 + 深度洞察 ──────────────────────────────

def build_card4(briefing: dict, deep_insights: str) -> dict:
    opps = briefing.get("sections", {}).get("opportunities", [])
    elements = []

    # 潜在机会
    for i, o in enumerate(opps):
        title_line = f"**💡 {o['title']}**"
        detail_line = o.get("detail", "")
        insight_line = f"> 🤖 **AI视角**：{o['ai_insight']}" if o.get("ai_insight") else ""
        action_line = f"**→ 可操作**：{o['actionable']}" if o.get("actionable") else ""
        src_line = _sources_md(o.get("sources", []))

        body = "\n".join(filter(None, [title_line, detail_line, insight_line, action_line, src_line]))
        elements.append(_div(body))
        if i < len(opps) - 1:
            elements.append(_hr())

    # Stage 3 深度洞察
    if deep_insights:
        elements.append(_hr())
        elements.append(_div(f"**🔬 AI 深度洞察**\n\n{deep_insights}"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header("💡 潜在机会 & 深度洞察", "orange"),
        "elements": elements or [_div("暂无数据")],
    }


# ── Card 5：行动建议 ──────────────────────────────────────────

def build_card5(briefing: dict) -> dict:
    personals = briefing.get("sections", {}).get("personal", [])
    date = briefing.get("date", "")
    elements = []

    for i, p in enumerate(personals):
        timeframe = p.get("timeframe", "")
        tf_badge = f"  `⏱ {timeframe}`" if timeframe else ""
        action_line = f"**✅ {p['action']}**{tf_badge}"
        reason_line = p.get("reason", "")
        body = "\n".join(filter(None, [action_line, reason_line]))
        elements.append(_div(body))
        if i < len(personals) - 1:
            elements.append(_hr())

    elements.append(_hr())
    elements.append(_note(f"🔗 查看完整简报：http://localhost:8099  ｜  日期：{date}"))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header("✅ 个人行动建议", "purple"),
        "elements": elements or [_div("暂无数据")],
    }


# ── 主推送入口 ────────────────────────────────────────────────

async def push_briefing_to_feishu(briefing: dict) -> bool:
    webhooks = [w for w in [
        settings.feishu_briefing_webhook,
        settings.feishu_briefing_webhook2,
    ] if w]
    if not webhooks:
        logger.warning("[飞书] FEISHU_BRIEFING_WEBHOOK 未配置，跳过推送")
        return False

    logger.info(f"[飞书] 开始推送简报到飞书（{len(webhooks)}个地址）...")

    # Stage 3 深度洞察
    deep_insights = ""
    try:
        async with httpx.AsyncClient() as client:
            deep_insights = await generate_deep_insights(briefing, client)
    except Exception as e:
        logger.warning(f"[飞书] Stage3 深度洞察失败（不影响推送）: {e}")

    # 构建 5 张卡片
    cards = [
        ("概览",   build_card1(briefing)),
        ("重大事项", build_card2(briefing)),
        ("市场信号", build_card3(briefing)),
        ("机会洞察", build_card4(briefing, deep_insights)),
        ("行动建议", build_card5(briefing)),
    ]

    total_success = 0
    with httpx.Client() as client:
        for webhook in webhooks:
            success = 0
            for name, card in cards:
                ok = _send_card(webhook, card, client)
                logger.info(f"[飞书] {webhook[-8:]} 卡片「{name}」{'✓' if ok else '✗'}")
                if ok:
                    success += 1
                await asyncio.sleep(1.5)   # 飞书限速：不超过5条/秒
            logger.info(f"[飞书] {webhook[-8:]} 推送完成 {success}/{len(cards)} 张卡片")
            total_success += success

    return total_success > 0


# ── 手动触发（用于测试）──────────────────────────────────────

async def push_latest_briefing():
    """推送最新一条简报（用于测试或手动触发）"""
    from storage.db import get_session, init_db
    from storage.models import Briefing
    from sqlalchemy import select
    await init_db()

    async with get_session() as session:
        row = (await session.execute(
            select(Briefing).order_by(Briefing.date.desc()).limit(1)
        )).scalar_one_or_none()

    if not row:
        logger.error("[飞书] 没有找到任何简报")
        return False

    logger.info(f"[飞书] 推送简报: {row.date}")
    return await push_briefing_to_feishu(row.content)
