from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


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
    REDIS_PASSWORD: Optional[str] = None
    CELERY_BROKER_URL: str = "redis://127.0.0.1:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://127.0.0.1:6379/2"

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

    # seoscan（Node.js CLI）URL 技术审计
    SEOSCAN_TIMEOUT: int = 120

    # PulseForge
    PULSEFORGE_API_BASE: Optional[str] = None
    PULSEFORGE_API_KEY: Optional[str] = None
    PULSEFORGE_TIMEOUT: int = 30

    # SERP 抓取：ScrapingBee Google Search API（优先于代理池）
    SCRAPINGBEE_API_KEY: Optional[str] = None
    SCRAPINGBEE_TIMEOUT: int = 60
    SCRAPINGBEE_PAGES: int = 3  # 单次请求聚合的 Google 结果页数（建议 ≤3）

    # SERP 内容优化：竞品页面分析数量与地区
    SERP_BENCHMARK_TOP_N: int = 5
    SERP_BENCHMARK_REGION: str = "us"

    # 代理（ScrapingBee 未配置时使用）
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

    # Google Search Console OAuth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://127.0.0.1:8000/api/v1/gsc/oauth/callback"
    FRONTEND_URL: str = "http://127.0.0.1:5173"

    # PageSpeed Insights API（Core Web Vitals 真实测量）
    PAGESPEED_API_KEY: Optional[str] = None
    PAGESPEED_TIMEOUT: int = 90
    PAGESPEED_STRATEGY: str = "mobile"  # mobile | desktop

    # Reddit OAuth（Reddit 运营模块）
    REDDIT_CLIENT_ID: Optional[str] = None
    REDDIT_CLIENT_SECRET: Optional[str] = None
    REDDIT_REDIRECT_URI: str = "http://127.0.0.1:8000/api/v1/reddit/oauth/callback"
    REDDIT_USER_AGENT: str = "SEOPlatform/1.0"

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV.lower() in ("dev", "development", "local")


settings = Settings()
