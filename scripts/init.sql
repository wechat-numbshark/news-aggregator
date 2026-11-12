-- 启用全文检索扩展
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- 文章主表
CREATE TABLE IF NOT EXISTS articles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       VARCHAR(64) NOT NULL,       -- 来源标识 e.g. "xinhua", "nyt", "nhk"
    source_name     VARCHAR(128) NOT NULL,       -- 来源名称 e.g. "新华社"
    source_country  CHAR(2) NOT NULL,            -- CN / US / JP / GB / DE ...
    source_type     VARCHAR(32) NOT NULL,        -- rss / rsshub / gdelt / wechat / api

    original_lang   VARCHAR(8) NOT NULL,         -- zh / en / ja / ...
    author          VARCHAR(256),                -- 作者
    title_original  TEXT NOT NULL,
    title_zh        TEXT,                        -- 中文标题（外文翻译后填入）
    summary_zh      TEXT,                        -- 中文摘要
    content_url     TEXT,
    image_url       TEXT,

    published_at    TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    translated_at   TIMESTAMPTZ,
    pushed_at       TIMESTAMPTZ,

    -- 分类标签 (可多选)
    categories      TEXT[] DEFAULT '{}',         -- stock_cn / stock_us / startup / opportunity / macro / tech / general
    tags            TEXT[] DEFAULT '{}',
    importance      SMALLINT DEFAULT 0,          -- 0-10 AI打分

    -- 状态
    is_translated   BOOLEAN NOT NULL DEFAULT FALSE,
    is_pushed       BOOLEAN NOT NULL DEFAULT FALSE,
    is_filtered_out BOOLEAN NOT NULL DEFAULT FALSE,  -- 低质量过滤

    -- 去重
    content_hash    CHAR(64) NOT NULL,           -- SHA256(source_id + title_original)
    raw_json        JSONB                        -- 原始数据备份
);

-- 索引
CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_hash ON articles(content_hash);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_country ON articles(source_country);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_categories ON articles USING GIN(categories);
CREATE INDEX IF NOT EXISTS idx_articles_tags ON articles USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_articles_push_queue ON articles(is_translated, is_pushed, is_filtered_out, published_at DESC)
    WHERE is_translated = TRUE AND is_pushed = FALSE AND is_filtered_out = FALSE;

-- 全文检索索引（中文用 trgm）
CREATE INDEX IF NOT EXISTS idx_articles_title_trgm ON articles USING GIN(title_zh gin_trgm_ops);

-- 来源配置表（记录每个来源的运行状态）
CREATE TABLE IF NOT EXISTS sources (
    id              VARCHAR(64) PRIMARY KEY,
    name            VARCHAR(128) NOT NULL,
    country         CHAR(2) NOT NULL,
    source_type     VARCHAR(32) NOT NULL,
    url             TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    last_fetched_at TIMESTAMPTZ,
    fetch_interval  INTEGER DEFAULT 1800,        -- 秒，默认30分钟
    error_count     INTEGER DEFAULT 0,
    last_error      TEXT,
    config_json     JSONB DEFAULT '{}'
);

-- 推送记录表
CREATE TABLE IF NOT EXISTS push_logs (
    id          BIGSERIAL PRIMARY KEY,
    article_id  UUID REFERENCES articles(id),
    channel     VARCHAR(64) NOT NULL,           -- feishu_webhook_xxx
    pushed_at   TIMESTAMPTZ DEFAULT NOW(),
    success     BOOLEAN,
    response    TEXT
);

CREATE INDEX IF NOT EXISTS idx_push_logs_article ON push_logs(article_id);
