from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    csfloat_api_key: str = ""
    discord_webhook_url: str = ""

    min_profit_usd: float = 2.0
    min_discount_pct: float = 10.0
    max_buy_usd: float = 10.0
    poll_interval_sec: int = 60
    csfloat_seller_fee_pct: float = 2.0
    # After a rate limit, wait at least this many seconds before scanning again
    rate_limit_cooldown_sec: int = 90
    # Skip illiquid junk (CSFloat predicted price with qty=2 is often fake edge)
    min_market_qty: int = 8
    min_liquidity_score: int = 15
    # Cap memory for watched auction raw listings
    max_watched_raw: int = 300

    host: str = "127.0.0.1"
    port: int = 8765

    @property
    def max_buy_cents(self) -> int:
        return int(self.max_buy_usd * 100)


settings = Settings()
