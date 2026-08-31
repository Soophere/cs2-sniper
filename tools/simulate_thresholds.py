"""How many verified deals appear at different minimum-profit settings?

Uses the live cached price book so the comparison is apples to apples.
"""

from __future__ import annotations

import asyncio

from server.config import settings
from server.price_sources import PriceAggregator
from server.pricing import build_arb_deal_from_quote


async def main() -> None:
    agg = PriceAggregator()
    await agg.refresh()
    quotes = list(agg.book.values())
    print(f"price book: {len(quotes)} items")
    print(f"budget: ${settings.max_buy_usd:.2f} · agreement required: {settings.min_sources_agree}\n")

    original = settings.min_profit_usd
    print(f"{'min profit':>11} | {'deals':>5} | {'gap needed':>10} | best few")
    print("-" * 78)
    try:
        for threshold in (2.0, 1.5, 1.0, 0.75, 0.5):
            settings.min_profit_usd = threshold
            deals = [d for d in (build_arb_deal_from_quote(q) for q in quotes) if d]
            deals.sort(key=lambda d: d.net_profit_usd, reverse=True)
            # Gap a max-budget buy needs to clear this profit bar.
            gap = threshold / settings.max_buy_usd * 100
            names = ", ".join(f"{d.name[:22]} +${d.net_profit_usd:.2f}" for d in deals[:2])
            print(f"{threshold:>10.2f} | {len(deals):>5} | {gap:>9.0f}% | {names}")
    finally:
        settings.min_profit_usd = original

    settings.min_profit_usd = 1.0
    deals = [d for d in (build_arb_deal_from_quote(q) for q in quotes) if d]
    settings.min_profit_usd = original
    total = sum(d.net_profit_usd for d in deals)
    print(f"\nAt a $1.00 floor: {len(deals)} deals, ${total:.2f} total profit on the table.")
    if deals:
        avg = total / len(deals)
        print(f"Average profit per flip: ${avg:.2f}")
        print(f"Flips needed to turn $10 into $40: about {int(30 / avg) + 1}")


if __name__ == "__main__":
    asyncio.run(main())
