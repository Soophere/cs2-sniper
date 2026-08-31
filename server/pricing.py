from __future__ import annotations

from statistics import median as _median
from typing import Any

from .buy_links import csfloat_item_url, market_buy_url, market_sell_url, sell_deposit_url
from .config import settings
from .models import ItemCategory, SnipeDeal, categorize_item, cents_to_usd
from .price_sources import MarketQuote
from .urgency import (
    alert_window_seconds,
    format_time_left,
    parse_expires_at,
    seconds_until,
    urgency_reason,
    urgency_tier,
)


SELL_SPEED_LABELS = {
    "instant": "Instant — cases/keys/high volume",
    "fast": "Fast — liquid, sells in hours",
    "medium": "Medium — 1-3 days",
    "slow": "Slow — low volume, risky",
}


def is_excluded_item(name: str) -> bool:
    lower = name.lower()
    return any(word in lower for word in settings.exclude_keyword_list)


def is_liquid_enough(sell_orders: int, liquidity_score: int, bid_volume: int, category: ItemCategory) -> bool:
    """Reject thin markets that look cheap but won't sell."""
    if category in {ItemCategory.CASE, ItemCategory.KEY}:
        return sell_orders >= max(20, settings.min_market_qty)
    if sell_orders >= settings.min_market_qty:
        return True
    if liquidity_score >= settings.min_liquidity_score and sell_orders >= 3:
        return True
    if bid_volume >= 30 and sell_orders >= 3:
        return True
    return False


def sell_speed_label(liquidity_score: int, sell_orders: int, bid_volume: int, category: ItemCategory) -> str:
    if category in {ItemCategory.CASE, ItemCategory.KEY} and sell_orders > 5000:
        return SELL_SPEED_LABELS["instant"]
    if liquidity_score >= 70 or sell_orders > 500 or bid_volume > 200:
        return SELL_SPEED_LABELS["fast"]
    if liquidity_score >= 45 or sell_orders > 80:
        return SELL_SPEED_LABELS["medium"]
    return SELL_SPEED_LABELS["slow"]


def _seller_fee_multiplier() -> float:
    venue = settings.sell_venue.lower()
    if venue == "skinport":
        return 1.0 - (settings.skinport_seller_fee_pct / 100.0)
    if venue == "waxpeer":
        return 1.0 - (settings.waxpeer_seller_fee_pct / 100.0)
    return 1.0 - (settings.csfloat_seller_fee_pct / 100.0)


def _risk_multiplier() -> float:
    return 1.0 - (settings.price_risk_buffer_pct / 100.0)


def _buyable_prices(quote: MarketQuote | None) -> dict[str, float]:
    """Only markets where cash actually buys the item."""
    if not quote:
        return {}
    allowed = settings.buyable_market_set
    return {k: v for k, v in quote.sources.items() if k in allowed and v and v > 0}


def _venue_ask_cents(
    venue: str,
    quote: MarketQuote | None,
    openskin_row: dict | None,
) -> int:
    src = venue.lower()
    if quote and quote.sources.get(src):
        return int(quote.sources[src] * 100)
    if openskin_row:
        block = openskin_row.get(src) or {}
        ask = block.get("ask")
        if ask:
            return int(float(ask) * 100)
    return 0


def _market_value_cents(quote: MarketQuote | None) -> int:
    """Steam-style 'what is it worth' anchor, independent of any single ask."""
    if quote and quote.market_value_usd:
        return int(quote.market_value_usd * 100)
    return 0


def _venue_from_market_value_cents(quote: MarketQuote | None) -> int:
    """Estimated sell-venue price derived from the market value anchor."""
    mv = _market_value_cents(quote)
    if mv <= 0:
        return 0
    if settings.sell_venue.lower() == "csfloat":
        return int(mv * (settings.csfloat_realisation_pct / 100.0))
    return mv


def _corroborated_sell_cents(
    venue_ask_cents: int,
    quote: MarketQuote | None,
) -> tuple[int, int]:
    """
    Sanity-check the sell price against every other price we have.

    Returns (sell_reference_cents, agreeing_source_count). A venue price that no
    other market comes near is treated as bad data and pulled down to the
    cross-market median, so an outlier can never invent profit.
    """
    others: list[float] = []
    if quote:
        others = [v * 100 for v in quote.sources.values() if v and v > 0]
        mv = _market_value_cents(quote)
        if mv > 0:
            others.append(float(mv))

    if not others:
        return venue_ask_cents, 1 if venue_ask_cents > 0 else 0

    cross_median = int(_median(others))
    if venue_ask_cents <= 0:
        return cross_median, len(others)

    agree = sum(1 for p in others if abs(p - venue_ask_cents) <= venue_ask_cents * 0.30)

    # Venue price far above the crowd → trust the crowd, not the outlier.
    if cross_median > 0 and venue_ask_cents > cross_median * 1.5:
        return cross_median, agree

    return venue_ask_cents, agree


def _skin_only_value_cents(reference: dict, item: dict) -> int:
    """
    Value of the bare skin, ignoring sticker/charm markup.

    CSFloat's `base_price` is the skin without applied items; `predicted_price`
    can already include a sticker guess, so the lower of the two is safer.
    """
    base = int(reference.get("base_price") or 0)
    predicted = int(reference.get("predicted_price") or 0)
    candidates = [c for c in (base, predicted) if c > 0]
    if not candidates:
        scm = int((item.get("scm") or {}).get("price") or 0)
        return scm
    return min(candidates)


def _has_applied_items(item: dict) -> bool:
    return bool(item.get("stickers")) or bool(item.get("keychains")) or bool(item.get("charms"))


def estimate_profit(
    buy_cents: int,
    sell_reference_cents: int,
    highest_bid_cents: int | None = None,
) -> tuple[float, float, float]:
    """Return (sell_target_usd, net_profit_usd, discount_pct) — after fees and risk buffer."""
    if sell_reference_cents <= 0:
        return 0.0, 0.0, 0.0

    fee_multiplier = _seller_fee_multiplier()
    realistic_cents = int(sell_reference_cents * _risk_multiplier())

    # An instant-sell bid is the only price you can actually hit today.
    bid = highest_bid_cents or 0
    if settings.sell_venue.lower() == "csfloat" and bid > realistic_cents:
        realistic_cents = bid

    net_cents = int(realistic_cents * fee_multiplier) - buy_cents
    discount_pct = ((sell_reference_cents - buy_cents) / sell_reference_cents) * 100.0

    return cents_to_usd(realistic_cents), cents_to_usd(net_cents), discount_pct


def score_deal(
    net_profit_usd: float,
    discount_pct: float,
    liquidity_score: int,
    sell_orders: int,
    category: ItemCategory,
    urgency: str,
    seconds_left: float | None,
) -> tuple[float, bool]:
    category_boost = {
        ItemCategory.CASE: 1.15,
        ItemCategory.KEY: 1.1,
        ItemCategory.STICKER: 1.05,
        ItemCategory.SKIN: 1.0,
        ItemCategory.OTHER: 0.95,
    }.get(category, 1.0)

    liquidity_factor = min(liquidity_score / 50.0, 2.0) + min(sell_orders / 1000.0, 1.5)
    score = net_profit_usd * liquidity_factor * category_boost + (discount_pct * 0.05)

    if urgency == "actionable":
        score *= 1.35
        if seconds_left is not None and seconds_left <= 15 * 60:
            score *= 1.25
    elif urgency == "watching":
        score *= 0.55

    is_big_hit = (
        (net_profit_usd >= settings.min_profit_usd * 2 or discount_pct >= 25)
        and sell_orders >= settings.min_market_qty
    )
    return score, is_big_hit


def build_deal_from_listing(
    listing: dict[str, Any],
    openskin_row: dict[str, Any] | None = None,
    quote: MarketQuote | None = None,
) -> SnipeDeal | None:
    item = listing.get("item") or {}
    name = item.get("market_hash_name") or ""
    if not name or is_excluded_item(name):
        return None

    listing_type = str(listing.get("type") or "buy_now")
    auction = listing.get("auction_details") or {}

    buy_cents = int(listing.get("price") or 0)
    if listing_type == "auction":
        top_bid = (auction.get("top_bid") or {}).get("price")
        reserve = auction.get("reserve_price")
        if top_bid:
            buy_cents = int(top_bid)
        elif reserve is not None:
            buy_cents = int(reserve)

    if buy_cents <= 0 or buy_cents > settings.max_buy_cents:
        return None

    category = categorize_item(name)
    reference = listing.get("reference") or {}

    os_csfloat = (openskin_row or {}).get("csfloat") or {}
    os_steam = (openskin_row or {}).get("steam") or {}

    bid_usd = os_csfloat.get("highest_bid")
    if bid_usd is None and quote and quote.highest_bid_usd is not None:
        bid_usd = quote.highest_bid_usd
    bid_cents = int(bid_usd * 100) if bid_usd else None

    # Sticker trap: never pay a premium over the bare skin value.
    skin_only_cents = _skin_only_value_cents(reference, item)
    if _has_applied_items(item) and skin_only_cents > 0:
        premium_pct = ((buy_cents - skin_only_cents) / skin_only_cents) * 100.0
        if premium_pct > settings.max_sticker_premium_pct:
            return None

    # Sell-venue price, best source first:
    #   1. a live ask on the venue
    #   2. CSFloat's own valuation of this skin (base/predicted price)
    #   3. the Steam-style market value, haircut to what the venue realises
    venue_ask_cents = _venue_ask_cents(settings.sell_venue, quote, openskin_row)
    if venue_ask_cents <= 0 and settings.sell_venue.lower() == "csfloat":
        venue_ask_cents = skin_only_cents
    if venue_ask_cents <= 0:
        venue_ask_cents = _venue_from_market_value_cents(quote)
    if venue_ask_cents <= 0:
        # No price on the market we actually sell on → we cannot value the exit.
        return None

    sell_ref_cents, agree_count = _corroborated_sell_cents(venue_ask_cents, quote)
    if sell_ref_cents <= 0:
        return None
    if agree_count < settings.min_sources_agree:
        return None

    # Never value the exit above the bare skin value.
    if skin_only_cents > 0:
        sell_ref_cents = min(sell_ref_cents, skin_only_cents)

    sell_target_usd, net_profit_usd, discount_pct = estimate_profit(
        buy_cents, sell_ref_cents, bid_cents
    )

    if net_profit_usd < settings.min_profit_usd:
        return None
    if discount_pct < settings.min_discount_pct:
        return None
    # Too-good-to-be-true gap = mismatched item or stale data.
    if discount_pct > settings.max_discount_pct:
        return None

    sell_orders = int(
        os_csfloat.get("sell_order_count")
        or (quote.volumes.get("csfloat") if quote else 0)
        or reference.get("quantity")
        or 0
    )
    bid_volume = int(os_csfloat.get("bid_volume") or 0)
    liquidity_score = int(
        os_steam.get("liquidity_score") or (quote.liquidity_score if quote else 0) or 0
    )

    if not is_liquid_enough(sell_orders, liquidity_score, bid_volume, category):
        return None

    expires = parse_expires_at(listing)
    secs_left = seconds_until(expires)
    provisional_big = net_profit_usd >= settings.min_profit_usd * 2 or discount_pct >= 25
    urgency = urgency_tier(listing_type, net_profit_usd, provisional_big, secs_left)
    if urgency == "expired":
        return None

    score, is_big_hit = score_deal(
        net_profit_usd,
        discount_pct,
        liquidity_score,
        sell_orders,
        category,
        urgency,
        secs_left,
    )

    window = alert_window_seconds(net_profit_usd, is_big_hit)
    urgency = urgency_tier(listing_type, net_profit_usd, is_big_hit, secs_left)
    reason = urgency_reason(net_profit_usd, is_big_hit, secs_left, listing_type)

    icon = item.get("icon_url")
    if icon and not icon.startswith("http"):
        icon = f"https://community.cloudflare.steamstatic.com/economy/image/{icon}"

    listing_id = str(listing.get("id"))
    source_map = dict(quote.sources) if quote else {}
    for src in ("csfloat", "skinport", "steam", "buff", "youpin"):
        block = (openskin_row or {}).get(src) or {}
        if block.get("ask") and src not in source_map:
            source_map[src] = float(block["ask"])

    buy_url = csfloat_item_url(listing_id)
    sell_url = market_sell_url(name)

    return SnipeDeal(
        listing_id=listing_id,
        name=name,
        category=category,
        buy_price_usd=cents_to_usd(buy_cents),
        market_price_usd=cents_to_usd(sell_ref_cents),
        sell_target_usd=sell_target_usd,
        net_profit_usd=net_profit_usd,
        discount_pct=discount_pct,
        liquidity_score=liquidity_score,
        sell_speed=sell_speed_label(liquidity_score, sell_orders, bid_volume, category),
        score=score,
        is_big_hit=is_big_hit,
        buy_url=buy_url,
        icon_url=icon,
        float_value=item.get("float_value"),
        source="csfloat",
        listing_type=listing_type,
        expires_at=expires.isoformat() if expires else None,
        seconds_left=secs_left,
        time_left_label=format_time_left(secs_left),
        urgency=urgency,
        urgency_reason=reason,
        alert_window_sec=window,
        extra={
            "sell_orders": sell_orders,
            "bid_volume": bid_volume,
            "reference_qty": reference.get("quantity"),
            "min_next_bid": auction.get("min_next_bid"),
            "prices": source_map,
            "median_western": quote.median_western if quote else None,
            "sell_venue": settings.sell_venue,
            "sell_url": sell_url,
            "sell_deposit_url": sell_deposit_url(),
            "sources_agree": agree_count,
            "instant_bid_usd": cents_to_usd(bid_cents) if bid_cents else None,
            "skin_only_usd": cents_to_usd(skin_only_cents) if skin_only_cents else None,
            "has_stickers": _has_applied_items(item),
            "buy_links": {
                "csfloat": buy_url,
                **{
                    src: market_buy_url(src, name)
                    for src in source_map
                    if src != "csfloat"
                },
            },
        },
    )


def build_arb_deal_from_quote(
    quote: MarketQuote,
    reject: dict[str, int] | None = None,
) -> SnipeDeal | None:
    """
    Cross-market underprice hit from free price dumps (no CSFloat listing needed).

    Pass `reject` to tally why candidates were dropped (used by tools/inspect_prices).
    """

    def drop(reason: str) -> None:
        if reject is not None:
            reject[reason] = reject.get(reason, 0) + 1

    if is_excluded_item(quote.name):
        drop("excluded item type")
        return None

    venue = settings.sell_venue.lower()
    venue_cents = int((quote.sources.get(venue) or 0) * 100)
    if venue_cents <= 0:
        # No live venue ask — fall back to the market-value anchor, haircut to
        # what the venue actually realises. Without either there is no real exit.
        venue_cents = _venue_from_market_value_cents(quote)
    if venue_cents <= 0:
        drop("no sell-venue price or value anchor")
        return None

    sell_ref_cents, agree_count = _corroborated_sell_cents(venue_cents, quote)
    if sell_ref_cents <= 0:
        return None
    if agree_count < settings.min_sources_agree:
        drop("not enough markets agree on value")
        return None

    # Buy only where cash actually buys the item, and take the true cheapest.
    buyable = {k: v for k, v in _buyable_prices(quote).items() if k != venue}
    buyable = {k: v for k, v in buyable.items() if v <= settings.max_buy_usd}
    if not buyable:
        drop("no cash-buyable market under budget")
        return None

    buy_source, buy_usd = min(buyable.items(), key=lambda kv: kv[1])
    sell_ref_usd = sell_ref_cents / 100.0
    if buy_usd >= sell_ref_usd:
        drop("buy price not below value")
        return None

    discount_pct = ((sell_ref_usd - buy_usd) / sell_ref_usd) * 100.0
    if discount_pct < settings.min_discount_pct:
        drop("gap too small")
        return None
    if discount_pct > settings.max_discount_pct:
        drop("gap too big (bad data)")
        return None

    sell_target_usd, net_profit, _ = estimate_profit(
        int(buy_usd * 100),
        sell_ref_cents,
        int(quote.highest_bid_usd * 100) if quote.highest_bid_usd else None,
    )
    if net_profit < settings.min_profit_usd:
        drop("profit below minimum after fees")
        return None

    category = categorize_item(quote.name)
    # No CSFloat volume feed exists, so use supply on the markets we can see as
    # the liquidity proxy: lots of copies listed elsewhere means it trades.
    sell_orders = max(
        [v for k, v in quote.volumes.items() if k in settings.buyable_market_set] or [0]
    )
    if not is_liquid_enough(sell_orders, quote.liquidity_score, 0, category):
        drop("too illiquid")
        return None

    urgency = "actionable"
    score, is_big_hit = score_deal(
        net_profit,
        discount_pct,
        quote.liquidity_score,
        sell_orders,
        category,
        urgency,
        None,
    )
    buy_url = market_buy_url(buy_source, quote.name)
    sell_url = market_sell_url(quote.name)
    sell_label = settings.sell_venue.capitalize()

    return SnipeDeal(
        listing_id=f"arb:{buy_source}:{quote.name}",
        name=quote.name,
        category=category,
        buy_price_usd=round(buy_usd, 2),
        market_price_usd=round(sell_ref_usd, 2),
        sell_target_usd=sell_target_usd,
        net_profit_usd=net_profit,
        discount_pct=discount_pct,
        liquidity_score=quote.liquidity_score,
        sell_speed=sell_speed_label(quote.liquidity_score, sell_orders, 0, category),
        score=score,
        is_big_hit=is_big_hit,
        buy_url=buy_url,
        icon_url=None,
        float_value=None,
        source=buy_source,
        listing_type="buy_now",
        expires_at=None,
        seconds_left=None,
        time_left_label="Buy now",
        urgency=urgency,
        urgency_reason=(
            f"Buy on {buy_source} ${buy_usd:.2f} → sell on {sell_label} "
            f"~${sell_target_usd:.2f} ({agree_count} markets agree)"
        ),
        alert_window_sec=0,
        extra={
            "sell_orders": sell_orders,
            "prices": dict(quote.sources),
            "median_western": quote.median_western,
            "buy_source": buy_source,
            "sell_venue": settings.sell_venue,
            "sell_url": sell_url,
            "sell_deposit_url": sell_deposit_url(),
            "sources_agree": agree_count,
            "market_value_usd": quote.market_value_usd,
            "buy_links": {
                src: market_buy_url(src, quote.name)
                for src in quote.sources
                if src in settings.buyable_market_set
            },
        },
    )
