from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .buy_links import csfloat_search_url, market_buy_url, market_sell_url, sell_deposit_url
from .config import settings
from .csfloat_client import CSFloatClient, RateLimitError, csfloat_client
from .discord_alerts import send_discord_alert
from .models import HotItem, SnipeDeal
from .openskin import DEFAULT_WATCHLIST, OpenSkinClient, openskin_client
from .price_sources import PriceAggregator, price_aggregator
from .pricing import build_arb_deal_from_quote, build_deal_from_listing
from .urgency import (
    big_fish_profit_usd,
    format_time_left,
    medium_profit_usd,
    seconds_until,
    urgency_reason,
    urgency_tier,
)


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
    # Why candidates were rejected on the last scan — proves the bot is working
    # even when the dashboard is empty.
    reject_reasons: dict[str, int] = field(default_factory=dict)
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
                    f"CSFloat light mode ({wait}s) — still scanning multi-market prices"
                )
            else:
                self.state.cooldown_until = None
                if self.state.last_error and "light mode" in (self.state.last_error or ""):
                    self.state.last_error = None

            try:
                await self.scan_once(light=light)
                if not light:
                    if self.state.last_error and "Discord" not in (self.state.last_error or ""):
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
        except Exception as exc:  # noqa: BLE001
            self.state.last_error = f"Discord alert failed: {exc}"

    async def scan_once(self, light: bool = False) -> list[SnipeDeal]:
        self.state.api_key_configured = bool(settings.csfloat_api_key.strip())
        actionable_new: list[SnipeDeal] = []

        # Bulk free price APIs — main scan engine (no CSFloat quota)
        try:
            await self.prices.refresh()
        except Exception:
            pass

        # Cross-market hits from price book (works during CSFloat cooldown)
        arb_checked = 0
        venue = settings.sell_venue.lower()
        buyable = settings.buyable_market_set
        rejects: dict[str, int] = {}
        for quote in list(self.prices.book.values()):
            # Need something to value the exit: a live venue ask, or the
            # Steam-style market value anchor.
            if not quote.sources.get(venue) and not quote.market_value_usd:
                continue
            # Need at least one cash-buyable market other than the sell venue.
            if not any(
                src in buyable and src != venue and price and price <= settings.max_buy_usd
                for src, price in quote.sources.items()
            ):
                continue
            arb_checked += 1
            deal = build_arb_deal_from_quote(quote, rejects)
            if not deal:
                continue
            self._upsert_deal(deal)
            if deal.urgency == "actionable":
                was_alerted = deal.listing_id in self.state.alerted_listing_ids
                await self._maybe_alert(deal)
                if not was_alerted and deal.listing_id in self.state.alerted_listing_ids:
                    actionable_new.append(deal)

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
        # CSFloat: at most ONE listing request per scan
        if not light and self.state.api_key_configured:
            feeds = (
                self.csfloat.iter_recent_under_budget,
                self.csfloat.iter_discount_under_budget,
                self.csfloat.iter_auctions_ending_soon,
            )
            feed_fn = feeds[self._feed_index % len(feeds)]
            self._feed_index += 1
            try:
                async for listing in feed_fn(settings.max_buy_cents, pages=1):
                    listings.append(listing)
            except RateLimitError:
                raise
            except Exception:
                pass
        else:
            try:
                public_listings, _ = await self.csfloat.get_listings(limit=40, public_feed=True)
                listings.extend(public_listings)
            except RateLimitError:
                pass
            except Exception:
                pass

        # Extra CSFloat watchlist call rarely (1 name / every 3rd full scan)
        if (
            not light
            and self.state.api_key_configured
            and self.state.scans_completed % 3 == 0
            and watch_names
        ):
            name = watch_names[self.state.scans_completed % len(watch_names)]
            try:
                item_listings = await self.csfloat.listings_for_item(name, limit=5)
                listings.extend(item_listings)
            except RateLimitError:
                raise
            except Exception:
                pass

        unique: dict[str, dict] = {}
        for listing in listings:
            listing_id = str(listing.get("id"))
            if listing_id:
                unique[listing_id] = listing
                self.state.watched_raw[listing_id] = listing

        if len(self.state.watched_raw) > settings.max_watched_raw:
            overflow = len(self.state.watched_raw) - settings.max_watched_raw
            for old_id in list(self.state.watched_raw.keys())[:overflow]:
                self.state.watched_raw.pop(old_id, None)

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

        self.state.reject_reasons = dict(
            sorted(rejects.items(), key=lambda kv: -kv[1])[:6]
        )
        self.state.listings_checked += len(unique) + arb_checked
        watching = 0

        for listing in unique.values():
            name = (listing.get("item") or {}).get("market_hash_name") or ""
            deal = build_deal_from_listing(
                listing,
                price_map.get(name),
                self.prices.get(name),
            )
            if not deal:
                lid = str(listing.get("id"))
                self.state.watched_raw.pop(lid, None)
                continue

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
                "buy_url": csfloat_search_url(h.name),
                "buy_links": {
                    "csfloat": csfloat_search_url(h.name),
                    "skinport": market_buy_url("skinport", h.name),
                    "steam": market_buy_url("steam", h.name),
                },
                "sell_venue": settings.sell_venue,
                "sell_url": market_sell_url(h.name),
                "sell_deposit_url": sell_deposit_url(),
                "sell_target_usd": h.ask_usd,
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
            "reject_reasons": self.state.reject_reasons,
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
                "sell_venue": settings.sell_venue,
            },
            "urgency_rules": {
                "big_fish": f"≥${big_fish_profit_usd():.2f} profit → Discord at ≤24h left",
                "medium": f"≥${medium_profit_usd():.2f} profit → Discord at ≤15m left",
                "small": f"≥${settings.min_profit_usd:.2f} profit → Discord at ≤5m left",
                "buy_now": "alert immediately with buy link",
            },
        }


scanner = SniperScanner()
