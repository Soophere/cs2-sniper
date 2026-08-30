# CS2 Sniper

Local dashboard + Discord alerts for under-market CSFloat deals.

## Quick start

1. Install Python 3.11+

2. Copy config and fill in your keys:
```powershell
cd C:\Users\Sulaiman\Desktop\cs2-sniper
copy .env.example .env
```

3. Edit `.env`:
- `CSFLOAT_API_KEY` — **required** for scanning items under $10 (csfloat.com → Profile → Developer)
- `DISCORD_WEBHOOK_URL` — your Discord channel webhook

4. Install and run:
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python -m server.main
```

5. Open **http://127.0.0.1:8765** in your browser.

## What it does

- Polls **CSFloat** for new / discounted listings under your max price ($10 default)
- Cross-checks prices + liquidity via **openskin.dev** (free API)
- Filters: min **$2 profit**, min **10% under market**
- Hunts **cases, keys, stickers, skins**
- Shows **fastest sellers** panel (high volume = sells quicker)
- **Big hits** pinned to top (2x min profit or 25%+ discount)
- **Buy now** button opens the CSFloat listing directly
- Sends **Discord embed alerts** when a deal is found

## APIs used

| Source | Purpose | Key needed? |
|--------|---------|-------------|
| CSFloat | Live listings | Yes (for filtered search) |
| openskin.dev | Prices, liquidity, hot items | No |

## Settings (dashboard or .env)

| Setting | Default |
|---------|---------|
| MIN_PROFIT_USD | 2 |
| MIN_DISCOUNT_PCT | 10 |
| MAX_BUY_USD | 10 |
| POLL_INTERVAL_SEC | 12 |

## Notes

- Without a CSFloat API key, only the public feed works (mostly expensive items — not useful for $10 snipes).
- Auto-buy is **not** included — click Buy on dashboard or Discord link. Safer for your account.
- After buying, Steam's ~7-day trade hold still applies before you can resell.
