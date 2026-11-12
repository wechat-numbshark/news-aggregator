"""
源管理后端逻辑：测试每个来源的连通性和采集能力
不依赖数据库，纯网络测试
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
import yaml
from pathlib import Path

from collectors.rss import RSSCollector
from collectors.rsshub import RSSHubCollector
from collectors.gdelt import GDELTCollector
from collectors.nyt import NYTCollector
from collectors.wechat import WeChatCollector

SOURCES_PATH = Path(__file__).parent.parent / "config" / "sources.yaml"

COLLECTOR_MAP = {
    "rss":     RSSCollector,
    "rsshub":  RSSHubCollector,
    "gdelt":   GDELTCollector,
    "nyt_api": NYTCollector,
    "wechat":  WeChatCollector,
}

COUNTRY_FLAG = {
    "CN": "🇨🇳", "US": "🇺🇸", "JP": "🇯🇵",
    "GB": "🇬🇧", "DE": "🇩🇪", "FR": "🇫🇷",
    "ES": "🇪🇸", "GLOBAL": "🌍",
}

LANG_LABEL = {
    "zh": "中文", "en": "英文", "ja": "日文",
    "de": "德文", "fr": "法文", "es": "西班牙文",
    "mixed": "多语言",
}


@dataclass
class SourceStatus:
    id: str
    name: str
    country: str
    lang: str
    source_type: str
    categories: list[str]
    interval: int
    url: str = ""

    # 测试结果
    status: str = "pending"          # pending | testing | ok | error | skipped
    article_count: int = 0
    sample_titles: list[str] = field(default_factory=list)
    error_msg: str = ""
    latency_ms: int = 0
    tested_at: float = 0.0

    @property
    def flag(self):
        return COUNTRY_FLAG.get(self.country, "🌐")

    @property
    def lang_label(self):
        return LANG_LABEL.get(self.lang, self.lang)

    @property
    def interval_label(self):
        m = self.interval // 60
        return f"{m}分钟" if m < 60 else f"{m//60}小时"


def load_sources() -> list[SourceStatus]:
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    sources = []
    for group_name, group_items in raw.items():
        if not isinstance(group_items, list):
            continue
        for cfg in group_items:
            url = (
                cfg.get("url")
                or cfg.get("rsshub_path")
                or cfg.get("gdelt_query")
                or cfg.get("nyt_section")
                or "(WeChat)"
            )
            sources.append(SourceStatus(
                id=cfg["id"],
                name=cfg["name"],
                country=cfg.get("country", ""),
                lang=cfg.get("lang", ""),
                source_type=cfg.get("type", ""),
                categories=cfg.get("categories", []),
                interval=cfg.get("interval", 1800),
                url=url,
            ))
    return sources


async def test_source(cfg: dict) -> dict:
    """测试单个来源，返回状态字典"""
    source_type = cfg.get("type", "")
    collector_cls = COLLECTOR_MAP.get(source_type)

    if not collector_cls:
        return {"status": "skipped", "error": f"未知类型: {source_type}", "count": 0, "titles": [], "latency_ms": 0}

    # GDELT 每5秒限1次，快速测试会触发429，正式运行(15min)完全正常
    if source_type == "gdelt":
        return {"status": "skipped", "error": "GDELT限速(1次/5s)，单次测试跳过；正式运行(15min间隔)正常", "count": 0, "titles": [], "latency_ms": 0}

    # WeChat 特殊处理
    if source_type == "wechat":
        from config.settings import settings
        if not settings.wewrss_base_url or "localhost" in settings.wewrss_base_url:
            return {"status": "skipped", "error": "WeWe-RSS 未部署", "count": 0, "titles": [], "latency_ms": 0}

    t0 = time.monotonic()
    try:
        collector = collector_cls(cfg)
        articles = await asyncio.wait_for(collector.fetch(), timeout=20)
        latency = int((time.monotonic() - t0) * 1000)

        if articles:
            return {
                "status": "ok",
                "error": "",
                "count": len(articles),
                "titles": [a.title[:60] for a in articles[:3]],
                "latency_ms": latency,
            }
        else:
            return {
                "status": "error",
                "error": "采集到 0 条，可能源地址失效",
                "count": 0,
                "titles": [],
                "latency_ms": latency,
            }
    except asyncio.TimeoutError:
        return {"status": "error", "error": "超时（20s）", "count": 0, "titles": [], "latency_ms": 20000}
    except Exception as e:
        latency = int((time.monotonic() - t0) * 1000)
        # 解包 tenacity RetryError，拿到真实原因
        cause = e
        if hasattr(e, "last_attempt") and e.last_attempt.failed:
            cause = e.last_attempt.exception()
        msg = str(cause)[:120]
        skip_keywords = ("未配置", "未部署", "GDELT限速", "429", "API_KEY", "developer.nytimes")
        status = "skipped" if any(k in msg for k in skip_keywords) else "error"
        return {"status": status, "error": msg, "count": 0, "titles": [], "latency_ms": latency}
