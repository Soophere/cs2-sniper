from __future__ import annotations

from typing import Any

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


def estimate_profit(
    buy_cents: int,
    market_cents: int,
    highest_bid_cents: int | None = None,
) -> tuple[float, float, float]:
    """Return (sell_target_usd, net_profit_usd, discount_pct)."""
    if market_cents <= 0:
        return 0.0, 0.0, 0.0

    fee_multiplier = 1.0 - (settings.csfloat_seller_fee_pct / 100.0)
    bid = highest_bid_cents or 0

    # Conservative: sell at bid if strong, else slightly under market ask
    if bid >= market_cents * 0.92:
        sell_cents = bid
    else:
        sell_cents = int(market_cents * 0.97)

    net_cents = int(sell_cents * fee_multiplier) - buy_cents
    discount_pct = ((market_cents - buy_cents) / market_cents) * 100.0

    return cents_to_usd(sell_cents), cents_to_usd(net_cents), discount_pct


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

    # Boost deals that are actually actionable soon
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
    if not name:
        return None

    listing_type = str(listing.get("type") or "buy_now")
    auction = listing.get("auction_details") or {}

    # For auctions, current price is top bid / listing price
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

    predicted_cents = int(reference.get("predicted_price") or 0)
    base_cents = int(reference.get("base_price") or 0)

    os_csfloat = (openskin_row or {}).get("csfloat") or {}
    os_steam = (openskin_row or {}).get("steam") or {}

    ask_usd = os_csfloat.get("ask") or (quote.sources.get("csfloat") if quote else None)
    ask_cents = int(ask_usd * 100) if ask_usd else 0
    bid_usd = os_csfloat.get("highest_bid")
    if bid_usd is None and quote and quote.highest_bid_usd is not None:
        bid_usd = quote.highest_bid_usd
    bid_cents = int(bid_usd * 100) if bid_usd else None

    market_candidates = [c for c in [predicted_cents, ask_cents, base_cents] if c > 0]

    # Cross-check with aggregated western markets (Skinport / Steam / CSGOMarket)
    if quote:
        if quote.median_western:
            market_candidates.append(int(quote.median_western * 100))
        if quote.sources.get("skinport"):
            market_candidates.append(int(quote.sources["skinport"] * 100))
        if quote.sources.get("steam"):
            market_candidates.append(int(quote.sources["steam"] * 100))
        if quote.sources.get("csgomarket"):
            market_candidates.append(int(quote.sources["csgomarket"] * 100))

    if not market_candidates:
        scm = item.get("scm") or {}
        scm_price = int(scm.get("price") or 0)
        if scm_price > 0:
            market_candidates.append(scm_price)

    if not market_candidates:
        return None

    # Conservative market: prefer CSFloat ask/predicted, else median western
    preferred = [c for c in [ask_cents, predicted_cents] if c > 0]
    market_cents = min(preferred) if preferred else min(market_candidates)

    # If CSFloat "predicted" is way above other western asks, trust the cheaper western median
    if quote and quote.median_western:
        western_cents = int(quote.median_western * 100)
        if market_cents > western_cents * 1.15:
            market_cents = western_cents

    sell_target_usd, net_profit_usd, discount_pct = estimate_profit(
        buy_cents, market_cents, bid_cents
    )

    if net_profit_usd < settings.min_profit_usd:
        return None
    if discount_pct < settings.min_discount_pct:
        return None

    # Extra safety: buy should also beat Skinport/Steam median when available
    if quote and quote.median_western:
        vs_median_pct = ((quote.median_western * 100) - buy_cents) / (quote.median_western * 100) * 100
        if vs_median_pct < settings.min_discount_pct * 0.5:
            # listed cheap on CSFloat ref but not vs other markets — weak edge
            pass  # still allow; score will be lower via market_cents

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
    # Always surface openskin markets if quote book missed them
    for src in ("csfloat", "skinport", "steam", "buff", "youpin"):
        block = (openskin_row or {}).get(src) or {}
        if block.get("ask") and src not in source_map:
            source_map[src] = float(block["ask"])

    return SnipeDeal(
        listing_id=listing_id,
        name=name,
        category=category,
        buy_price_usd=cents_to_usd(buy_cents),
        market_price_usd=cents_to_usd(market_cents),
        sell_target_usd=sell_target_usd,
        net_profit_usd=net_profit_usd,
        discount_pct=discount_pct,
        liquidity_score=liquidity_score,
        sell_speed=sell_speed_label(liquidity_score, sell_orders, bid_volume, category),
        score=score,
        is_big_hit=is_big_hit,
        buy_url=f"https://csfloat.com/item/{listing_id}",
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
        },
    )
