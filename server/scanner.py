from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .config import settings
from .csfloat_client import CSFloatClient, RateLimitError, csfloat_client
from .discord_alerts import send_discord_alert
from .models import HotItem, SnipeDeal
from .openskin import DEFAULT_WATCHLIST, OpenSkinClient, openskin_client
from .price_sources import PriceAggregator, price_aggregator
from .pricing import build_deal_from_listing
from .urgency import format_time_left, seconds_until, urgency_reason, urgency_tier


@dataclass
class ScannerState:
    running: bool = False
    last_scan_at: str | None = None
    last_error: str | None = None
    scans_completed: int = 0
    listings_checked: int = 0
    alerts_sent: int = 0
    watching_count: int = 0
    api_key_configured: bool = False
    cooldown_until: str | None = None
    light_mode: bool = False
    hot_items: list[HotItem] = field(default_factory=list)
    deals: deque[SnipeDeal] = field(default_factory=lambda: deque(maxlen=200))
    alerted_listing_ids: set[str] = field(default_factory=set)
    watched_raw: dict[str, dict] = field(default_factory=dict)


class SniperScanner:
    def __init__(
        self,
        csfloat: CSFloatClient | None = None,
        openskin: OpenSkinClient | None = None,
        prices: PriceAggregator | None = None,
        on_deal: Callable[[SnipeDeal], Any] | None = None,
    ) -> None:
        self.csfloat = csfloat or csfloat_client
        self.openskin = openskin or openskin_client
        self.prices = prices or price_aggregator
        self.on_deal = on_deal
        self.state = ScannerState()
        self._task: asyncio.Task | None = None
        self._watchlist: list[str] = list(DEFAULT_WATCHLIST)
        self._cooldown_until: float = 0.0
        self._backoff_sec: int = settings.rate_limit_cooldown_sec
        self._feed_index: int = 0

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.state.running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self.state.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while self.state.running:
            now = time.monotonic()
            # Soft cooldown: NEVER fully stop — keep light scans (public feed + price APIs)
            light = now < self._cooldown_until
            self.state.light_mode = light
            if light:
                wait = int(self._cooldown_until - now)
                self.state.last_error = (
                    f"CSFloat light mode ({wait}s) — still scanning public feed + other markets"
                )
            else:
                self.state.cooldown_until = None
                if self.state.last_error and "light mode" in (self.state.last_error or ""):
                    self.state.last_error = None

            try:
                await self.scan_once(light=light)
                if not light:
                    self.state.last_error = None
                    self._backoff_sec = settings.rate_limit_cooldown_sec
                sleep_for = 90 if light else settings.poll_interval_sec
            except RateLimitError as exc:
                self._backoff_sec = max(exc.retry_after_sec, 90)
                self._cooldown_until = time.monotonic() + self._backoff_sec
                until = datetime.now(timezone.utc).timestamp() + self._backoff_sec
                self.state.cooldown_until = datetime.fromtimestamp(until, tz=timezone.utc).isoformat()
                self.state.light_mode = True
                self.state.last_error = f"{exc} Staying online in light mode."
                self._backoff_sec = min(self._backoff_sec * 2, 900)
                # Immediately do a light scan so user isn't staring at a dead bot
                try:
                    await self.scan_once(light=True)
                except Exception:
                    pass
                sleep_for = 45
            except Exception as exc:  # noqa: BLE001
                self.state.last_error = str(exc)
                sleep_for = settings.poll_interval_sec

            await asyncio.sleep(sleep_for)

    def _upsert_deal(self, deal: SnipeDeal) -> None:
        # refresh existing by id
        existing = [d for d in self.state.deals if d.listing_id != deal.listing_id]
        existing.insert(0, deal)
        self.state.deals = deque(existing[:200], maxlen=200)

    async def _maybe_alert(self, deal: SnipeDeal) -> None:
        if deal.urgency != "actionable":
            return
        if deal.listing_id in self.state.alerted_listing_ids:
            return

        if self.on_deal:
            maybe = self.on_deal(deal)
            if asyncio.iscoroutine(maybe):
                await maybe

        try:
            sent = await send_discord_alert(deal)
            if sent:
                self.state.alerted_listing_ids.add(deal.listing_id)
                self.state.alerts_sent += 1
        except Exception:
            pass

    async def scan_once(self, light: bool = False) -> list[SnipeDeal]:
        self.state.api_key_configured = bool(settings.csfloat_api_key.strip())
        actionable_new: list[SnipeDeal] = []

        # Bulk free price APIs (Skinport / CSGOMarket / SkinCash) — cached 10 min
        try:
            await self.prices.refresh()
        except Exception:
            pass

        # Refresh openskin hot list only every 5 scans (heavy call)
        if self.state.scans_completed % 5 == 0 or not self.state.hot_items:
            try:
                self.state.hot_items = await self.openskin.refresh_hot_items()
            except Exception:
                if not self.state.hot_items:
                    self.state.hot_items = []

        watch_names = list(
            dict.fromkeys(self._watchlist + [h.name for h in self.state.hot_items[:10]])
        )

        listings: list[dict] = []
        if light or not self.state.api_key_configured:
            # Light / no-key: public feed only (no max_price filter → still useful for watching)
            try:
                public_listings, _ = await self.csfloat.get_listings(limit=40, public_feed=True)
                listings.extend(public_listings)
            except RateLimitError:
                # Even public is blocked — keep processing cached watches + price book
                pass
            except Exception:
                pass
        else:
            # Full mode: rotate ONE heavy feed per scan to cut request count ~3x
            feeds = (
                ("most_recent", self.csfloat.iter_recent_under_budget),
                ("discount", self.csfloat.iter_discount_under_budget),
                ("auctions", self.csfloat.iter_auctions_ending_soon),
            )
            _, feed_fn = feeds[self._feed_index % len(feeds)]
            self._feed_index += 1
            async for listing in feed_fn(settings.max_buy_cents, pages=1):
                listings.append(listing)

            # Watchlist only every other scan, and only 3 names
            if self.state.scans_completed % 2 == 0:
                for name in watch_names[:3]:
                    try:
                        item_listings = await self.csfloat.listings_for_item(name, limit=5)
                        listings.extend(item_listings)
                        await asyncio.sleep(2.0)
                    except RateLimitError:
                        raise
                    except Exception:
                        continue

        unique: dict[str, dict] = {}
        for listing in listings:
            listing_id = str(listing.get("id"))
            if listing_id:
                unique[listing_id] = listing
                self.state.watched_raw[listing_id] = listing

        # Trim watched raw cache so memory doesn't grow forever
        if len(self.state.watched_raw) > settings.max_watched_raw:
            # keep newest-ish by iterating insertion order (py3.7+ dicts)
            overflow = len(self.state.watched_raw) - settings.max_watched_raw
            for old_id in list(self.state.watched_raw.keys())[:overflow]:
                self.state.watched_raw.pop(old_id, None)

        # also re-evaluate previously watched auctions not in this fetch
        for listing_id, listing in list(self.state.watched_raw.items()):
            unique.setdefault(listing_id, listing)

        names_needed = {
            (listing.get("item") or {}).get("market_hash_name")
            for listing in unique.values()
        }
        names_needed.discard(None)

        price_map: dict[str, dict] = {}
        if names_needed:
            try:
                price_map = await self.openskin.fetch_prices_batch(sorted(names_needed))
                await self.prices.merge_openskin_batch(price_map)
            except Exception:
                price_map = {}

        self.state.listings_checked += len(unique)
        watching = 0

        for listing in unique.values():
            name = (listing.get("item") or {}).get("market_hash_name") or ""
            deal = build_deal_from_listing(
                listing,
                price_map.get(name),
                self.prices.get(name),
            )
            if not deal:
                # drop dead watches
                lid = str(listing.get("id"))
                self.state.watched_raw.pop(lid, None)
                continue

            # refresh time labels on every scan
            if deal.expires_at:
                try:
                    exp = datetime.fromisoformat(deal.expires_at)
                    deal.seconds_left = seconds_until(exp)
                    deal.time_left_label = format_time_left(deal.seconds_left)
                    deal.urgency = urgency_tier(
                        deal.listing_type,
                        deal.net_profit_usd,
                        deal.is_big_hit,
                        deal.seconds_left,
                    )
                    deal.urgency_reason = urgency_reason(
                        deal.net_profit_usd,
                        deal.is_big_hit,
                        deal.seconds_left,
                        deal.listing_type,
                    )
                except ValueError:
                    pass

            if deal.urgency == "expired":
                self.state.watched_raw.pop(deal.listing_id, None)
                continue

            if deal.urgency == "watching":
                watching += 1

            self._upsert_deal(deal)

            if deal.urgency == "actionable":
                was_alerted = deal.listing_id in self.state.alerted_listing_ids
                await self._maybe_alert(deal)
                if not was_alerted and deal.listing_id in self.state.alerted_listing_ids:
                    actionable_new.append(deal)

        self.state.watching_count = watching
        actionable_new.sort(key=lambda d: d.score, reverse=True)
        self.state.scans_completed += 1
        self.state.last_scan_at = datetime.now(timezone.utc).isoformat()
        return actionable_new

    def get_deals(self, limit: int = 100) -> list[dict]:
        # actionable first, then watching; score within groups
        deals = list(self.state.deals)

        def sort_key(d: SnipeDeal) -> tuple:
            urgency_rank = 0 if d.urgency == "actionable" else 1
            time_rank = d.seconds_left if d.seconds_left is not None else 0
            return (urgency_rank, -d.score, time_rank)

        deals.sort(key=sort_key)
        return [d.to_dict() for d in deals[:limit]]

    def get_hot_items(self) -> list[dict]:
        return [
            {
                "name": h.name,
                "category": h.category.value,
                "ask_usd": h.ask_usd,
                "highest_bid_usd": h.highest_bid_usd,
                "sell_order_count": h.sell_order_count,
                "bid_volume": h.bid_volume,
                "liquidity_score": h.liquidity_score,
                "volume_24h": h.volume_24h,
                "liquidity_rank": round(h.liquidity_rank, 1),
                "sell_speed": "instant"
                if h.sell_order_count > 5000
                else "fast"
                if h.liquidity_score > 60
                else "medium",
            }
            for h in self.state.hot_items
        ]

    def status(self) -> dict[str, Any]:
        return {
            "running": self.state.running,
            "last_scan_at": self.state.last_scan_at,
            "last_error": self.state.last_error,
            "scans_completed": self.state.scans_completed,
            "listings_checked": self.state.listings_checked,
            "alerts_sent": self.state.alerts_sent,
            "watching_count": self.state.watching_count,
            "api_key_configured": self.state.api_key_configured,
            "deal_count": len(self.state.deals),
            "cooldown_until": self.state.cooldown_until,
            "light_mode": self.state.light_mode,
            "price_sources": self.prices.status(),
            "settings": {
                "min_profit_usd": settings.min_profit_usd,
                "min_discount_pct": settings.min_discount_pct,
                "max_buy_usd": settings.max_buy_usd,
                "poll_interval_sec": settings.poll_interval_sec,
            },
            "urgency_rules": {
                "big_fish": "≥$6 profit → Discord at ≤24h left",
                "medium": "≥$4 profit → Discord at ≤15m left",
                "small": "≥$2 profit → Discord at ≤5m left",
                "buy_now": "alert immediately",
            },
        }


scanner = SniperScanner()
