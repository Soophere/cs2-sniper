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
    # Where you actually resell (Oman: CSFloat + crypto — Skinport kept for price reference only)
    sell_venue: str = "csfloat"
    skinport_seller_fee_pct: float = 8.0
    waxpeer_seller_fee_pct: float = 6.0

    # --- Anti-fake-profit guards -------------------------------------------
    # Markets where you can actually BUY an item for cash and receive it.
    # loot.farm = skin-for-skin trading, skincash = cashout site, buff/youpin = CN
    # payouts. Their "prices" are NOT cash buy prices, so they are reference only.
    buyable_markets: str = "csfloat,skinport,waxpeer,steam"
    # A gap this large is almost always bad/mismatched data, not free money.
    max_discount_pct: float = 35.0
    # Assume we sell this much below the reference price (undercut + price drift
    # during the 7-day Steam trade hold).
    price_risk_buffer_pct: float = 8.0
    # Require this many independent markets to agree on the sell price.
    min_sources_agree: int = 3
    # Reject a listing whose price is inflated this far above the bare skin value
    # (sticker/charm premium you will not get back).
    max_sticker_premium_pct: float = 12.0
    # Item names containing these are too illiquid to flip reliably.
    exclude_keywords: str = "souvenir package,autograph capsule,graffiti,patch pack"
    # There is no free bulk CSFloat price dump, so when we only have a Steam-style
    # market value we assume CSFloat realises this fraction of it.
    csfloat_realisation_pct: float = 85.0
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

    @property
    def buyable_market_set(self) -> set[str]:
        return {m.strip().lower() for m in self.buyable_markets.split(",") if m.strip()}

    @property
    def exclude_keyword_list(self) -> list[str]:
        return [k.strip().lower() for k in self.exclude_keywords.split(",") if k.strip()]


settings = Settings()
