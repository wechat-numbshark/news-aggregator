from __future__ import annotations
"""
翻译 + 摘要处理器，使用 OpenRouter API。

模型选择策略（性价比排序）:
  1. qwen/qwen3-235b-a22b:free   - 免费，中/英/日极强，批处理首选
  2. deepseek/deepseek-chat-v3   - 极便宜($0.27/1M)，中文最强，英文好
  3. google/gemini-2.0-flash-exp:free - 免费快速备用
  4. anthropic/claude-haiku-4-5  - 质量高但有成本，用于重要文章

处理逻辑: 外文标题+摘要 → 中文标题+中文简要摘要（2-3句话）
同时判断文章分类（stock_cn / stock_us / startup / opportunity / general）
"""
import asyncio
import json
from typing import Optional

import aiohttp
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TRANSLATE_PROMPT = """你是一个专业的全球资讯分析助手。请处理以下新闻条目：

来源语言: {lang}
标题: {title}
摘要: {summary}

请以JSON格式返回（只返回JSON，不要其他内容）:
{{
  "title_zh": "中文标题（精准翻译或意译，15-30字）",
  "summary_zh": "中文摘要（2-3句，150字以内，突出核心信息）",
  "categories": ["分类标签数组，从以下选择: stock_cn(A股/中国股市), stock_us(美股/外国股票), startup(创业/融资/新公司), opportunity(商业机会/赚钱/新趋势), macro(宏观经济/政策), tech(科技/AI), general(其他)"],
  "importance": 重要程度0-10的整数(10最重要，股票行情、重大政策、大额融资给高分)
}}"""


class Translator:
    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_translate_model
        self.fast_model = settings.openrouter_fast_model
        self.timeout = aiohttp.ClientTimeout(total=60)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
    async def translate_article(
        self,
        title: str,
        lang: str,
        summary: Optional[str] = None,
        use_fast: bool = False,
    ) -> dict:
        """翻译单篇文章，返回 {title_zh, summary_zh, categories, importance}"""
        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY 未配置，跳过翻译")
            return {"title_zh": title, "summary_zh": summary or "", "categories": [], "importance": 0}

        model = self.fast_model if use_fast else self.model
        prompt = TRANSLATE_PROMPT.format(
            lang=lang,
            title=title,
            summary=summary or "（无摘要）",
        )

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/news-aggregator",
            "X-Title": "News Aggregator",
        }

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(OPENROUTER_URL, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"OpenRouter {resp.status}: {text[:200]}")
                        raise Exception(f"HTTP {resp.status}")
                    data = await resp.json()

            content = data["choices"][0]["message"]["content"]
            result = json.loads(content)
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}, 原始: {content[:100]}")
            return {"title_zh": title, "summary_zh": summary or "", "categories": [], "importance": 0}

    async def translate_batch(
        self,
        items: list[dict],   # [{"id": uuid, "title": str, "lang": str, "summary": str}]
        concurrency: int = 5,
    ) -> list[dict]:
        """批量翻译，返回 [{"id": uuid, ...翻译结果}]"""
        semaphore = asyncio.Semaphore(concurrency)

        async def _translate_one(item: dict):
            async with semaphore:
                result = await self.translate_article(
                    title=item["title"],
                    lang=item["lang"],
                    summary=item.get("summary"),
                )
                return {"id": item["id"], **result}

        results = await asyncio.gather(
            *[_translate_one(item) for item in items],
            return_exceptions=True,
        )

        out = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"翻译失败: {r}")
            else:
                out.append(r)
        return out


# 全局单例
translator = Translator()
