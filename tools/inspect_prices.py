"""Audit price coverage and show which gaps survive the sanity checks."""

from __future__ import annotations

import asyncio
from collections import Counter

from server.config import settings
from server.price_sources import PriceAggregator
from server.pricing import build_arb_deal_from_quote


async def main() -> None:
    agg = PriceAggregator()
    await agg.refresh()
    book = agg.book
    print(f"items in book: {len(book)}")

    counts: Counter[str] = Counter()
    for quote in book.values():
        for src, price in quote.sources.items():
            if price and price > 0:
                counts[src] += 1
        if quote.market_value_usd:
            counts["market_value"] += 1

    print("\nprices per source:")
    for src, count in counts.most_common():
        if src == "market_value":
            kind = "VALUE ANCHOR"
        elif src in settings.buyable_market_set:
            kind = "buyable"
        else:
            kind = "reference only"
        print(f"  {src:14} {count:>7}  ({kind})")

    venue = settings.sell_venue.lower()
    print(f"\nsell venue: {venue}")
    print(f"items with a live {venue} ask: {sum(1 for q in book.values() if q.sources.get(venue))}")
    print(f"items with a value anchor:    {counts['market_value']}")

    reject: dict[str, int] = {}
    deals = [d for d in (build_arb_deal_from_quote(q, reject) for q in book.values()) if d]
    deals.sort(key=lambda d: d.score, reverse=True)

    print("\nrejection funnel:")
    for reason, count in sorted(reject.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>7}  {reason}")

    print(f"\ndeals surviving all sanity checks: {len(deals)}")
    for d in deals[:25]:
        e = d.extra or {}
        print(
            f"  {d.name[:38]:40} buy ${d.buy_price_usd:>6.2f} ({d.source:8}) "
            f"-> sell ${d.sell_target_usd:>6.2f} | profit ${d.net_profit_usd:>5.2f} "
            f"| {d.discount_pct:>4.1f}% | agree={e.get('sources_agree')} qty={e.get('sell_orders')}"
        )


if __name__ == "__main__":
    asyncio.run(main())
