from __future__ import annotations

import httpx

from .buy_links import sell_deposit_url
from .config import settings
from .models import SnipeDeal


async def send_discord_alert(deal: SnipeDeal) -> bool:
    webhook = settings.discord_webhook_url.strip()
    if not webhook:
        return False

    if deal.urgency != "actionable":
        return False

    if deal.is_big_hit or deal.net_profit_usd >= 6:
        color = 0xFF4444
        title_prefix = "BIG FISH"
    elif deal.seconds_left is not None and deal.seconds_left <= 5 * 60:
        color = 0xFFAA00
        title_prefix = "ENDING SOON"
    else:
        color = 0x44AA44
        title_prefix = "SNIPE"

    if deal.listing_type == "auction":
        action_hint = f"Auction ends in **{deal.time_left_label}** — act before bids climb"
    else:
        action_hint = "Buy now — tap the link and purchase if still listed"

    prices = (deal.extra or {}).get("prices") or {}
    price_bits = []
    for key in ("csfloat", "skinport", "steam", "csgomarket", "waxpeer", "skincash", "lootfarm"):
        if prices.get(key) is not None:
            price_bits.append(f"{key} ${float(prices[key]):.2f}")
    price_line = " · ".join(price_bits) if price_bits else "n/a"

    buy_links = (deal.extra or {}).get("buy_links") or {}
    sell_url = (deal.extra or {}).get("sell_url") or sell_deposit_url()
    sell_deposit = (deal.extra or {}).get("sell_deposit_url") or sell_deposit_url()
    sell_venue = (deal.extra or {}).get("sell_venue") or settings.sell_venue

    link_bits = [f"[👉 BUY NOW]({deal.buy_url})", f"[Sell on {sell_venue.capitalize()}]({sell_deposit})"]
    link_bits.append(f"[Check {sell_venue} price]({sell_url})")
    for src, url in list(buy_links.items())[:2]:
        if url and url != deal.buy_url:
            link_bits.append(f"[{src}]({url})")
    links_line = " · ".join(link_bits)

    qty = (deal.extra or {}).get("sell_orders") or (deal.extra or {}).get("reference_qty") or "?"

    embed = {
        "title": f"{title_prefix}: {deal.name}",
        "url": deal.buy_url,
        "color": color,
        "description": (
            f"{action_hint}\n{deal.urgency_reason}\n\n"
            f"**Links:** {links_line}\n"
            f"Flip: buy cheap → sell on **{sell_venue.capitalize()}** (Skinport = reference only)"
        ),
        "fields": [
            {"name": "Buy / Bid", "value": f"${deal.buy_price_usd:.2f}", "inline": True},
            {"name": "Sell target", "value": f"${deal.sell_target_usd:.2f}", "inline": True},
            {"name": "Est. Profit", "value": f"${deal.net_profit_usd:.2f}", "inline": True},
            {"name": "Discount", "value": f"{deal.discount_pct:.1f}%", "inline": True},
            {"name": "Time left", "value": deal.time_left_label, "inline": True},
            {"name": "Sell on", "value": sell_venue.capitalize(), "inline": True},
            {"name": "Liquidity", "value": f"{qty} listings · score {deal.liquidity_score}", "inline": True},
            {"name": "Market prices", "value": price_line[:1000] or "n/a", "inline": False},
            {"name": "Sell speed", "value": deal.sell_speed, "inline": False},
        ],
        "footer": {"text": "CS2 Sniper · click BUY or Sell link"},
    }

    if deal.icon_url:
        embed["thumbnail"] = {"url": deal.icon_url}

    payload = {
        "username": "CS2 Sniper",
        "content": (
            f"**{title_prefix}** +${deal.net_profit_usd:.2f} · {deal.time_left_label}\n"
            f">>> **[👉 BUY]({deal.buy_url})** · **[Sell on {sell_venue.capitalize()}]({sell_deposit})**"
        ),
        "embeds": [embed],
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(webhook, json=payload)
        response.raise_for_status()

    return True
