# 每日新闻简报系统设计

## 概述

从每日海量文章（1800+）中，通过三阶段漏斗处理，生成结构化中文简报。
覆盖：重大事项、股市信号、潜在机会、个人建议。

---

## 架构：三阶段漏斗

```
1800 篇原始文章
      │
      ▼
┌─────────────────────────────┐
│  阶段 0：规则预筛（0 token）  │
│  • 只取今日采集文章           │
│  • 去掉无分类垃圾文            │
│  • 按 importance 排序        │
│  输出：~400 篇（只取标题+URL） │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  阶段 1：批量评分（廉价模型）             │
│  模型：qwen/qwen3-235b-a22b:free        │
│  多语言直读，无需翻译                    │
│  输入：标题 + 来源 + 分类（每批60条）     │
│  输出：JSON[{id, score(1-10), zh摘要}]  │
│  约 7 批，合计 ~7K token                │
│  取 Top 80（按得分）                    │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  阶段 2：深度分析（稍强模型）             │
│  模型：google/gemini-2.0-flash-exp:free  │
│  输入：80篇 × (标题+摘要+URL) ≈ 8K token│
│  输出：结构化 JSON 简报                  │
│  合计 ~12K token（含输出）               │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  阶段 3：渲染 & 存库                     │
│  • 保存到 briefings 表（JSONB）          │
│  • Web 页面「每日简报」Tab 展示           │
│  • 可选：推送飞书                         │
└─────────────────────────────────────────┘
```

---

## Token 成本

| 阶段 | 模型 | 估算 Token | 费用 |
|------|------|-----------|------|
| 阶段1 批量评分（7批） | qwen3-235b:free | ~7K | 免费 |
| 阶段2 深度分析 | gemini-2.0-flash:free | ~12K | 免费 |
| **每日合计** | | **~19K** | **¥0** |

---

## 简报 JSON 结构

```json
{
  "date": "2026-05-20",
  "generated_at": "2026-05-20T08:05:00Z",
  "article_count": 1890,
  "top_count": 80,
  "model_scoring": "qwen/qwen3-235b-a22b:free",
  "model_analysis": "google/gemini-2.0-flash-exp:free",
  "sections": {
    "summary": "今日全球市场...(一段话总结)",
    "headlines": [
      {
        "title": "美联储暗示年内降息节奏放缓",
        "summary": "鲍威尔讲话显示...",
        "impact": "对A股情绪负面，对美元偏强",
        "importance": 9,
        "sources": [{"name": "Reuters", "url": "https://..."}]
      }
    ],
    "market": {
      "a_share": [
        {
          "signal": "AI算力板块受益于...",
          "direction": "利好",
          "analysis": "...",
          "sources": [{"name": "FT", "url": "..."}]
        }
      ],
      "us_share": [...],
      "macro": [...]
    },
    "opportunities": [
      {
        "title": "欧洲能源政策转向带来的产业链机会",
        "detail": "...",
        "ai_insight": "表面是能源新闻，实则...",
        "actionable": "关注A股中...",
        "sources": [{"name": "Guardian", "url": "..."}]
      }
    ],
    "personal": [
      {
        "action": "关注XXX板块ETF",
        "reason": "基于以上3条信号...",
        "timeframe": "1-2周"
      }
    ]
  }
}
```

---

## 数据库

```sql
CREATE TABLE briefings (
    date          DATE PRIMARY KEY,
    content       JSONB NOT NULL,
    article_count INTEGER,
    top_count     INTEGER,
    model_scoring VARCHAR(64),
    model_analysis VARCHAR(64),
    generated_at  TIMESTAMPTZ DEFAULT NOW(),
    duration_sec  NUMERIC(8,2)
);
```

---

## 触发方式

| 方式 | 时间 | 说明 |
|------|------|------|
| 自动定时 | 每天 08:00 | 采集完成（07:00）后1小时自动生成 |
| 手动触发 | 任意时间 | Web页面「生成今日简报」按钮 |
| API触发 | POST /api/briefing/generate | 可指定日期重新生成 |

---

## 阶段1 Prompt（极简，节省token）

```
对下方每条新闻，输出重要性评分(1-10)和≤20字中文摘要。
评分标准：与股市/宏观/创业/科技突破的相关度。
仅输出JSON数组，不要其他内容。

格式：[{"id":"1","s":8,"zh":"一句话摘要"},...]

新闻：
[{"id":"1","title":"...","src":"BBC","cat":"macro"},...]
```

---

## 阶段2 Prompt（分析师视角）

```
你是顶级投资分析师，擅长从全球新闻中发现中国投资者容易忽视的机会。

基于以下精选新闻生成今日中文简报。要求：
1. headlines：3-5条真正重大的全球事件
2. market.a_share：3条对A股最相关的信号（方向+分析）
3. market.us_share：3条美股信号
4. market.macro：2条宏观信号
5. opportunities：3-5条潜在机会（重点挖掘：供应链机会、产业趋势、
   表面不起眼但AI视角发现的隐性商机）
6. personal：3条具体可执行的个人行动建议
7. summary：3-5句话的今日总结

每条必须带 sources[{name, url}]。
严格输出JSON，不要markdown代码块，不要其他文字。

今日精选新闻（{n}条）：
{articles_json}
```

---

## 文件结构

```
news_aggregator/
├── processors/
│   ├── briefing.py        # 三阶段处理管道（新增）
│   └── translator.py      # 现有翻译器
├── api/
│   ├── app.py             # 新增 /api/briefing/* 接口
│   └── static/
│       └── index.html     # 新增「每日简报」Tab
└── storage/
    └── models.py          # 新增 Briefing 模型
```
