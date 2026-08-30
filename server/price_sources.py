from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import settings


@dataclass
class MarketQuote:
    name: str
    sources: dict[str, float] = field(default_factory=dict)  # source -> ask USD
    volumes: dict[str, int] = field(default_factory=dict)
    highest_bid_usd: float | None = None
    liquidity_score: int = 0
    volume_24h: int = 0

    @property
    def best_ask(self) -> float | None:
        vals = [v for v in self.sources.values() if v and v > 0]
        return min(vals) if vals else None

    @property
    def western_ask(self) -> float | None:
        """Markets relevant to CSFloat flipping (ignore CN-only dumps)."""
        keys = ("csfloat", "skinport", "steam", "csgomarket", "skincash")
        vals = [self.sources[k] for k in keys if self.sources.get(k)]
        return min(vals) if vals else self.best_ask

    @property
    def median_western(self) -> float | None:
        keys = ("csfloat", "skinport", "steam", "csgomarket")
        vals = sorted(self.sources[k] for k in keys if self.sources.get(k))
        if not vals:
            return self.best_ask
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
            "highest_bid_usd": self.highest_bid_usd,
            "liquidity_score": self.liquidity_score,
            "volume_24h": self.volume_24h,
        }


class PriceAggregator:
    """Multi-source price book. Free APIs, no keys required."""

    def __init__(self) -> None:
        self.book: dict[str, MarketQuote] = {}
        self.source_status: dict[str, str] = {}
        self.last_refresh_at: float = 0.0
        self._lock = asyncio.Lock()

    def get(self, name: str) -> MarketQuote | None:
        return self.book.get(name)

    def status(self) -> dict[str, Any]:
        return {
            "item_count": len(self.book),
            "sources": dict(self.source_status),
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
        # Refresh bulk sources at most every 10 minutes
        if not force and self.last_refresh_at and time.time() - self.last_refresh_at < 600:
            return

        async with self._lock:
            if not force and self.last_refresh_at and time.time() - self.last_refresh_at < 600:
                return
            await asyncio.gather(
                self._load_skinport(),
                self._load_csgomarket(),
                self._load_skincash(),
                return_exceptions=True,
            )
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

            lowest = row.get("lowest_ask")
            if isinstance(lowest, dict) and lowest.get("price"):
                # already covered by per-market
                pass

    async def _load_skinport(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(
                    "https://api.skinport.com/v1/items",
                    params={"app_id": 730, "currency": "USD"},
                    headers={"Accept-Encoding": "br, gzip"},
                )
                response.raise_for_status()
                items = response.json()
            if not isinstance(items, list):
                self.source_status["skinport"] = "bad payload"
                return
            count = 0
            for item in items:
                name = item.get("market_hash_name")
                price = item.get("min_price")
                if name and price is not None and float(price) <= settings.max_buy_usd * 3:
                    self._set_ask(name, "skinport", price, item.get("quantity"))
                    count += 1
            self.source_status["skinport"] = f"ok ({count})"
        except Exception as exc:  # noqa: BLE001
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
                name = item.get("market_hash_name") or item.get("market_hash_name".upper())
                if not name and "market_hash_name" not in item:
                    continue
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


price_aggregator = PriceAggregator()
