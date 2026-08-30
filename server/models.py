from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ItemCategory(str, Enum):
    CASE = "case"
    KEY = "key"
    STICKER = "sticker"
    SKIN = "skin"
    OTHER = "other"


def categorize_item(name: str) -> ItemCategory:
    lower = name.lower()
    if lower.endswith(" case"):
        return ItemCategory.CASE
    if "case key" in lower or lower.endswith(" key"):
        return ItemCategory.KEY
    if name.startswith("Sticker |") or lower.startswith("sticker |"):
        return ItemCategory.STICKER
    if "|" in name:
        return ItemCategory.SKIN
    return ItemCategory.OTHER


def cents_to_usd(cents: int | float | None) -> float:
    if cents is None:
        return 0.0
    return round(float(cents) / 100.0, 2)


@dataclass
class HotItem:
    name: str
    category: ItemCategory
    ask_usd: float
    highest_bid_usd: float | None
    sell_order_count: int
    bid_volume: int
    liquidity_score: int
    volume_24h: int = 0

    @property
    def liquidity_rank(self) -> float:
        return (
            self.sell_order_count * 0.4
            + self.bid_volume * 0.4
            + self.liquidity_score * 10
            + self.volume_24h * 2
        )


@dataclass
class SnipeDeal:
    listing_id: str
    name: str
    category: ItemCategory
    buy_price_usd: float
    market_price_usd: float
    sell_target_usd: float
    net_profit_usd: float
    discount_pct: float
    liquidity_score: int
    sell_speed: str
    score: float
    is_big_hit: bool
    buy_url: str
    icon_url: str | None
    float_value: float | None
    source: str
    listing_type: str = "buy_now"
    expires_at: str | None = None
    seconds_left: float | None = None
    time_left_label: str = "Buy now"
    urgency: str = "actionable"
    urgency_reason: str = "Buy now — grab it"
    alert_window_sec: int = 0
    found_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "listing_id": self.listing_id,
            "name": self.name,
            "category": self.category.value,
            "buy_price_usd": self.buy_price_usd,
            "market_price_usd": self.market_price_usd,
            "sell_target_usd": self.sell_target_usd,
            "net_profit_usd": self.net_profit_usd,
            "discount_pct": round(self.discount_pct, 1),
            "liquidity_score": self.liquidity_score,
            "sell_speed": self.sell_speed,
            "score": round(self.score, 2),
            "is_big_hit": self.is_big_hit,
            "buy_url": self.buy_url,
            "icon_url": self.icon_url,
            "float_value": self.float_value,
            "source": self.source,
            "listing_type": self.listing_type,
            "expires_at": self.expires_at,
            "seconds_left": self.seconds_left,
            "time_left_label": self.time_left_label,
            "urgency": self.urgency,
            "urgency_reason": self.urgency_reason,
            "alert_window_sec": self.alert_window_sec,
            "found_at": self.found_at,
            "extra": self.extra,
        }
