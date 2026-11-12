"""
快速测试采集器（无需数据库）
运行: python scripts/test_collectors.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from collectors.rss import RSSCollector
from collectors.rsshub import RSSHubCollector
from collectors.gdelt import GDELTCollector
from collectors.nyt import NYTCollector


async def test_rss():
    print("\n=== 测试 RSS: 新华社财经 ===")
    c = RSSCollector({
        "id": "test_xinhua",
        "name": "新华社测试",
        "country": "CN",
        "type": "rss",
        "lang": "zh",
        "url": "http://www.xinhuanet.com/fortune/rss/index.rss",
        "categories": ["macro"],
    })
    articles = await c.fetch()
    for a in articles[:3]:
        print(f"  [{a.source_country}] {a.title[:50]}")
    print(f"  共 {len(articles)} 条")


async def test_rsshub():
    print("\n=== 测试 RSSHub: 财联社电报 ===")
    c = RSSHubCollector({
        "id": "test_cls",
        "name": "财联社测试",
        "country": "CN",
        "type": "rsshub",
        "lang": "zh",
        "rsshub_path": "/cls/telegraph",
        "categories": ["stock_cn"],
    })
    articles = await c.fetch()
    for a in articles[:3]:
        print(f"  [{a.source_country}] {a.title[:50]}")
    print(f"  共 {len(articles)} 条")


async def test_gdelt():
    print("\n=== 测试 GDELT: 全球财经 ===")
    c = GDELTCollector({
        "id": "test_gdelt",
        "name": "GDELT测试",
        "country": "GLOBAL",
        "type": "gdelt",
        "lang": "mixed",
        "gdelt_query": "China economy",
        "categories": ["macro"],
    })
    articles = await c.fetch()
    for a in articles[:3]:
        print(f"  [{a.source_country}/{a.original_lang}] {a.title[:60]}")
    print(f"  共 {len(articles)} 条")


async def test_nyt():
    print("\n=== 测试 NYT API ===")
    from config.settings import settings
    if not settings.nyt_api_key:
        print("  ⚠️ NYT_API_KEY 未配置，跳过")
        return
    c = NYTCollector({
        "id": "test_nyt",
        "name": "NYT测试",
        "country": "US",
        "type": "nyt_api",
        "lang": "en",
        "nyt_section": "business",
        "categories": ["stock_us"],
    })
    articles = await c.fetch()
    for a in articles[:3]:
        print(f"  [{a.source_country}] {a.title[:60]}")
    print(f"  共 {len(articles)} 条")


async def main():
    print("开始测试采集器（只测试网络请求，不写数据库）")
    await test_rss()
    await test_rsshub()
    await test_gdelt()
    await test_nyt()
    print("\n✅ 测试完成")


if __name__ == "__main__":
    asyncio.run(main())
