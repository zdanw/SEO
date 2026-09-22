"""P4：生产环境配置校验。"""
from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRET_MARKERS = ("change-me", "please-change", "123456")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "SEO Platform"
    APP_ENV: str = "dev"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    SECRET_KEY: str = "change-me-in-production-please-123456"
    API_V1_PREFIX: str = "/api/v1"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    ALLOW_REGISTRATION: bool = False
    CORS_ORIGINS: str = ""

    # 数据库
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 5432
    DB_USER: str = "seo"
    DB_PASSWORD: str = "seo_dev_pass"
    DB_NAME: str = "seo_platform"
    DATABASE_URL: str = "postgresql+psycopg2://seo:seo_dev_pass@127.0.0.1:5432/seo_platform"

    # Redis
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = "seo_dev_redis"
    CELERY_BROKER_URL: str = "redis://:seo_dev_redis@127.0.0.1:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://:seo_dev_redis@127.0.0.1:6379/2"

    # DeepSeek AI
    DEEPSEEK_API_KEY: Optional[str] = None
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_TIMEOUT: int = 120

    # Agnes（Sapiens AI）可选模型
    AGNES_API_KEY: Optional[str] = None
    AGNES_BASE_URL: str = "https://api.sapiens.ai/v1"
    AGNES_MODEL: str = "agnes-2.5-flash"
    AGNES_TIMEOUT: int = 60

    # AI 内容检测：hybrid | lmscan | signs_of_ai | heuristic
    AI_DETECTOR: str = "hybrid"
    AI_DETECTOR_MIN_CHARS: int = 200
    AI_DETECTOR_LMSCAN_WEIGHT: float = 0.6
    AI_DETECTOR_SIGNS_WEIGHT: float = 0.4

    # SERP 抓取：ScrapingBee Google Search API（优先于代理池）
    SCRAPINGBEE_API_KEY: Optional[str] = None
    SCRAPINGBEE_TIMEOUT: int = 60
    SCRAPINGBEE_PAGES: int = 3  # 单次请求聚合的 Google 结果页数（建议 ≤3）
    # 合规优先：默认不用代理直抓；仅显式打开后才允许 PROXY_* 作为降级
    SERP_ALLOW_PROXY_FALLBACK: bool = False
    SERP_DAILY_QUOTA: int = 500  # <=0 表示不限制
    SERP_EST_COST_PER_REQUEST: float = 0.005  # 单次调用估算费用（USD），用于成本看板

    # 代理（仅当 SERP_ALLOW_PROXY_FALLBACK=true 且未配置 ScrapingBee 时使用）
    PROXY_PROVIDER: Optional[str] = None
    PROXY_USERNAME: Optional[str] = None
    PROXY_PASSWORD: Optional[str] = None
    PROXY_ENDPOINT: Optional[str] = None
    PROXY_LIST: Optional[str] = None

    # SMTP
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: str = "seo-platform@example.com"
    ALERT_EMAIL_TO: Optional[str] = None

    FRONTEND_URL: str = "http://127.0.0.1:5173"

    # Zernio API 端点（凭证仅在前端「社交账号」写入 zernio_api_keys，不走环境变量）
    ZERNIO_API_BASE: str = "https://zernio.com/api/v1"
    ZERNIO_TIMEOUT: int = 30

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV.lower() in ("dev", "development", "local")

    def cors_origins_list(self) -> list[str]:
        """开发默认可 *；生产须配置 CORS_ORIGINS 或回退 FRONTEND_URL。"""
        raw = (self.CORS_ORIGINS or "").strip()
        if self.is_dev and not raw:
            return ["*"]
        if raw:
            return [o.strip() for o in raw.split(",") if o.strip()]
        return [self.FRONTEND_URL.rstrip("/")]

    def validate_production(self) -> None:
        if self.is_dev:
            return
        errors: list[str] = []
        if self.APP_DEBUG:
            errors.append("生产环境须设置 APP_DEBUG=false")
        key = self.SECRET_KEY or ""
        if len(key) < 32 or any(m in key.lower() for m in _WEAK_SECRET_MARKERS):
            errors.append("生产环境须设置长度≥32 且非默认占位符的 SECRET_KEY")
        if errors:
            raise RuntimeError("生产配置校验失败：" + "；".join(errors))


settings = Settings()
