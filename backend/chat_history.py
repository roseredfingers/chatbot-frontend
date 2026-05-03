"""
Chat history persistence using Azure Blob Storage with gzip compression (async I/O).

Each user's chat history is stored as a single gzip-compressed JSON blob:
    <container>/<user_email>/chat-history.json.gz

Environment variables:
    AZURE_STORAGE_CONNECTION_STRING  – connection string for the storage account
    CHAT_HISTORY_CONTAINER           – blob container name (default: "chat-history")

All blob operations are async so HTTP workers can serve many concurrent users without
blocking the event loop on network I/O.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient

logger = logging.getLogger(__name__)

CONTAINER_NAME = os.getenv("CHAT_HISTORY_CONTAINER", "chat-history")

_blob_client: Optional[BlobServiceClient] = None
_blob_client_lock = asyncio.Lock()


async def _get_blob_service() -> BlobServiceClient:
    """Lazily create one aio BlobServiceClient per worker process (connection pooling)."""
    global _blob_client
    async with _blob_client_lock:
        if _blob_client is None:
            conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
            _blob_client = BlobServiceClient.from_connection_string(conn_str)
    return _blob_client


def _blob_path(user_id: str) -> str:
    safe_id = "".join(
        c if c.isalnum() or c in ("@", ".", "_", "-") else "_" for c in user_id
    )
    return f"{safe_id}/chat-history.json.gz"


# ─────────────────────────────────────────────
# Data helpers (CPU-bound / pure; sync is fine)
# ─────────────────────────────────────────────


def new_conversation_id() -> str:
    return str(uuid4())


def build_empty_history(user_id: str) -> Dict:
    return {
        "userId": user_id,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "conversations": [],
    }


def build_conversation(conv_id: str, title: str = "New Chat") -> Dict:
    return {
        "id": conv_id,
        "title": title,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "messages": [],
    }


def append_message(
    conversation: Dict,
    role: str,
    content: str,
    suggested_questions: Optional[List[str]] = None,
    timestamp_override: Optional[str] = None,
) -> None:
    msg = {
        "role": role,
        "content": content,
        "timestamp": timestamp_override or datetime.now(timezone.utc).isoformat(),
    }
    if suggested_questions:
        msg["suggestedQuestions"] = suggested_questions
    conversation["messages"].append(msg)
    conversation["lastUpdated"] = datetime.now(timezone.utc).isoformat()


def _decode_history_bytes(blob_path: str, data: bytes) -> Dict:
    if not data:
        raise ValueError("empty blob")
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError, gzip.BadGzipFile):
        logger.warning(
            "Blob %s is not valid gzip; trying raw JSON (check upload / Content-Encoding).",
            blob_path,
        )
        raw = data
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in history blob %s: %s", blob_path, exc)
        raise


def _encode_history_gzip(history: Dict) -> bytes:
    history["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    raw = json.dumps(history, ensure_ascii=False).encode("utf-8")
    return gzip.compress(raw)


# ─────────────────────────────────────────────
# Async blob read / write
# ─────────────────────────────────────────────


async def _ensure_container(container) -> None:
    try:
        await container.create_container()
    except ResourceExistsError:
        pass
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) == 409:
            return
        raise


async def load_history_async(user_id: str) -> Dict:
    user_key = (user_id or "").strip()
    if not user_key:
        logger.info("Empty user_id for load_history; returning empty shell.")
        return build_empty_history(user_id or "")

    blob_path = _blob_path(user_key)
    service = await _get_blob_service()
    container = service.get_container_client(CONTAINER_NAME)
    blob = container.get_blob_client(blob_path)
    try:
        logger.info(
            "Loading chat history | container=%s | blob=%s | user_id=%r",
            CONTAINER_NAME,
            blob_path,
            user_key,
        )
        downloader = await blob.download_blob()
        payload = await downloader.readall()
        return _decode_history_bytes(blob_path, payload)
    except ResourceNotFoundError:
        logger.info(
            "Blob not found (new user or path mismatch) | container=%s | blob=%s | user_id=%r",
            CONTAINER_NAME,
            blob_path,
            user_key,
        )
        return build_empty_history(user_key)
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) == 404:
            logger.info(
                "Blob not found (HTTP 404) | container=%s | blob=%s | user_id=%r",
                CONTAINER_NAME,
                blob_path,
                user_key,
            )
            return build_empty_history(user_key)
        raise
    except KeyError as exc:
        logger.error("AZURE_STORAGE_CONNECTION_STRING is not set: %s", exc)
        raise
    except Exception:
        logger.exception(
            "Failed to load history | container=%s | blob=%s | user_id=%r",
            CONTAINER_NAME,
            blob_path,
            user_key,
        )
        raise


async def save_history_async(user_id: str, history: Dict) -> None:
    user_key = (user_id or "").strip()
    if not user_key:
        raise ValueError("user_id required for save_history")

    compressed = _encode_history_gzip(history)
    service = await _get_blob_service()
    container = service.get_container_client(CONTAINER_NAME)
    await _ensure_container(container)
    blob = container.get_blob_client(_blob_path(user_key))
    await blob.upload_blob(
        compressed,
        overwrite=True,
        content_settings=ContentSettings(
            content_type="application/json",
            content_encoding="gzip",
        ),
    )
    logger.info(
        "Saved history for %s (%d bytes compressed).", user_key, len(compressed)
    )


async def get_user_conversations_async(user_id: str) -> List[Dict]:
    history = await load_history_async(user_id)
    return history.get("conversations", [])


async def save_user_conversations_async(
    user_id: str, conversations: List[Dict]
) -> None:
    uid = (user_id or "").strip()
    history = {
        "userId": uid,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "conversations": conversations,
    }
    # lastUpdated set again in save_history_async path via _encode_history_gzip
    await save_history_async(uid, history)


async def save_single_conversation_async(user_id: str, conversation: Dict) -> None:
    history = await load_history_async(user_id)
    convs = history.get("conversations", [])

    existing_idx = next(
        (i for i, c in enumerate(convs) if c["id"] == conversation["id"]),
        None,
    )

    if existing_idx is not None:
        convs[existing_idx] = conversation
    else:
        convs.insert(0, conversation)

    history["conversations"] = convs
    await save_history_async((user_id or "").strip(), history)


async def delete_conversation_async(user_id: str, conversation_id: str) -> bool:
    history = await load_history_async(user_id)
    convs = history.get("conversations", [])
    filtered = [c for c in convs if c["id"] != conversation_id]
    if len(filtered) == len(convs):
        return False
    history["conversations"] = filtered
    await save_history_async((user_id or "").strip(), history)
    return True


# ─────────────────────────────────────────────
# Sync bridge (only for blocking CLIs / tests — not inside async HTTP handlers)
# ─────────────────────────────────────────────


def load_history(user_id: str) -> Dict:
    return asyncio.run(load_history_async(user_id))


def save_history(user_id: str, history: Dict) -> None:
    asyncio.run(save_history_async(user_id, history))


def get_user_conversations(user_id: str) -> List[Dict]:
    return asyncio.run(get_user_conversations_async(user_id))


def save_user_conversations(user_id: str, conversations: List[Dict]) -> None:
    asyncio.run(save_user_conversations_async(user_id, conversations))


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    return asyncio.run(delete_conversation_async(user_id, conversation_id))
