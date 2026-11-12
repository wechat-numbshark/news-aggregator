from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os


class Settings(BaseSettings):
    # 数据库
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "newsdb"
    postgres_user: str = "newsuser"
    postgres_password: str = ""

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_translate_model: str = "qwen/qwen3-235b-a22b:free"
    openrouter_fast_model: str = "google/gemini-2.0-flash-exp:free"

    # 飞书
    feishu_webhook_stock_cn: str = ""
    feishu_webhook_stock_us: str = ""
    feishu_webhook_startup: str = ""
    feishu_webhook_general: str = ""
    feishu_briefing_webhook: str = ""    # 每日简报推送（主）
    feishu_briefing_webhook2: str = ""   # 每日简报推送（副）

    # WeWe-RSS
    wewrss_base_url: str = "http://localhost:4000"
    wewrss_auth_code: str = ""

    # NYT
    nyt_api_key: str = ""

    # 代理
    http_proxy: Optional[str] = None
    https_proxy: Optional[str] = None

    # 运行控制
    log_level: str = "INFO"
    dry_run: bool = True
    max_articles_per_run: int = 500

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
