# CS2 Sniper

Local dashboard + Discord alerts for under-market CSFloat deals.

## Secrets (important)

Your real API key and Discord webhook live only in **`.env`**.
That file is **gitignored** and must never be uploaded to GitHub.

- Safe to commit: `.env.example` (placeholders only)
- Never commit: `.env`, anything with real keys/webhooks

If a key ever leaks, rotate it on CSFloat / Discord immediately.

## Quick start

1. Install Python 3.11+

2. Copy config and fill in your keys:
```powershell
cd C:\Users\Sulaiman\Desktop\cs2-sniper
copy .env.example .env
```

3. Edit `.env` (local only):
- `CSFLOAT_API_KEY` — **required** (csfloat.com → Profile → Developer)
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
- Cross-checks prices + liquidity via free public market dumps + OpenSkin
- Filters: min **$1 profit** (adjustable), min **10% under market**, 3+ markets must agree
- Hunts **cases, keys, stickers, skins** — cases preferred for small budgets
- Shows **liquid items** and a **market pulse** panel (Steam news + trader talk + flip tips)
- **Buy / Sell** buttons with clear price difference
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
