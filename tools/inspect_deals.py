"""Print the current deal feed with its validation signals (debug helper)."""

from __future__ import annotations

import json
import urllib.request

URL = "http://127.0.0.1:8765/api/deals"


def main() -> None:
    with urllib.request.urlopen(URL, timeout=20) as resp:
        payload = json.load(resp)

    deals = payload.get("deals", [])
    print(f"deals kept: {len(deals)}")
    for deal in deals[:15]:
        extra = deal.get("extra") or {}
        print(
            f"  {deal['name'][:40]:42} "
            f"buy {deal['buy_price_usd']:>7.2f} "
            f"sell {deal['sell_target_usd']:>7.2f} "
            f"profit {deal['net_profit_usd']:>6.2f} "
            f"disc {deal['discount_pct']:>5.1f}% "
            f"src={deal.get('source')} "
            f"agree={extra.get('sources_agree')} "
            f"qty={extra.get('sell_orders')}"
        )


if __name__ == "__main__":
    main()
