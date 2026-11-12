from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import hashlib


@dataclass
class RawArticle:
    """采集层输出的原始文章结构"""
    source_id:      str
    source_name:    str
    source_country: str
    source_type:    str
    original_lang:  str
    title:          str
    content_url:    str
    categories:     list[str] = field(default_factory=list)
    author:         Optional[str] = None
    summary:        Optional[str] = None
    image_url:      Optional[str] = None
    published_at:   Optional[datetime] = None
    raw_data:       Optional[dict] = None

    @property
    def content_hash(self) -> str:
        raw = f"{self.source_id}::{self.title}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class BaseCollector(ABC):
    def __init__(self, source_config: dict):
        self.cfg = source_config
        self.source_id = source_config["id"]
        self.source_name = source_config["name"]
        self.source_country = source_config["country"]
        self.source_type = source_config["type"]
        self.original_lang = source_config.get("lang", "unknown")
        self.categories = source_config.get("categories", [])

    @abstractmethod
    async def fetch(self) -> list[RawArticle]:
        """采集文章，返回 RawArticle 列表"""
        ...

    def _make_article(self, title: str, url: str, **kwargs) -> RawArticle:
        return RawArticle(
            source_id=self.source_id,
            source_name=self.source_name,
            source_country=self.source_country,
            source_type=self.source_type,
            original_lang=self.original_lang,
            title=title,
            content_url=url,
            categories=self.categories.copy(),
            **kwargs,
        )
