"""
Chat history persistence using Azure Blob Storage with gzip compression.

Each user's chat history is stored as a single gzip-compressed JSON blob:
    <container>/<user_email>/chat-history.json.gz

Environment variables:
    AZURE_STORAGE_CONNECTION_STRING  – connection string for the storage account
    CHAT_HISTORY_CONTAINER           – blob container name (default: "chat-history")
"""

import gzip
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)

CONTAINER_NAME = os.getenv("CHAT_HISTORY_CONTAINER", "chat-history")


def _get_blob_service() -> BlobServiceClient:
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    return BlobServiceClient.from_connection_string(conn_str)


def _blob_path(user_id: str) -> str:
    safe_id = "".join(c if c.isalnum() or c in ("@", ".", "_", "-") else "_" for c in user_id)
    return f"{safe_id}/chat-history.json.gz"


# ─────────────────────────────────────────────
# Data helpers
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
) -> None:
    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if suggested_questions:
        msg["suggestedQuestions"] = suggested_questions
    conversation["messages"].append(msg)
    conversation["lastUpdated"] = datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────
# Blob read / write (gzip compressed)
# ─────────────────────────────────────────────

def load_history(user_id: str) -> Dict:
    try:
        service = _get_blob_service()
        container = service.get_container_client(CONTAINER_NAME)
        blob = container.get_blob_client(_blob_path(user_id))
        compressed = blob.download_blob().readall()
        raw = gzip.decompress(compressed)
        return json.loads(raw)
    except Exception:
        logger.info("No existing history for %s, returning empty.", user_id)
        return build_empty_history(user_id)


def save_history(user_id: str, history: Dict) -> None:
    history["lastUpdated"] = datetime.now(timezone.utc).isoformat()
    raw = json.dumps(history, ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw)

    service = _get_blob_service()
    container = service.get_container_client(CONTAINER_NAME)

    try:
        container.create_container()
    except Exception:
        pass

    blob = container.get_blob_client(_blob_path(user_id))
    blob.upload_blob(
        compressed,
        overwrite=True,
        content_settings=ContentSettings(
            content_type="application/json",
            content_encoding="gzip",
        ),
    )
    logger.info("Saved history for %s (%d bytes compressed).", user_id, len(compressed))


# ─────────────────────────────────────────────
# High-level operations (called from routes)
# ─────────────────────────────────────────────

def get_user_conversations(user_id: str) -> List[Dict]:
    history = load_history(user_id)
    return history.get("conversations", [])


def save_user_conversations(user_id: str, conversations: List[Dict]) -> None:
    history = {
        "userId": user_id,
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "conversations": conversations,
    }
    save_history(user_id, history)


def save_single_conversation(user_id: str, conversation: Dict) -> None:
    """Upsert a single conversation into the user's history."""
    history = load_history(user_id)
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
    save_history(user_id, history)


def delete_conversation(user_id: str, conversation_id: str) -> bool:
    history = load_history(user_id)
    convs = history.get("conversations", [])
    filtered = [c for c in convs if c["id"] != conversation_id]
    if len(filtered) == len(convs):
        return False
    history["conversations"] = filtered
    save_history(user_id, history)
    return True
