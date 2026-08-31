from __future__ import annotations

from urllib.parse import quote

from .config import settings


def csfloat_item_url(listing_id: str | None) -> str:
    if listing_id:
        return f"https://csfloat.com/item/{listing_id}"
    return "https://csfloat.com/search"


def csfloat_search_url(name: str) -> str:
    return f"https://csfloat.com/search?market_hash_name={quote(name)}"


def skinport_sell_url() -> str:
    return "https://skinport.com/sell"


def csfloat_sell_url() -> str:
    return "https://csfloat.com/profile"


def sell_deposit_url(venue: str | None = None) -> str:
    src = (venue or settings.sell_venue or "csfloat").lower().strip()
    if src == "skinport":
        return skinport_sell_url()
    if src == "waxpeer":
        return "https://waxpeer.com/profile"
    return csfloat_sell_url()


def skinport_market_url(name: str) -> str:
    return f"https://skinport.com/market?search={quote(name)}"


def market_sell_url(name: str, venue: str | None = None) -> str:
    src = (venue or settings.sell_venue or "csfloat").lower().strip()
    if src == "skinport":
        return skinport_market_url(name)
    if src == "csfloat":
        return csfloat_search_url(name)
    if src == "waxpeer":
        return f"https://waxpeer.com/{quote(name)}"
    return csfloat_search_url(name)


def market_buy_url(source: str, name: str, listing_id: str | None = None) -> str:
    """Direct-ish buy / search URL for a market (best effort)."""
    src = (source or "").lower().strip()
    q = quote(name)

    if src == "csfloat":
        return csfloat_item_url(listing_id) if listing_id else csfloat_search_url(name)
    if src == "skinport":
        return skinport_market_url(name)
    if src == "steam":
        return f"https://steamcommunity.com/market/listings/730/{q}"
    if src == "csgomarket":
        return f"https://market.csgo.com/en/{q}"
    if src == "waxpeer":
        return f"https://waxpeer.com/{q}"
    if src == "lisskins":
        return f"https://lis-skins.com/market/csgo/?query={q}"
    if src == "skincash":
        return f"https://skincash.gg/?search={q}"
    if src == "buff":
        return f"https://buff.163.com/market/csgo#tab=selling&search={q}"
    if src == "youpin":
        return f"https://youpin.gg/"
    if src == "lootfarm":
        return "https://loot.farm/"
    if src == "csgotrader":
        return f"https://csgotrader.app/prices?market_hash_name={q}"
    if src == "swapgg":
        return f"https://swap.gg/"
    return csfloat_search_url(name)
