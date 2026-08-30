from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .csfloat_client import csfloat_client
from .scanner import scanner

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


class SettingsUpdate(BaseModel):
    csfloat_api_key: str | None = None
    discord_webhook_url: str | None = None
    min_profit_usd: float | None = Field(default=None, ge=0)
    min_discount_pct: float | None = Field(default=None, ge=0, le=99)
    max_buy_usd: float | None = Field(default=None, ge=0.1, le=1000)
    poll_interval_sec: int | None = Field(default=None, ge=5, le=120)


def _persist_env(updates: dict[str, Any]) -> None:
    env_path = ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    mapping = {
        "csfloat_api_key": "CSFLOAT_API_KEY",
        "discord_webhook_url": "DISCORD_WEBHOOK_URL",
        "min_profit_usd": "MIN_PROFIT_USD",
        "min_discount_pct": "MIN_DISCOUNT_PCT",
        "max_buy_usd": "MAX_BUY_USD",
        "poll_interval_sec": "POLL_INTERVAL_SEC",
    }

    existing = {line.split("=", 1)[0]: idx for idx, line in enumerate(lines) if "=" in line and not line.strip().startswith("#")}

    for key, env_key in mapping.items():
        if key not in updates or updates[key] is None:
            continue
        value = str(updates[key])
        new_line = f"{env_key}={value}"
        if env_key in existing:
            lines[existing[env_key]] = new_line
        else:
            lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@asynccontextmanager
async def lifespan(_: FastAPI):
    scanner.start()
    yield
    await scanner.stop()


app = FastAPI(title="CS2 Sniper", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return scanner.status()


@app.get("/api/deals")
async def deals(limit: int = 100) -> dict[str, Any]:
    items = scanner.get_deals(limit=limit)
    big_hits = [d for d in items if d.get("is_big_hit")]
    return {"deals": items, "big_hits": big_hits, "count": len(items)}


@app.get("/api/hot-items")
async def hot_items() -> dict[str, Any]:
    return {"items": scanner.get_hot_items()}


@app.post("/api/scan")
async def scan_now() -> dict[str, Any]:
    deals = await scanner.scan_once()
    return {"found": len(deals), "deals": [d.to_dict() for d in deals]}


@app.post("/api/scanner/start")
async def start_scanner() -> dict[str, str]:
    scanner.start()
    return {"status": "started"}


@app.post("/api/scanner/stop")
async def stop_scanner() -> dict[str, str]:
    await scanner.stop()
    return {"status": "stopped"}


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return {
        "csfloat_api_key_set": bool(settings.csfloat_api_key.strip()),
        "discord_webhook_set": bool(settings.discord_webhook_url.strip()),
        "min_profit_usd": settings.min_profit_usd,
        "min_discount_pct": settings.min_discount_pct,
        "max_buy_usd": settings.max_buy_usd,
        "poll_interval_sec": settings.poll_interval_sec,
    }


@app.post("/api/settings")
async def update_settings(body: SettingsUpdate) -> dict[str, Any]:
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No settings provided")

    _persist_env(updates)

    for key, value in updates.items():
        setattr(settings, key, value)

    if "csfloat_api_key" in updates:
        csfloat_client.api_key = settings.csfloat_api_key

    scanner.state.api_key_configured = bool(settings.csfloat_api_key.strip())
    return {"ok": True, "updated": list(updates.keys())}


@app.get("/api/events")
async def events() -> StreamingResponse:
    async def event_stream():
        last_count = -1
        while True:
            status = scanner.status()
            deals = scanner.get_deals(limit=100)
            payload = {
                "status": status,
                "deals": deals,
                "hot_items": scanner.get_hot_items(),
            }
            count = len(deals)
            if count != last_count or True:
                yield f"data: {json.dumps(payload)}\n\n"
                last_count = count
            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def main() -> None:
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
