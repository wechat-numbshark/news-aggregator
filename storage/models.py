from sqlalchemy import (
    Column, String, Text, Boolean, SmallInteger, Integer,
    DateTime, ARRAY, JSON, Index, ForeignKey, BigInteger
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()


class Article(Base):
    __tablename__ = "articles"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id       = Column(String(64), nullable=False, index=True)
    source_name     = Column(String(128), nullable=False)
    source_country  = Column(String(8), nullable=False, index=True)
    source_type     = Column(String(32), nullable=False)

    original_lang   = Column(String(8), nullable=False)
    author          = Column(String(256))           # 作者
    title_original  = Column(Text, nullable=False)
    title_zh        = Column(Text)
    summary_zh      = Column(Text)
    content_url     = Column(Text)
    content_text    = Column(Text)          # 抓取的正文全文
    image_url       = Column(Text)

    published_at    = Column(DateTime(timezone=True), index=True)
    collected_at    = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    translated_at   = Column(DateTime(timezone=True))
    pushed_at       = Column(DateTime(timezone=True))

    categories      = Column(ARRAY(Text), server_default="{}")
    tags            = Column(ARRAY(Text), server_default="{}")
    importance      = Column(SmallInteger, default=0)

    is_translated   = Column(Boolean, nullable=False, default=False)
    is_pushed       = Column(Boolean, nullable=False, default=False)
    is_filtered_out = Column(Boolean, nullable=False, default=False)

    content_hash    = Column(String(64), nullable=False, unique=True)
    raw_json        = Column(JSONB)

    __table_args__ = (
        Index("idx_push_queue", "is_translated", "is_pushed", "is_filtered_out", "published_at",
              postgresql_where="is_translated = TRUE AND is_pushed = FALSE AND is_filtered_out = FALSE"),
    )


class Source(Base):
    __tablename__ = "sources"

    id              = Column(String(64), primary_key=True)
    name            = Column(String(128), nullable=False)
    country         = Column(String(8), nullable=False)
    source_type     = Column(String(32), nullable=False)
    url             = Column(Text)
    is_active       = Column(Boolean, default=True)
    last_fetched_at = Column(DateTime(timezone=True))
    fetch_interval  = Column(Integer, default=1800)
    error_count     = Column(Integer, default=0)
    last_error      = Column(Text)
    config_json     = Column(JSONB, default={})


class Briefing(Base):
    __tablename__ = "briefings"

    date           = Column(String(10), primary_key=True)   # YYYY-MM-DD
    content        = Column(JSONB, nullable=False)
    article_count  = Column(Integer)
    top_count      = Column(Integer)
    model_scoring  = Column(String(64))
    model_analysis = Column(String(64))
    generated_at   = Column(DateTime(timezone=True), server_default=func.now())
    duration_sec   = Column(Integer)


class PushLog(Base):
    __tablename__ = "push_logs"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    article_id  = Column(UUID(as_uuid=True), ForeignKey("articles.id"))
    channel     = Column(String(64), nullable=False)
    pushed_at   = Column(DateTime(timezone=True), server_default=func.now())
    success     = Column(Boolean)
    response    = Column(Text)
