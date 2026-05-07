"""
Per-user token usage (estimated) in Azure Blob Storage (gzip JSON).

Schema v2 blob: {sanitized_user_id}/token-usage.json.gz
  - months: { "YYYY-MM": { input_tokens, output_tokens, days: { "YYYY-MM-DD": {...} } } }

Per-user overrides: {sanitized_user_id}/token-limits.json.gz
  - input_limit, output_limit (monthly, UTC month alignment with usage)

Environment:
    CHAT_HISTORY_CONTAINER, AZURE_STORAGE_CONNECTION_STRING
    MONTHLY_INPUT_TOKEN_LIMIT, MONTHLY_OUTPUT_TOKEN_LIMIT
    TOKEN_ESTIMATE_INPUT_OVERHEAD, MAX_ASSUMED_OUTPUT_TOKENS_PER_TURN
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

logger = logging.getLogger(__name__)

CONTAINER_NAME = os.getenv("CHAT_HISTORY_CONTAINER", "chat-history")

MONTHLY_INPUT_TOKEN_LIMIT = int(os.getenv("MONTHLY_INPUT_TOKEN_LIMIT", "1000000"))
MONTHLY_OUTPUT_TOKEN_LIMIT = int(os.getenv("MONTHLY_OUTPUT_TOKEN_LIMIT", "1000000"))
TOKEN_ESTIMATE_INPUT_OVERHEAD = int(os.getenv("TOKEN_ESTIMATE_INPUT_OVERHEAD", "120"))
MAX_ASSUMED_OUTPUT_TOKENS_PER_TURN = int(
    os.getenv("MAX_ASSUMED_OUTPUT_TOKENS_PER_TURN", "8192")
)

SCHEMA_VERSION = 2

_blob_client: Optional[BlobServiceClient] = None
_blob_client_lock = asyncio.Lock()


class TokenLimitExceeded(Exception):
    """Raised when a request would exceed the user's monthly token budget."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def _get_blob_service() -> BlobServiceClient:
    global _blob_client
    async with _blob_client_lock:
        if _blob_client is None:
            conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
            _blob_client = BlobServiceClient.from_connection_string(conn_str)
    return _blob_client


async def get_blob_service() -> BlobServiceClient:
    """Shared aio BlobServiceClient (admin aggregation and tooling)."""
    return await _get_blob_service()


def _safe_user_path(user_id: str) -> str:
    return "".join(
        c if c.isalnum() or c in ("@", ".", "_", "-") else "_" for c in user_id
    )


def usage_blob_path(user_id: str) -> str:
    return f"{_safe_user_path(user_id)}/token-usage.json.gz"


def limits_blob_path(user_id: str) -> str:
    return f"{_safe_user_path(user_id)}/token-limits.json.gz"


def _current_period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 bytes per token for Latin/UTF-8 text)."""
    if not text:
        return 0
    return max(1, len(text.encode("utf-8")) // 4)


def decode_blob_bytes(raw: bytes) -> Dict[str, Any]:
    try:
        data = gzip.decompress(raw)
    except (OSError, EOFError, gzip.BadGzipFile):
        data = raw
    return json.loads(data.decode("utf-8"))


def _encode_payload(record: Dict[str, Any]) -> bytes:
    record["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    return gzip.compress(body)


def normalize_usage_record(data: Dict[str, Any], user_key: str) -> Dict[str, Any]:
    """Migrate legacy flat record to schema v2 (in-memory)."""
    if data.get("schemaVersion") == SCHEMA_VERSION and isinstance(data.get("months"), dict):
        out = dict(data)
        out.setdefault("userId", user_key)
        return out

    period = (data.get("period") or _current_period()).strip()
    in_t = int(data.get("input_tokens", 0))
    out_t = int(data.get("output_tokens", 0))
    return {
        "userId": user_key,
        "schemaVersion": SCHEMA_VERSION,
        "months": {
            period: {
                "input_tokens": in_t,
                "output_tokens": out_t,
                "days": {},
            }
        },
        "lastUpdated": data.get("lastUpdated"),
    }


def _empty_record_v2(user_key: str) -> Dict[str, Any]:
    return {
        "userId": user_key,
        "schemaVersion": SCHEMA_VERSION,
        "months": {},
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
    }


async def _ensure_container(container) -> None:
    try:
        await container.create_container()
    except ResourceExistsError:
        pass
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) == 409:
            return
        raise


async def get_effective_limits_async(user_id: str) -> Dict[str, int]:
    """Monthly limits for this user (override blob or env defaults)."""
    user_key = (user_id or "").strip()
    base_in = MONTHLY_INPUT_TOKEN_LIMIT
    base_out = MONTHLY_OUTPUT_TOKEN_LIMIT
    if not user_key:
        return {"input_limit": base_in, "output_limit": base_out}

    service = await _get_blob_service()
    container = service.get_container_client(CONTAINER_NAME)
    blob = container.get_blob_client(limits_blob_path(user_key))
    try:
        dl = await blob.download_blob()
        raw = await dl.readall()
        ov = decode_blob_bytes(raw)
        in_lim = int(ov.get("input_limit", base_in))
        out_lim = int(ov.get("output_limit", base_out))
        return {
            "input_limit": max(0, in_lim),
            "output_limit": max(0, out_lim),
        }
    except ResourceNotFoundError:
        pass
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
    return {"input_limit": base_in, "output_limit": base_out}


async def set_user_limits_async(
    user_id: str, input_limit: int, output_limit: int
) -> Dict[str, Any]:
    """Persist per-user monthly limits (admin)."""
    user_key = (user_id or "").strip()
    if not user_key:
        raise ValueError("user_id required")

    service = await _get_blob_service()
    container = service.get_container_client(CONTAINER_NAME)
    await _ensure_container(container)
    blob = container.get_blob_client(limits_blob_path(user_key))
    body = {
        "userId": user_key,
        "input_limit": max(0, int(input_limit)),
        "output_limit": max(0, int(output_limit)),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    payload = _encode_payload(body)
    await blob.upload_blob(
        payload,
        overwrite=True,
        content_settings=ContentSettings(
            content_type="application/json",
            content_encoding="gzip",
        ),
    )
    return body


async def get_usage_async(user_id: str) -> Dict[str, Any]:
    """Return current UTC month usage + limits for API/UI (no side effects)."""
    user_key = (user_id or "").strip()
    period = _current_period()
    limits = await get_effective_limits_async(user_key)
    in_lim = limits["input_limit"]
    out_lim = limits["output_limit"]
    combined_limit = in_lim + out_lim

    if not user_key:
        return {
            "period": period,
            "input_tokens": 0,
            "output_tokens": 0,
            "input_limit": in_lim,
            "output_limit": out_lim,
            "input_remaining": in_lim,
            "output_remaining": out_lim,
            "combined_used": 0,
            "combined_limit": combined_limit,
            "combined_fraction": 0.0,
        }

    service = await _get_blob_service()
    container = service.get_container_client(CONTAINER_NAME)
    blob = container.get_blob_client(usage_blob_path(user_key))
    record = _empty_record_v2(user_key)
    try:
        dl = await blob.download_blob()
        raw = await dl.readall()
        loaded = decode_blob_bytes(raw)
        record = normalize_usage_record(loaded, user_key)
    except ResourceNotFoundError:
        pass
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) != 404:
            raise

    month = (record.get("months") or {}).get(period) or {}
    in_u = int(month.get("input_tokens", 0))
    out_u = int(month.get("output_tokens", 0))
    combined_used = in_u + out_u
    frac = float(combined_used) / combined_limit if combined_limit > 0 else 0.0

    return {
        "period": period,
        "input_tokens": in_u,
        "output_tokens": out_u,
        "input_limit": in_lim,
        "output_limit": out_lim,
        "input_remaining": max(0, in_lim - in_u),
        "output_remaining": max(0, out_lim - out_u),
        "combined_used": combined_used,
        "combined_limit": combined_limit,
        "combined_fraction": min(1.0, max(0.0, frac)),
    }


def _precheck_limits(usage: Dict[str, Any], msg: str) -> None:
    in_est = estimate_tokens(msg) + TOKEN_ESTIMATE_INPUT_OVERHEAD
    out_room = usage["output_limit"] - usage["output_tokens"]
    in_room = usage["input_limit"] - usage["input_tokens"]
    if in_room < in_est:
        raise TokenLimitExceeded(
            "Monthly input token budget is exhausted. Resets next calendar month (UTC)."
        )
    if out_room < MAX_ASSUMED_OUTPUT_TOKENS_PER_TURN:
        raise TokenLimitExceeded(
            "Monthly output token budget is too low for another reply. Resets next calendar month (UTC)."
        )


async def record_turn_async(
    user_id: str, user_message: str, assistant_text: str
) -> Dict[str, Any]:
    """
    Add estimated tokens for one turn using optimistic concurrency (ETag retries).
    """
    user_key = (user_id or "").strip()
    if not user_key:
        return await get_usage_async("")

    in_delta = estimate_tokens(user_message) + TOKEN_ESTIMATE_INPUT_OVERHEAD
    out_delta = estimate_tokens(assistant_text)

    for attempt in range(8):
        limits = await get_effective_limits_async(user_key)
        service = await _get_blob_service()
        container = service.get_container_client(CONTAINER_NAME)
        await _ensure_container(container)
        blob = container.get_blob_client(usage_blob_path(user_key))

        etag = None
        record = _empty_record_v2(user_key)
        try:
            props = await blob.get_blob_properties()
            etag = props.etag
            dl = await blob.download_blob()
            raw = await dl.readall()
            loaded = decode_blob_bytes(raw)
            record = normalize_usage_record(loaded, user_key)
        except ResourceNotFoundError:
            pass
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) != 404:
                raise

        period = _current_period()
        months = record.setdefault("months", {})
        month = months.setdefault(
            period,
            {"input_tokens": 0, "output_tokens": 0, "days": {}},
        )
        month.setdefault("days", {})
        dkey = _utc_today()
        day = month["days"].setdefault(
            dkey, {"input_tokens": 0, "output_tokens": 0}
        )

        in_cur = int(month.get("input_tokens", 0))
        out_cur = int(month.get("output_tokens", 0))
        in_room = max(0, limits["input_limit"] - in_cur)
        out_room = max(0, limits["output_limit"] - out_cur)
        apply_in = min(in_delta, in_room)
        apply_out = min(out_delta, out_room)

        new_in = in_cur + apply_in
        new_out = out_cur + apply_out

        if apply_in < in_delta or apply_out < out_delta:
            logger.warning(
                "Token usage clamped | user=%r | requested in=%d out=%d | applied in=%d out=%d",
                user_key,
                in_delta,
                out_delta,
                apply_in,
                apply_out,
            )

        month["input_tokens"] = new_in
        month["output_tokens"] = new_out
        day["input_tokens"] = int(day.get("input_tokens", 0)) + apply_in
        day["output_tokens"] = int(day.get("output_tokens", 0)) + apply_out

        payload = _encode_payload(record)

        try:
            if etag:
                await blob.upload_blob(
                    payload,
                    overwrite=True,
                    content_settings=ContentSettings(
                        content_type="application/json",
                        content_encoding="gzip",
                    ),
                    match_conditions=MatchConditions(if_match=etag),
                )
            else:
                await blob.upload_blob(
                    payload,
                    overwrite=True,
                    content_settings=ContentSettings(
                        content_type="application/json",
                        content_encoding="gzip",
                    ),
                )
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 412 and attempt < 7:
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            raise

        logger.info(
            "Token usage | user=%r | +in=%d +out=%d | total in=%d out=%d | period=%s",
            user_key,
            in_delta,
            out_delta,
            new_in,
            new_out,
            period,
        )
        return await get_usage_async(user_key)

    raise RuntimeError("Could not persist token usage after retries")


async def check_request_allowed_async(user_id: str, message: str) -> None:
    """Call before invoking the model."""
    user_key = (user_id or "").strip()
    if not user_key:
        return
    usage = await get_usage_async(user_key)
    _precheck_limits(usage, message)
