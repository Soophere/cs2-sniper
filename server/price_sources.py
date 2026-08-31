from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .config import settings

CACHE_DIR = Path(__file__).resolve().parent.parent / "data"
# Skinport allows very few calls on the full item dump and is our only source of
# the USD value anchor, so we cache it and only refetch occasionally.
SKINPORT_CACHE = CACHE_DIR / "skinport_cache.json"
SKINPORT_MIN_INTERVAL_SEC = 1800


@dataclass
class MarketQuote:
    name: str
    sources: dict[str, float] = field(default_factory=dict)  # source -> ask USD
    volumes: dict[str, int] = field(default_factory=dict)
    highest_bid_usd: float | None = None
    liquidity_score: int = 0
    volume_24h: int = 0
    # Steam-style market value (Skinport `suggested_price`). Not a buyable ask —
    # this is the "what is this skin actually worth" anchor.
    market_value_usd: float | None = None

    @property
    def buyable_sources(self) -> dict[str, float]:
        """Only markets where cash buys the item and you receive it."""
        allowed = settings.buyable_market_set
        return {k: v for k, v in self.sources.items() if k in allowed and v and v > 0}

    @property
    def best_ask(self) -> float | None:
        vals = [v for v in self.sources.values() if v and v > 0]
        return min(vals) if vals else None

    @property
    def cheapest_buyable(self) -> tuple[str, float] | None:
        vals = self.buyable_sources
        if not vals:
            return None
        return min(vals.items(), key=lambda x: x[1])

    @property
    def cheapest_source(self) -> tuple[str, float] | None:
        vals = [(k, v) for k, v in self.sources.items() if v and v > 0]
        if not vals:
            return None
        return min(vals, key=lambda x: x[1])

    @property
    def western_ask(self) -> float | None:
        """Cheapest price you can actually pay in cash."""
        vals = list(self.buyable_sources.values())
        return min(vals) if vals else None

    @property
    def median_western(self) -> float | None:
        """Median across buyable cash markets only (trade/cashout sites excluded)."""
        vals = sorted(self.buyable_sources.values())
        if not vals:
            return None
        mid = len(vals) // 2
        if len(vals) % 2:
            return vals[mid]
        return (vals[mid - 1] + vals[mid]) / 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sources": self.sources,
            "volumes": self.volumes,
            "best_ask": self.best_ask,
            "western_ask": self.western_ask,
            "median_western": self.median_western,
            "market_value_usd": self.market_value_usd,
            "highest_bid_usd": self.highest_bid_usd,
            "liquidity_score": self.liquidity_score,
            "volume_24h": self.volume_24h,
        }


class PriceAggregator:
    """Multi-source price book. Free public dumps — no keys required."""

    def __init__(self) -> None:
        self.book: dict[str, MarketQuote] = {}
        self.source_status: dict[str, str] = {}
        self.last_refresh_at: float = 0.0
        self._lock = asyncio.Lock()

    def get(self, name: str) -> MarketQuote | None:
        return self.book.get(name)

    def status(self) -> dict[str, Any]:
        ok = sum(1 for v in self.source_status.values() if v.startswith("ok"))
        return {
            "item_count": len(self.book),
            "sources": dict(self.source_status),
            "sources_ok": ok,
            "sources_total": len(self.source_status),
            "last_refresh_at": self.last_refresh_at or None,
            "age_sec": int(time.time() - self.last_refresh_at) if self.last_refresh_at else None,
        }

    def _ensure(self, name: str) -> MarketQuote:
        if name not in self.book:
            self.book[name] = MarketQuote(name=name)
        return self.book[name]

    def _set_ask(self, name: str, source: str, price: float | None, qty: int | None = None) -> None:
        if not name or price is None:
            return
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            return
        if price_f <= 0:
            return
        q = self._ensure(name)
        q.sources[source] = round(price_f, 4)
        if qty is not None:
            try:
                q.volumes[source] = int(qty)
            except (TypeError, ValueError):
                pass

    async def refresh(self, force: bool = False) -> None:
        # Bulk dumps refresh at most every 8 minutes
        if not force and self.last_refresh_at and time.time() - self.last_refresh_at < 480:
            return

        async with self._lock:
            if not force and self.last_refresh_at and time.time() - self.last_refresh_at < 480:
                return
            await asyncio.gather(
                self._load_skinport(),
                self._load_csgomarket(),
                self._load_skincash(),
                self._load_waxpeer(),
                self._load_lootfarm(),
                return_exceptions=True,
            )
            # Runs second: it calibrates itself against the USD anchors above.
            try:
                await self._load_bitskins()
            except Exception:  # noqa: BLE001
                pass
            self.last_refresh_at = time.time()

    async def merge_openskin_batch(self, rows: dict[str, dict[str, Any]]) -> None:
        for name, row in rows.items():
            q = self._ensure(name)
            for source in ("csfloat", "skinport", "steam", "buff", "youpin"):
                block = row.get(source) or {}
                ask = block.get("ask")
                if ask:
                    q.sources[source] = float(ask)
                qty = block.get("sell_order_count") or block.get("ask_volume")
                if qty is not None:
                    try:
                        q.volumes[source] = int(qty)
                    except (TypeError, ValueError):
                        pass

            csf = row.get("csfloat") or {}
            if csf.get("highest_bid") is not None:
                q.highest_bid_usd = float(csf["highest_bid"])

            steam = row.get("steam") or {}
            if steam.get("liquidity_score") is not None:
                q.liquidity_score = int(steam["liquidity_score"])
            if steam.get("volume_24h") is not None:
                q.volume_24h = int(steam["volume_24h"])

    def _apply_skinport(self, items: list[dict[str, Any]]) -> int:
        count = 0
        for item in items:
            name = item.get("market_hash_name")
            if not name:
                continue
            price = item.get("min_price")
            if price is not None and float(price) <= settings.max_buy_usd * 3:
                self._set_ask(name, "skinport", price, item.get("quantity"))
                count += 1
            # `suggested_price` tracks the Steam market value and is our anchor
            # for what the skin is really worth.
            suggested = item.get("suggested_price") or item.get("median_price")
            if suggested:
                try:
                    self._ensure(name).market_value_usd = round(float(suggested), 4)
                except (TypeError, ValueError):
                    pass
        return count

    def _read_skinport_cache(self) -> tuple[list[dict[str, Any]], float] | None:
        try:
            if not SKINPORT_CACHE.exists():
                return None
            blob = json.loads(SKINPORT_CACHE.read_text(encoding="utf-8"))
            items = blob.get("items")
            if isinstance(items, list) and items:
                return items, float(blob.get("fetched_at") or 0)
        except Exception:  # noqa: BLE001
            return None
        return None

    def _write_skinport_cache(self, items: list[dict[str, Any]]) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            SKINPORT_CACHE.write_text(
                json.dumps({"fetched_at": time.time(), "items": items}),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    async def _load_skinport(self) -> None:
        """
        Skinport asks plus the USD value anchor.

        Cached to disk: the dump is heavily rate limited and it is the only place
        we get a real USD reference, so a 429 must degrade to stale data rather
        than wiping the price book.
        """
        cached = self._read_skinport_cache()
        if cached:
            items, fetched_at = cached
            age = time.time() - fetched_at
            if age < SKINPORT_MIN_INTERVAL_SEC:
                count = self._apply_skinport(items)
                self.source_status["skinport"] = f"ok ({count}, cached {int(age / 60)}m)"
                return

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(
                    "https://api.skinport.com/v1/items",
                    params={"app_id": 730, "currency": "USD"},
                    headers={"Accept-Encoding": "br, gzip", "Accept": "application/json"},
                )
                response.raise_for_status()
                items = response.json()
            if not isinstance(items, list) or not items:
                raise ValueError("bad payload")
            self._write_skinport_cache(items)
            count = self._apply_skinport(items)
            self.source_status["skinport"] = f"ok ({count}, fresh)"
        except Exception as exc:  # noqa: BLE001
            if cached:
                items, fetched_at = cached
                count = self._apply_skinport(items)
                age_min = int((time.time() - fetched_at) / 60)
                self.source_status["skinport"] = (
                    f"stale cache ({count}, {age_min}m old) — {type(exc).__name__}"
                )
            else:
                self.source_status["skinport"] = f"error: {exc}"

    async def _load_csgomarket(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get("https://market.csgo.com/api/v2/prices/USD.json")
                response.raise_for_status()
                payload = response.json()
            items = payload.get("items") or []
            count = 0
            if isinstance(items, list):
                iterable = items
            elif isinstance(items, dict):
                iterable = [
                    {"market_hash_name": k, **(v if isinstance(v, dict) else {"price": v})}
                    for k, v in items.items()
                ]
            else:
                self.source_status["csgomarket"] = "bad payload"
                return

            for item in iterable:
                name = item.get("market_hash_name")
                price = item.get("price")
                vol = item.get("volume")
                if name and price is not None:
                    try:
                        price_f = float(price)
                    except (TypeError, ValueError):
                        continue
                    if price_f <= settings.max_buy_usd * 3:
                        qty = None
                        try:
                            qty = int(str(vol).replace(",", "")) if vol is not None else None
                        except ValueError:
                            qty = None
                        self._set_ask(name, "csgomarket", price_f, qty)
                        count += 1
            self.source_status["csgomarket"] = f"ok ({count})"
        except Exception as exc:  # noqa: BLE001
            self.source_status["csgomarket"] = f"error: {exc}"

    async def _load_skincash(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(
                    "https://api.skincash.gg/v1/prices",
                    headers={"Accept-Encoding": "gzip"},
                )
                response.raise_for_status()
                payload = response.json()
            items = payload.get("items") or []
            count = 0
            for item in items:
                name = item.get("market_hash_name")
                price = item.get("price")
                if name and price is not None and float(price) <= settings.max_buy_usd * 3:
                    self._set_ask(name, "skincash", price, item.get("quantity"))
                    count += 1
            self.source_status["skincash"] = f"ok ({count})"
        except Exception as exc:  # noqa: BLE001
            self.source_status["skincash"] = f"error: {exc}"

    async def _load_waxpeer(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get("https://api.waxpeer.com/v1/prices?game=csgo")
                response.raise_for_status()
                payload = response.json()
            items = payload.get("items") or []
            count = 0
            for item in items:
                name = item.get("name")
                # Waxpeer `min` is USD cents
                cents = item.get("min")
                if name is None or cents is None:
                    continue
                try:
                    price_f = float(cents) / 100.0
                except (TypeError, ValueError):
                    continue
                if 0 < price_f <= settings.max_buy_usd * 3:
                    self._set_ask(name, "waxpeer", price_f, item.get("count"))
                    count += 1
            self.source_status["waxpeer"] = f"ok ({count})"
        except Exception as exc:  # noqa: BLE001
            self.source_status["waxpeer"] = f"error: {exc}"

    async def _load_bitskins(self) -> None:
        """
        BitSkins suggested prices as an extra corroborating opinion.

        BitSkins publishes prices in an undocumented fixed-point unit, so rather
        than hardcode a divisor we infer it from items we already price in real
        USD and reject the whole feed if it does not line up. That way a units
        change upstream can never silently inflate profit estimates.
        """
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(
                    "https://api.bitskins.com/market/skin/730",
                    headers={"Accept-Encoding": "gzip"},
                )
                response.raise_for_status()
                rows = response.json()
            if not isinstance(rows, list):
                self.source_status["bitskins"] = "bad payload"
                return

            anchors = {
                name: q.market_value_usd
                for name, q in self.book.items()
                if q.market_value_usd and q.market_value_usd > 0
            }
            if len(anchors) < 500:
                self.source_status["bitskins"] = "skipped (no USD anchors yet)"
                return

            pairs: list[tuple[str, float, float]] = []
            for row in rows:
                name = row.get("name")
                raw = row.get("suggested_price")
                if not name or not raw:
                    continue
                ref = anchors.get(name)
                if not ref or ref <= 0:
                    continue
                try:
                    pairs.append((name, float(raw), ref))
                except (TypeError, ValueError):
                    continue

            if len(pairs) < 200:
                self.source_status["bitskins"] = f"skipped (only {len(pairs)} overlap)"
                return

            ratios = sorted(raw / ref for _, raw, ref in pairs)
            scale = ratios[len(ratios) // 2]
            # Snap to the nearest sane fixed-point divisor.
            divisor = min((1.0, 10.0, 100.0, 1000.0), key=lambda d: abs(scale - d))
            if not 0.5 <= scale / divisor <= 2.0:
                self.source_status["bitskins"] = f"rejected (odd scale {scale:.1f})"
                return

            agree = sum(
                1 for _, raw, ref in pairs if abs(((raw / divisor) / ref) - 1.0) <= 0.25
            )
            agree_pct = agree / len(pairs) * 100
            if agree_pct < 50:
                self.source_status["bitskins"] = (
                    f"rejected (only {agree_pct:.0f}% match real USD)"
                )
                return

            count = 0
            for row in rows:
                name = row.get("name")
                raw = row.get("suggested_price")
                if not name or not raw:
                    continue
                try:
                    price = float(raw) / divisor
                except (TypeError, ValueError):
                    continue
                if 0 < price <= settings.max_buy_usd * 3:
                    self._set_ask(name, "bitskins", price)
                    count += 1
            self.source_status["bitskins"] = (
                f"ok ({count}, /{divisor:.0f}, {agree_pct:.0f}% match)"
            )
        except Exception as exc:  # noqa: BLE001
            self.source_status["bitskins"] = f"error: {exc}"

    async def _load_lootfarm(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get("https://loot.farm/fullprice.json")
                response.raise_for_status()
                items = response.json()
            if not isinstance(items, list):
                self.source_status["lootfarm"] = "bad payload"
                return
            count = 0
            for item in items:
                name = item.get("name")
                cents = item.get("price")
                if name is None or cents is None:
                    continue
                try:
                    price_f = float(cents) / 100.0
                except (TypeError, ValueError):
                    continue
                if 0 < price_f <= settings.max_buy_usd * 3:
                    self._set_ask(name, "lootfarm", price_f, item.get("have"))
                    count += 1
            self.source_status["lootfarm"] = f"ok ({count})"
        except Exception as exc:  # noqa: BLE001
            self.source_status["lootfarm"] = f"error: {exc}"


price_aggregator = PriceAggregator()
