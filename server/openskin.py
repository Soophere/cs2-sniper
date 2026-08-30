from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import settings
from .models import HotItem, ItemCategory, categorize_item


def _ask_value(raw: Any) -> float:
    """openskin lowest_ask can be a float or {marketplace, price}."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict):
        try:
            return float(raw.get("price") or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


class OpenSkinClient:
    BASE = "https://api.openskin.dev/v1"

    def __init__(self) -> None:
        self._cache_all: dict[str, dict[str, Any]] = {}
        self._cache_loaded_at: float = 0.0

    async def fetch_prices_batch(self, names: list[str]) -> dict[str, dict[str, Any]]:
        if not names:
            return {}

        result: dict[str, dict[str, Any]] = {}
        chunk_size = 100

        async with httpx.AsyncClient(timeout=60.0) as client:
            for i in range(0, len(names), chunk_size):
                chunk = names[i : i + chunk_size]
                response = await client.post(
                    f"{self.BASE}/prices/batch",
                    json={"items": chunk},
                    headers={"Accept-Encoding": "gzip"},
                )
                response.raise_for_status()
                payload = response.json()
                result.update(payload.get("data") or {})

        return result

    async def load_all_under_budget(self, max_usd: float = 10.0) -> dict[str, dict[str, Any]]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(
                f"{self.BASE}/prices/all",
                headers={"Accept-Encoding": "gzip"},
            )
            response.raise_for_status()
            payload = response.json()

        filtered: dict[str, dict[str, Any]] = {}
        for name, row in (payload.get("data") or {}).items():
            ask = _ask_value(row.get("lowest_ask")) or _ask_value((row.get("csfloat") or {}).get("ask"))
            if ask and ask <= max_usd:
                filtered[name] = row

        self._cache_all = filtered
        return filtered

    def hot_items_from_cache(self, limit: int = 40) -> list[HotItem]:
        items: list[HotItem] = []
        for name, row in self._cache_all.items():
            csf = row.get("csfloat") or {}
            steam = row.get("steam") or {}
            ask = _ask_value(csf.get("ask")) or _ask_value(row.get("lowest_ask"))
            if not ask:
                continue

            items.append(
                HotItem(
                    name=name,
                    category=categorize_item(name),
                    ask_usd=float(ask),
                    highest_bid_usd=csf.get("highest_bid"),
                    sell_order_count=int(csf.get("sell_order_count") or 0),
                    bid_volume=int(csf.get("bid_volume") or 0),
                    liquidity_score=int(steam.get("liquidity_score") or 0),
                    volume_24h=int(steam.get("volume_24h") or 0),
                )
            )

        items.sort(key=lambda x: x.liquidity_rank, reverse=True)
        return items[:limit]

    async def refresh_hot_items(self) -> list[HotItem]:
        await self.load_all_under_budget(settings.max_buy_usd)
        return self.hot_items_from_cache()


openskin_client = OpenSkinClient()

# Seed watchlist for targeted scans (cases, keys, stickers, cheap liquid skins)
DEFAULT_WATCHLIST = [
    "Revolution Case",
    "Kilowatt Case",
    "Dreams & Nightmares Case",
    "Recoil Case",
    "Fracture Case",
    "Snakebite Case",
    "CS2 Case Key",
    "Chroma Case",
    "Chroma 2 Case",
    "Sticker | Boombl4 (Gold) | Stockholm 2021",
    "Sticker | FaZe Clan | Paris 2023",
    "Sticker | Natus Vincere | Paris 2023",
    "MP9 | Starlight Protector (Field-Tested)",
    "AK-47 | Slate (Field-Tested)",
    "AWP | Atheris (Field-Tested)",
    "M4A1-S | Night Terror (Field-Tested)",
    "Glock-18 | Moonrise (Field-Tested)",
    "USP-S | Ticket to Hell (Field-Tested)",
]
