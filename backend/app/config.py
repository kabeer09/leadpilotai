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

    # Usage caps effectively removed (local trial owner request).
    # High but finite: guards against runaway jobs, not against you.
    DAILY_CRAWL_PAGES: int = 100000
    DAILY_EMAIL_LIMIT: int = 2000
    MAX_CRAWL_PAGES: int = 10000
    MAX_CAMPAIGN_RECIPIENTS: int = 5000
    # Parallel crawl workers. 2 = smooth on a typical desktop alongside
    # the API + browsers (3+ starves SQLite/API on low-RAM machines).
    CRAWL_WORKERS: int = 2

    # Google OAuth (Sign in with Google + Gmail send). Empty = button hidden.
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""  # Auto-derived from APP_URL if empty

    # Browser rendering fallback (JS-heavy pages). Disable where Chromium
    # is unavailable (tiny containers): rendering then degrades to an
    # honest "js-shell" event instead of failing.
    PLAYWRIGHT_ENABLED: bool = True
    MAX_RENDER_PAGES: int = 25
    # Gemini AI (optional — for AI email personalization)
    GEMINI_API_KEY: str = ""

    # Google Business Lead Finder (Places API New). Key stays server-side
    # only — never sent to the browser. Empty = feature reports
    # "not configured" honestly instead of failing silently.
    GOOGLE_MAPS_API_KEY: str = ""
    # Places Text Search (New) returns max 20/page; Google allows 3 pages
    # => hard cap 60 results per search request.
    GB_MAX_RESULTS_PER_SEARCH: int = 60
    # Website enrichment bounds (spec: bounded crawling, configurable).
    GB_MAX_PAGES_PER_SITE: int = 6
    GB_REQUEST_DELAY: float = 1.0        # seconds between website fetches
    GB_SEARCH_TIMEOUT: float = 20.0      # seconds per Google request
    GB_SEARCH_MAX_RETRIES: int = 3       # transient errors: exp. backoff
    GB_ENRICHMENT_ENABLED: bool = True
    # Request budget: our field mask (phone/website/rating) bills at the
    # Text Search Enterprise SKU: ~$0.035/request after Google's 1,000 free
    # Text Search requests/month (reset the 1st, Pacific time).
    # Used for per-campaign cost estimates only — Google's invoice is truth.
    GB_COST_PER_CALL_USD: float = 0.035
    GB_DAILY_API_CALLS: int = 500        # per-user daily Google request budget

    # WhatsApp automation (dryrun | meta). meta = WhatsApp Business Cloud API
    # (requires an approved message template for first/cold contact).
    WHATSAPP_PROVIDER: str = "dryrun"  # dryrun | meta
    WHATSAPP_ACCESS_TOKEN: str = ""     # Meta permanent system-user token
    WHATSAPP_PHONE_NUMBER_ID: str = ""  # your WABA phone number id
    WHATSAPP_TEMPLATE_NAME: str = ""    # approved template, e.g. catalog_hello
    WHATSAPP_TEMPLATE_LANG: str = "en"
    # Which variables we can fill in the template body, in order.
    # Available: company | country | phone | message
    WHATSAPP_TEMPLATE_VARS: str = "company"
    DAILY_WHATSAPP_LIMIT: int = 1000

    @property
    def google_redirect_uri(self) -> str:
        if self.GOOGLE_REDIRECT_URI:
            return self.GOOGLE_REDIRECT_URI
        return self.APP_URL.rstrip("/") + "/api/auth/google/callback"

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
    if settings.WHATSAPP_PROVIDER == "meta":
        if not (settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID):
            missing.append("WHATSAPP_PROVIDER=meta but WHATSAPP_ACCESS_TOKEN/PHONE_NUMBER_ID are empty")
        if not settings.WHATSAPP_TEMPLATE_NAME:
            missing.append("WHATSAPP_PROVIDER=meta but WHATSAPP_TEMPLATE_NAME is empty (cold messages need an approved template)")
    if settings.DATABASE_URL.startswith("sqlite"):
        missing.append("Using local SQLite (fine for trial dev; use PostgreSQL in production)")
    if not settings.GOOGLE_MAPS_API_KEY:
        missing.append("GOOGLE_MAPS_API_KEY is empty — Google Business Lead Finder "
                       "shows 'not configured' until a Places API (New) key is set")
    return missing
