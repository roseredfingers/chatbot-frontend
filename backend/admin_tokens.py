"""
Admin-only aggregation of token usage across all users (blob scan).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, DefaultDict, Dict, List, Set

import token_usage
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

logger = logging.getLogger(__name__)


def _user_id_from_blob_path(blob_name: str) -> str:
    if "/" not in blob_name:
        return ""
    return blob_name.split("/", 1)[0]


def _is_token_usage_blob(name: str) -> bool:
    return name.endswith("/token-usage.json.gz")


def _is_chat_history_blob(name: str) -> bool:
    return name.endswith("/chat-history.json.gz")


async def list_known_user_ids_async() -> List[str]:
    """Distinct user folder IDs from token-usage and chat-history blobs."""
    service = await token_usage.get_blob_service()
    container = service.get_container_client(token_usage.CONTAINER_NAME)
    seen: Set[str] = set()
    try:
        async for b in container.list_blobs():
            name = b.name
            if _is_token_usage_blob(name) or _is_chat_history_blob(name):
                uid = _user_id_from_blob_path(name)
                if uid:
                    seen.add(uid)
    except Exception:
        logger.exception("list_known_user_ids failed")
        raise
    return sorted(seen)


async def _load_usage_for_aggregate(container, blob_name: str) -> Dict[str, Any] | None:
    uid = _user_id_from_blob_path(blob_name)
    if not uid:
        return None
    blob = container.get_blob_client(blob_name)
    try:
        dl = await blob.download_blob()
        raw = await dl.readall()
        data = token_usage.decode_blob_bytes(raw)
        return token_usage.normalize_usage_record(data, uid)
    except ResourceNotFoundError:
        return None
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) == 404:
            return None
        raise


async def aggregate_year_async(year: int) -> Dict[str, Any]:
    """
    Sum input/output tokens across all users for a UTC calendar year.
    Returns daily map, monthly map, totals, and active user counts per bucket.
    """
    yprefix = f"{year:04d}-"
    service = await token_usage.get_blob_service()
    container = service.get_container_client(token_usage.CONTAINER_NAME)

    daily_in: DefaultDict[str, int] = defaultdict(int)
    daily_out: DefaultDict[str, int] = defaultdict(int)
    daily_users: DefaultDict[str, Set[str]] = defaultdict(set)

    monthly_in: DefaultDict[str, int] = defaultdict(int)
    monthly_out: DefaultDict[str, int] = defaultdict(int)
    monthly_users: DefaultDict[str, Set[str]] = defaultdict(set)

    users_with_tokens = 0
    year_in = year_out = 0

    sem = asyncio.Semaphore(24)

    async def consume_blob(name: str) -> None:
        nonlocal users_with_tokens, year_in, year_out
        if not _is_token_usage_blob(name):
            return
        async with sem:
            rec = await _load_usage_for_aggregate(container, name)
        if not rec:
            return
        uid = rec.get("userId") or _user_id_from_blob_path(name)
        months = rec.get("months") or {}
        touched = False
        for mk, mdata in months.items():
            if not isinstance(mk, str) or not mk.startswith(yprefix):
                continue
            touched = True
            mi = int(mdata.get("input_tokens", 0))
            mo = int(mdata.get("output_tokens", 0))
            monthly_in[mk] += mi
            monthly_out[mk] += mo
            monthly_users[mk].add(uid)
            year_in += mi
            year_out += mo

            days = mdata.get("days") or {}
            if isinstance(days, dict):
                for dk, dd in days.items():
                    if not isinstance(dk, str) or not dk.startswith(str(year)):
                        continue
                    di = int(dd.get("input_tokens", 0))
                    do = int(dd.get("output_tokens", 0))
                    if di == 0 and do == 0:
                        continue
                    daily_in[dk] += di
                    daily_out[dk] += do
                    daily_users[dk].add(uid)
        if touched:
            users_with_tokens += 1

    blob_names = [b.name async for b in container.list_blobs()]
    token_blob_names = [n for n in blob_names if _is_token_usage_blob(n)]
    if token_blob_names:
        await asyncio.gather(*(consume_blob(n) for n in token_blob_names))

    def serial_daily() -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        all_days = set(daily_in.keys()) | set(daily_out.keys()) | set(daily_users.keys())
        for d in sorted(all_days):
            out[d] = {
                "input_tokens": daily_in.get(d, 0),
                "output_tokens": daily_out.get(d, 0),
                "users": len(daily_users.get(d, set())),
            }
        return out

    def serial_monthly() -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        all_m = set(monthly_in.keys()) | set(monthly_out.keys()) | set(monthly_users.keys())
        for m in sorted(all_m):
            out[m] = {
                "input_tokens": monthly_in.get(m, 0),
                "output_tokens": monthly_out.get(m, 0),
                "users": len(monthly_users.get(m, set())),
            }
        return out

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    cur_month = f"{now.year:04d}-{now.month:02d}"

    daily_map = serial_daily()
    monthly_map = serial_monthly()

    if year == now.year:
        tblock = daily_map.get(today, {})
        today_in = int(tblock.get("input_tokens", 0))
        today_out = int(tblock.get("output_tokens", 0))
        today_users = int(tblock.get("users", 0))
    else:
        today_in = today_out = today_users = 0

    month_block = monthly_map.get(cur_month, {}) if year == now.year else {}
    month_in = month_block.get("input_tokens", 0)
    month_out = month_block.get("output_tokens", 0)

    return {
        "year": year,
        "generated_at_utc": now.isoformat(),
        "users_with_usage_blobs": users_with_tokens,
        "year_totals": {
            "input_tokens": year_in,
            "output_tokens": year_out,
            "combined": year_in + year_out,
        },
        "current_utc_month": cur_month,
        "current_month_totals": {
            "input_tokens": month_in,
            "output_tokens": month_out,
            "combined": month_in + month_out,
            "users": month_block.get("users", 0),
        },
        "today_utc": today,
        "today_totals": {
            "input_tokens": today_in,
            "output_tokens": today_out,
            "combined": today_in + today_out,
            "users": today_users,
        },
        "daily": daily_map,
        "monthly": monthly_map,
    }


async def get_user_limits_snapshot_async(user_id: str) -> Dict[str, Any]:
    base_in = token_usage.MONTHLY_INPUT_TOKEN_LIMIT
    base_out = token_usage.MONTHLY_OUTPUT_TOKEN_LIMIT
    eff = await token_usage.get_effective_limits_async(user_id)
    return {
        "user_id": user_id,
        "input_limit": eff["input_limit"],
        "output_limit": eff["output_limit"],
        "default_input_limit": base_in,
        "default_output_limit": base_out,
        "has_custom_limits": (
            eff["input_limit"] != base_in or eff["output_limit"] != base_out
        ),
    }


async def list_users_with_limits_async() -> List[Dict[str, Any]]:
    ids = await list_known_user_ids_async()
    if not ids:
        return []

    sem = asyncio.Semaphore(40)

    async def row(uid: str) -> Dict[str, Any]:
        async with sem:
            return await get_user_limits_snapshot_async(uid)

    rows = await asyncio.gather(*(row(i) for i in ids))
    return sorted(rows, key=lambda r: (r.get("user_id") or "").lower())
