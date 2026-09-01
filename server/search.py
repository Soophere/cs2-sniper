"""Local skin search across the multi-market price book.

No marketplace site required — type a name fragment and get prices + buy links.
"""

from __future__ import annotations

from typing import Any

from .buy_links import market_buy_url, market_sell_url, sell_deposit_url
from .config import settings
from .models import categorize_item
from .price_sources import MarketQuote, PriceAggregator


def _tokens(query: str) -> list[str]:
    return [t for t in query.lower().split() if t]


def _score_name(name: str, tokens: list[str]) -> float:
    lower = name.lower()
    if not tokens:
        return 0.0
    if not all(t in lower for t in tokens):
        return 0.0

    score = 100.0
    # Exact / prefix matches jump to the top.
    joined = " ".join(tokens)
    if lower == joined:
        score += 80
    elif lower.startswith(joined):
        score += 40
    # Prefer fewer leftover characters (tighter match).
    score -= max(0, len(lower) - len(joined)) * 0.15
    # Prefer earlier first-token hit.
    score -= lower.find(tokens[0]) * 0.05
    return score


def search_price_book(
    book: dict[str, MarketQuote],
    query: str,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    tokens = _tokens(query)
    if not tokens:
        return []

    limit = max(1, min(int(limit), 50))
    scored: list[tuple[float, MarketQuote]] = []
    for quote in book.values():
        score = _score_name(quote.name, tokens)
        if score > 0:
            scored.append((score, quote))

    scored.sort(key=lambda row: (-row[0], row[1].name.lower()))
    venue = settings.sell_venue.lower()
    buyable = settings.buyable_market_set

    results: list[dict[str, Any]] = []
    for score, quote in scored[:limit]:
        sources = {k: round(v, 2) for k, v in quote.sources.items() if v and v > 0}
        buyable_prices = {
            k: v for k, v in sources.items() if k in buyable
        }
        cheapest = None
        if buyable_prices:
            src, price = min(buyable_prices.items(), key=lambda kv: kv[1])
            cheapest = {"source": src, "price_usd": price, "url": market_buy_url(src, quote.name)}
        elif sources:
            src, price = min(sources.items(), key=lambda kv: kv[1])
            cheapest = {"source": src, "price_usd": price, "url": market_buy_url(src, quote.name)}

        sell_ask = sources.get(venue)
        results.append(
            {
                "name": quote.name,
                "category": categorize_item(quote.name).value,
                "score": round(score, 2),
                "prices": sources,
                "volumes": dict(quote.volumes),
                "market_value_usd": quote.market_value_usd,
                "median_western": quote.median_western,
                "cheapest": cheapest,
                "sell_venue": settings.sell_venue,
                "sell_ask_usd": sell_ask,
                "sell_url": market_sell_url(quote.name),
                "sell_deposit_url": sell_deposit_url(),
                "buy_links": {
                    src: market_buy_url(src, quote.name) for src in sources
                },
            }
        )
    return results


async def ensure_book_ready(agg: PriceAggregator) -> None:
    """Refresh once if the book is empty so search still works on a cold start."""
    if not agg.book:
        await agg.refresh(force=True)
