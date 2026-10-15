"""Central settings — everything from environment, placeholders only in repo."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./trial.db"
    SECRET_KEY: str = "change-me-to-a-long-random-string"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    APP_URL: str = "http://127.0.0.1:8002"
    FRONTEND_URL: str = "http://localhost:5174"

    EMAIL_PROVIDER: str = "dryrun"  # dryrun | smtp | resend
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True
    RESEND_API_KEY: str = ""
    RESEND_FROM: str = ""

    DAILY_CRAWL_PAGES: int = 2000
    DAILY_EMAIL_LIMIT: int = 50
    MAX_CRAWL_PAGES: int = 500
    MAX_CAMPAIGN_RECIPIENTS: int = 100
    # Parallel crawl workers (local desktop: 3; tiny containers: 1)
    CRAWL_WORKERS: int = 3

    # Google OAuth (Sign in with Google + Gmail send). Empty = button hidden.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://127.0.0.1:8002/api/auth/google/callback"

    # Browser rendering fallback (JS-heavy pages). Disable where Chromium
    # is unavailable (tiny containers): rendering then degrades to an
    # honest "js-shell" event instead of failing.
    PLAYWRIGHT_ENABLED: bool = True
    MAX_RENDER_PAGES: int = 25

    # Rate limits (requests per minute per IP)
    RATE_LOGIN_PER_MIN: int = 10
    RATE_SIGNUP_PER_MIN: int = 5
    RATE_SENSITIVE_PER_MIN: int = 20

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


def startup_warnings() -> list[str]:
    """Report missing config WITHOUT printing secret values."""
    missing: list[str] = []
    if settings.SECRET_KEY in ("", "change-me-to-a-long-random-string"):
        missing.append("SECRET_KEY is the insecure default — set a long random value")
    if settings.EMAIL_PROVIDER == "smtp" and not (settings.SMTP_HOST and settings.SMTP_FROM):
        missing.append("EMAIL_PROVIDER=smtp but SMTP_HOST/SMTP_FROM are empty")
    if settings.EMAIL_PROVIDER == "resend" and not (settings.RESEND_API_KEY and settings.RESEND_FROM):
        missing.append("EMAIL_PROVIDER=resend but RESEND_API_KEY/RESEND_FROM are empty")
    if settings.DATABASE_URL.startswith("sqlite"):
        missing.append("Using local SQLite (fine for trial dev; use PostgreSQL in production)")
    return missing
