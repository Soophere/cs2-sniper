"""Market pulse: Steam news + trader talk + timeless flip tips.

No API keys. Public endpoints only. Failures degrade to tips-only so the
sniper dashboard never depends on this panel.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from typing import Any

import httpx

# Timeless advice for a $10 flipper — not "news", just what experienced
# traders actually repeat. Shown even when feeds are down.
CURATED_TIPS: list[dict[str, str]] = [
    {
        "kind": "tip",
        "source": "Flip rule",
        "title": "Cases & keys beat stickers when your budget is small",
        "summary": (
            "High volume means you can sell the same day. A $1.50 case flip "
            "with 10k listings is safer than a $3 sticker with 12 copies."
        ),
        "url": "",
    },
    {
        "kind": "tip",
        "source": "Flip rule",
        "title": "Never pay a sticker premium you cannot recover",
        "summary": (
            "On cheap skins, stickers almost never return their cost. Buy the "
            "bare skin value — treat sticker markup as a donation."
        ),
        "url": "",
    },
    {
        "kind": "tip",
        "source": "Flip rule",
        "title": "Check the sell site before you buy",
        "summary": (
            "Cross-market gaps look free until you open CSFloat and see the "
            "real ask. Ten seconds of verification beats a losing trade."
        ),
        "url": "",
    },
    {
        "kind": "tip",
        "source": "Flip rule",
        "title": "Trade hold kills weak edges",
        "summary": (
            "Steam's 7-day hold means price can drift. Demand enough discount "
            "to survive a few percent of movement after fees."
        ),
        "url": "",
    },
]

_TAG_RE = re.compile(r"<[^>]+>")
_CACHE_TTL = 900  # 15 minutes
_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _clean(text: str, limit: int = 220) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


async def _steam_news(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Official CS2 / CS:GO Steam news (app 730)."""
    try:
        resp = await client.get(
            "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/",
            params={"appid": 730, "count": 6, "maxlength": 280, "format": "json"},
        )
        resp.raise_for_status()
        items = (((resp.json() or {}).get("appnews") or {}).get("newsitems")) or []
        out: list[dict[str, Any]] = []
        for item in items[:5]:
            title = _clean(str(item.get("title") or ""), 120)
            if not title:
                continue
            out.append(
                {
                    "kind": "news",
                    "source": "Steam",
                    "title": title,
                    "summary": _clean(str(item.get("contents") or ""), 200),
                    "url": str(item.get("url") or ""),
                    "published": int(item.get("date") or 0) or None,
                }
            )
        return out
    except Exception:  # noqa: BLE001
        return []


async def _reddit_pulse(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Trader talk from public Reddit JSON (no key). Soft-fails if blocked."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; cs2-sniper/1.0; local dashboard; "
            "contact: local-only)"
        ),
        "Accept": "application/json",
    }
    for url in (
        "https://www.reddit.com/r/csgomarketforum/hot.json",
        "https://www.reddit.com/r/GlobalOffensiveTrade/new.json",
    ):
        try:
            resp = await client.get(
                url,
                params={"limit": 12, "raw_json": 1},
                headers=headers,
            )
            if resp.status_code >= 400:
                continue
            children = (((resp.json() or {}).get("data") or {}).get("children")) or []
            out: list[dict[str, Any]] = []
            skip = ("megathread", "daily", "weekly", "mod post", "[h]", "[s]")
            for child in children:
                data = child.get("data") or {}
                title = _clean(str(data.get("title") or ""), 140)
                if not title:
                    continue
                lower = title.lower()
                if any(s in lower for s in skip):
                    continue
                if data.get("over_18") or data.get("stickied"):
                    continue
                permalink = str(data.get("permalink") or "")
                link = (
                    f"https://www.reddit.com{permalink}"
                    if permalink
                    else str(data.get("url") or "")
                )
                flair = _clean(str(data.get("link_flair_text") or "Discussion"), 40)
                sub = str(data.get("subreddit") or "reddit")
                out.append(
                    {
                        "kind": "talk",
                        "source": f"r/{sub} · {flair}",
                        "title": title,
                        "summary": _clean(str(data.get("selftext") or ""), 180)
                        or "Open the thread for trader discussion.",
                        "url": link,
                        "published": int(data.get("created_utc") or 0) or None,
                    }
                )
                if len(out) >= 6:
                    break
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return []


async def _steam_discussions(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fallback trader-ish chatter from Steam CS2 discussions RSS."""
    try:
        resp = await client.get(
            "https://steamcommunity.com/app/730/discussions/",
            params={"rss": "1"},
            headers={"User-Agent": "cs2-sniper/1.0"},
        )
        if resp.status_code >= 400:
            return []
        text = resp.text
        items = re.findall(
            r"<item>\s*<title><!\[CDATA\[(.*?)\]\]></title>.*?<link>(.*?)</link>"
            r"(?:.*?<description><!\[CDATA\[(.*?)\]\]></description>)?",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        out: list[dict[str, Any]] = []
        for title, link, desc in items[:6]:
            clean_title = _clean(title, 140)
            if not clean_title:
                continue
            out.append(
                {
                    "kind": "talk",
                    "source": "Steam discussions",
                    "title": clean_title,
                    "summary": _clean(desc or "", 180)
                    or "Community discussion on Steam.",
                    "url": _clean(link, 300),
                    "published": None,
                }
            )
        return out
    except Exception:  # noqa: BLE001
        return []


async def get_market_pulse(force: bool = False) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and _cache["payload"] is not None
        and now - float(_cache["at"]) < _CACHE_TTL
    ):
        return _cache["payload"]

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        news, talk = await asyncio.gather(
            _steam_news(client),
            _reddit_pulse(client),
        )
        if not talk:
            talk = await _steam_discussions(client)

    payload = {
        "updated_at": int(now),
        "tips": CURATED_TIPS,
        "news": news,
        "talk": talk,
        "links": [
            {
                "label": "Steam CS2 news",
                "url": "https://store.steampowered.com/news/app/730",
            },
            {
                "label": "r/csgomarketforum",
                "url": "https://www.reddit.com/r/csgomarketforum/",
            },
            {
                "label": "CSFloat blog",
                "url": "https://blog.csfloat.com/",
            },
            {
                "label": "Skinport blog",
                "url": "https://skinport.com/blog",
            },
        ],
    }
    _cache["at"] = now
    _cache["payload"] = payload
    return payload
