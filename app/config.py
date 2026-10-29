from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./metering.db"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_id: str = ""
    app_base_url: str = "http://localhost:8000"

    # Background job (Stage 8)
    background_jobs_enabled: bool = True
    cleanup_interval_seconds: int = 3600  # how often the cleanup job runs
    receipt_retention_hours: int = 24  # idempotency receipts older than this are deleted

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

# ---------------------------------------------------------------------------
# PINNED PRICING CONSTANTS
# Money is always a whole number of micro-dollars. No floats anywhere.
# These are demo prices chosen for this project, not real provider prices.
# They are plain constants on purpose: they must not change through .env.
# ---------------------------------------------------------------------------
MICROS_PER_DOLLAR = 1_000_000
MICROS_PER_CENT = 10_000

CALL_PRICE_MICROS = 100  # price of one API call

# Token prices, in micro-dollars per 1,000 tokens.
FRESH_INPUT_PER_1K_MICROS = 300
CACHED_INPUT_PER_1K_MICROS = 75  # 75% cheaper than fresh input
OUTPUT_PER_1K_MICROS = 1500  # reasoning tokens are billed at this same rate
