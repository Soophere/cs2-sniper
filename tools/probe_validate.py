"""Check the bitskins loader passes its own self-calibration.

Caches the Skinport dump locally so repeated runs don't trip its rate limit.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from server.price_sources import PriceAggregator

CACHE = Path(__file__).parent / ".skinport_cache.json"


async def main() -> None:
    agg = PriceAggregator()

    if CACHE.exists():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        for name, value in cached.items():
            agg._ensure(name).market_value_usd = value
        print(f"loaded {len(cached)} cached Skinport anchors")
    else:
        await agg._load_skinport()
        print(f"skinport: {agg.source_status.get('skinport')}")
        anchors = {
            name: q.market_value_usd
            for name, q in agg.book.items()
            if q.market_value_usd
        }
        if anchors:
            CACHE.write_text(json.dumps(anchors), encoding="utf-8")
            print(f"cached {len(anchors)} Skinport anchors")

    anchors = sum(1 for q in agg.book.values() if q.market_value_usd)
    print(f"USD anchors available: {anchors}")

    await agg._load_bitskins()
    print(f"\nbitskins: {agg.source_status.get('bitskins')}")

    priced = sum(1 for q in agg.book.values() if q.sources.get("bitskins"))
    print(f"items now carrying a bitskins price: {priced}")


if __name__ == "__main__":
    asyncio.run(main())
