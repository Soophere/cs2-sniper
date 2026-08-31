"""Health-check the public price feeds we rely on, plus known alternatives.

Run this when deal flow dries up to see whether a feed died upstream.
Reports HTTP status and top-level shape only.

Findings as of last audit:
  skinport      LIVE  - real buyable asks + suggested_price (our USD anchor)
  csgomarket    LIVE  - reference prices
  waxpeer       LIVE  - buyable asks
  skincash      LIVE  - cashout quotes (reference only)
  lootfarm      LIVE  - skin-swap trade values (reference only)
  bitskins      LIVE  - suggested_price, fixed-point units, self-calibrated
  csgobackpack  DEAD  - returns all zeros for every item
  csgotrader    DEAD  - serves an HTML page instead of JSON
  pricempire / csgoskins / steamapis  - require a paid API key
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cs2-sniper/1.0",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Encoding": "gzip",
}

CANDIDATES = {
    # In use
    "csgomarket": "https://market.csgo.com/api/v2/prices/USD.json",
    "waxpeer": "https://api.waxpeer.com/v1/prices?game=csgo",
    "skincash": "https://api.skincash.gg/v1/prices",
    "lootfarm": "https://loot.farm/fullprice.json",
    "bitskins": "https://api.bitskins.com/market/skin/730",
    # Known dead — kept so we notice if they come back
    "csgobackpack (dead)": "https://csgobackpack.net/api/GetItemsList/v2/?currency=USD",
    "csgotrader (dead)": "https://prices.csgotrader.app/latest/prices_v6.json",
}

# Skinport is deliberately excluded: it allows only 8 calls per 5 minutes and the
# running server needs that quota.


def describe(raw: bytes) -> str:
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return f"NOT JSON -> {raw[:80].decode('utf-8', 'replace')!r}"

    if isinstance(data, list):
        out = f"list[{len(data)}]"
        if data and isinstance(data[0], dict):
            out += f" fields={list(data[0])[:8]}"
        return out
    if isinstance(data, dict):
        return f"dict top-keys={list(data)[:6]}"
    return type(data).__name__


def main() -> None:
    for label, url in CANDIDATES.items():
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
            print(f"\n[{label}] {resp.status}  {len(raw) / 1_000_000:.2f}MB")
            print(f"   {describe(raw)}")
        except urllib.error.HTTPError as exc:
            print(f"\n[{label}] HTTP {exc.code} {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            print(f"\n[{label}] FAILED {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
