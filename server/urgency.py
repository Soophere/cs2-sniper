from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import settings


# Profit tiers → when Discord should fire for auctions
# Buy-now listings always alert immediately (they're actionable now).
BIG_FISH_PROFIT_USD = 6.0          # "big fish" — warn with ~24h left
MEDIUM_PROFIT_USD = 4.0            # solid flip — warn near the end
BIG_FISH_WINDOW_SEC = 24 * 3600    # 24 hours
MEDIUM_WINDOW_SEC = 15 * 60        # 15 minutes
SMALL_WINDOW_SEC = 5 * 60          # 5 minutes


def parse_expires_at(listing: dict[str, Any]) -> datetime | None:
    details = listing.get("auction_details") or {}
    raw = details.get("expires_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_until(expires_at: datetime | None, now: datetime | None = None) -> float | None:
    if expires_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (expires_at - now).total_seconds()


def format_time_left(seconds: float | None) -> str:
    if seconds is None:
        return "Buy now"
    if seconds <= 0:
        return "Ended"
    if seconds < 60:
        return f"{int(seconds)}s left"
    if seconds < 3600:
        mins = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s left"
    if seconds < 86400:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        return f"{hours}h {mins}m left"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    return f"{days}d {hours}h left"


def alert_window_seconds(net_profit_usd: float, is_big_hit: bool) -> int:
    """How close to auction end before we Discord-alert."""
    if is_big_hit or net_profit_usd >= BIG_FISH_PROFIT_USD:
        return BIG_FISH_WINDOW_SEC
    if net_profit_usd >= MEDIUM_PROFIT_USD:
        return MEDIUM_WINDOW_SEC
    return SMALL_WINDOW_SEC


def urgency_tier(
    listing_type: str,
    net_profit_usd: float,
    is_big_hit: bool,
    seconds_left: float | None,
) -> str:
    """
    actionable  — Discord now (buy_now, or auction inside time window)
    watching    — good deal but auction still has too much time (don't spam)
    expired     — auction already over
    """
    if listing_type == "buy_now" or seconds_left is None:
        return "actionable"

    if seconds_left <= 0:
        return "expired"

    window = alert_window_seconds(net_profit_usd, is_big_hit)
    if seconds_left <= window:
        return "actionable"
    return "watching"


def should_discord_alert(deal_urgency: str, listing_id: str, already_alerted: set[str]) -> bool:
    if deal_urgency != "actionable":
        return False
    if listing_id in already_alerted:
        return False
    return True


def urgency_reason(net_profit_usd: float, is_big_hit: bool, seconds_left: float | None, listing_type: str) -> str:
    if listing_type == "buy_now" or seconds_left is None:
        return "Buy now — grab it"

    window = alert_window_seconds(net_profit_usd, is_big_hit)
    if is_big_hit or net_profit_usd >= BIG_FISH_PROFIT_USD:
        label = "big fish"
    elif net_profit_usd >= MEDIUM_PROFIT_USD:
        label = "medium"
    else:
        label = "small"

    if seconds_left <= 0:
        return "Auction ended"
    if seconds_left <= window:
        return f"{label} profit — time window hit ({format_time_left(seconds_left)})"
    return (
        f"Watching ({label}): alerts at "
        f"{format_time_left(float(window))} — now {format_time_left(seconds_left)}"
    )
