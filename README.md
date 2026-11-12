**语言 / Language / 言語:** [简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md)

# News Aggregator / 新闻聚合

把公开 RSS、RSSHub、GDELT 和可选的 NYT API 文章汇总到本地，生成网页列表与 AI 简报；飞书 Webhook 推送是可选功能。默认只在本机开放网页。

## 快速开始

需要 Python 3.11+ 和 Docker。复制 `.env.example` 为 `.env`，设置 `POSTGRES_PASSWORD`。用 `docker compose up -d` 启动 PostgreSQL 与 Redis，随后安装依赖 `pip install -r requirements.txt`，执行 `python api/app.py`，在 `http://127.0.0.1:8099` 查看页面。定时采集入口是 `python main.py`。

不配置 OpenRouter、NYT 或飞书时，对应的 AI、NYT 和推送能力不可用；RSS 来源仍可按各站可用性测试。来源清单在 `config/sources.yaml`，部分 RSSHub 路由需要你自己的 RSSHub 实例。首次运行建议先在网页的“来源”页逐项测试。

## 数据与安全

`.env`、`data/`、`logs/` 和数据库内容不会进入仓库。网页包含触发采集与来源测试的接口，默认仅绑定 `127.0.0.1`；若要开放给其他人，先加认证与反向代理。抓取器只保存必要的标题、摘要与原文链接；使用者应遵守各来源的许可和速率限制。配置文件中的来源是候选项，不保证持续可用。

## 开发

`python -m compileall -q .` 检查语法。现有项目没有完整自动化测试；线上来源和外部模型需各自验证。

MIT License。本项目与新闻来源、飞书或模型服务商无隶属关系。

## 语言

网页右下角可切换中文、English、日本語；主导航、筛选和常用状态会随之切换。来自外部系统的标题、原文和诊断详情保留来源语言。
