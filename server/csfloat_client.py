from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

import httpx

from .config import settings


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after_sec: int = 300) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class CSFloatClient:
    BASE = "https://csfloat.com/api/v1"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.csfloat_api_key
        self._last_request_at = 0.0
        self._min_gap_sec = 2.0  # stay under CSFloat IP + account limits

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = self.api_key
        return headers

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_gap_sec:
            await asyncio.sleep(self._min_gap_sec - elapsed)

    async def get_listings(
        self,
        *,
        limit: int = 40,
        sort_by: str = "most_recent",
        listing_type: str = "buy_now",
        max_price_cents: int | None = None,
        market_hash_name: str | None = None,
        cursor: str | None = None,
        public_feed: bool = False,
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {"limit": min(limit, 40)}

        if public_feed:
            params["min_ref_qty"] = 20
        else:
            params["sort_by"] = sort_by
            params["type"] = listing_type
            if max_price_cents is not None:
                params["max_price"] = max_price_cents
            if market_hash_name:
                params["market_hash_name"] = market_hash_name
            if cursor:
                params["cursor"] = cursor

        await self._throttle()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.BASE}/listings",
                params=params,
                headers=self._headers(),
            )
            self._last_request_at = time.monotonic()

            body_text = response.text or ""
            body_lower = body_text.lower()

            if response.status_code == 429:
                retry = int(response.headers.get("Retry-After") or 120)
                raise RateLimitError(
                    "CSFloat 429 Too Many Requests — switching to light mode.",
                    retry_after_sec=max(retry, 90),
                )

            if response.status_code == 401:
                raise PermissionError(
                    "CSFloat API key invalid. Get one at csfloat.com -> Profile -> Developer."
                )

            if response.status_code == 403:
                # Only treat as rate-limit when the body says so — bare 403 can be auth/filter
                if "too many" in body_lower or "rate" in body_lower:
                    retry = int(response.headers.get("Retry-After") or 120)
                    raise RateLimitError(
                        "CSFloat rate limit — switching to light mode.",
                        retry_after_sec=max(retry, 90),
                    )
                raise PermissionError(f"CSFloat forbidden: {body_text[:180]}")

            response.raise_for_status()
            payload = response.json()

        if isinstance(payload, dict) and "message" in payload:
            msg = str(payload.get("message") or "").lower()
            if "too many" in msg:
                raise RateLimitError(str(payload.get("message")), retry_after_sec=120)

        if isinstance(payload, list):
            return payload, None

        return payload.get("data") or [], payload.get("cursor")

    async def iter_recent_under_budget(
        self,
        max_price_cents: int,
        pages: int = 1,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        seen_ids: set[str] = set()

        for _ in range(pages):
            listings, cursor = await self.get_listings(
                sort_by="most_recent",
                max_price_cents=max_price_cents,
                cursor=cursor,
            )
            if not listings:
                break

            for listing in listings:
                listing_id = str(listing.get("id"))
                if listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)
                yield listing

            if not cursor:
                break

    async def iter_discount_under_budget(
        self,
        max_price_cents: int,
        pages: int = 1,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        seen_ids: set[str] = set()

        for _ in range(pages):
            listings, cursor = await self.get_listings(
                sort_by="highest_discount",
                max_price_cents=max_price_cents,
                cursor=cursor,
            )
            if not listings:
                break

            for listing in listings:
                listing_id = str(listing.get("id"))
                if listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)
                yield listing

            if not cursor:
                break

    async def listings_for_item(self, market_hash_name: str, limit: int = 10) -> list[dict[str, Any]]:
        listings, _ = await self.get_listings(
            market_hash_name=market_hash_name,
            limit=limit,
            max_price_cents=settings.max_buy_cents,
        )
        return listings

    async def iter_auctions_ending_soon(
        self,
        max_price_cents: int,
        pages: int = 1,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor: str | None = None
        seen_ids: set[str] = set()

        for _ in range(pages):
            listings, cursor = await self.get_listings(
                sort_by="expires_soon",
                listing_type="auction",
                max_price_cents=max_price_cents,
                cursor=cursor,
            )
            if not listings:
                break

            for listing in listings:
                listing_id = str(listing.get("id"))
                if listing_id in seen_ids:
                    continue
                seen_ids.add(listing_id)
                yield listing

            if not cursor:
                break


csfloat_client = CSFloatClient()
