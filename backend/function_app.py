"""
Azure Functions v2 Python app.

HTTP triggers (under /api/... per host.json):
  GET  /api/ping
  POST /api/messages                  – Teams Bot Framework
  POST /api/nuvoco_frontend           – Web/frontend chat
  GET  /api/chat_history              – Load all conversations for a user
  POST /api/chat_history              – Bulk-save conversations
  POST /api/chat_history_delete       – Delete a single conversation
  POST /api/prime_conversation        – Inject prior history into RAG thread (first open)
  POST /api/append_exchange           – Persist a single user/assistant exchange

Blob storage and CPU-heavy LangGraph work are offloaded so async routes stay non-blocking
under concurrent load (aio blob I/O + asyncio.to_thread for sync graph / LLM code).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import parse_qs, unquote, urlparse

import azure.functions as func
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    ConversationState,
    MemoryStorage,
    TurnContext,
)
from botbuilder.schema import Activity

import chat_history
from langgraph_chain import ThreadedChatManager

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Singletons (warm across invocations)
# ─────────────────────────────────────────────
chat_manager = ThreadedChatManager()

SETTINGS = BotFrameworkAdapterSettings(
    app_id=os.environ.get("MICROSOFT_APP_ID", ""),
    app_password=os.environ.get("MICROSOFT_APP_PASSWORD", ""),
    channel_auth_tenant=os.environ.get("MICROSOFT_APP_TENANT_ID", ""),
)
MEMORY = MemoryStorage()
CONVERSATION_STATE = ConversationState(MEMORY)

try:
    from teams_bot.my_bot import MyBot

    BOT = MyBot(CONVERSATION_STATE)
except ImportError:
    BOT = None

ADAPTER = BotFrameworkAdapter(SETTINGS)


async def _on_error(context: TurnContext, error: Exception) -> None:
    logger.exception("BOT ERROR: %s", error)
    await context.send_activity(
        "Sorry, something went wrong while processing your request."
    )


ADAPTER.on_turn_error = _on_error

# ─────────────────────────────────────────────
# Function App
# ─────────────────────────────────────────────
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _json_response(data, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
        headers={"X-Content-Type-Options": "nosniff"},
    )


def _error_response(message: str, status_code: int = 500) -> func.HttpResponse:
    return _json_response({"error": message}, status_code)


def _first_query_value(req: func.HttpRequest, name: str) -> str:
    try:
        raw = req.params.get(name) if req.params else None
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    except Exception:
        pass
    try:
        pairs = parse_qs(urlparse(req.url or "").query, keep_blank_values=True)
        vals = pairs.get(name) or pairs.get(name.lower())
        if vals and vals[0] is not None:
            return unquote(vals[0]).strip()
    except Exception:
        pass
    return ""


@app.route(route="ping", methods=["GET"])
async def ping(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        "ok",
        status_code=200,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@app.route(route="messages", methods=["POST"])
async def messages(req: func.HttpRequest) -> func.HttpResponse:
    if BOT is None:
        return _error_response("Teams bot not configured.", 503)
    try:
        body = req.get_json()
        activity = Activity().deserialize(body)
        auth_header = req.headers.get("Authorization", "")

        logger.info(
            "Incoming | type=%s | channel=%s | text=%s",
            activity.type,
            activity.channel_id,
            activity.text,
        )

        response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)

        if response:
            return func.HttpResponse(
                body=json.dumps(response.body) if response.body else "",
                status_code=response.status,
                mimetype="application/json",
                headers={"X-Content-Type-Options": "nosniff"},
            )
        return func.HttpResponse(
            status_code=200,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    except Exception as exc:
        logger.exception("messages endpoint failed")
        return _error_response(str(exc))


@app.route(route="nuvoco_frontend", methods=["POST"])
async def nuvoco_frontend(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        message = body.get("text", body.get("message", ""))
        channel_id = body.get("channelId", body.get("session_id", "default"))

        logger.info("Frontend | channel=%s | text=%s", channel_id, message)

        result = await asyncio.to_thread(chat_manager.chat, message, channel_id)

        if result:
            answer, suggested_questions, status_code = result
            return _json_response({
                "answer": answer,
                "suggested_questions": suggested_questions or [],
                "status": status_code,
            })

        return _json_response({
            "answer": "No response generated.",
            "suggested_questions": [],
            "status": 200,
        })

    except Exception as exc:
        logger.exception("nuvoco_frontend endpoint failed")
        return _error_response(str(exc))


@app.route(route="prime_conversation", methods=["POST"])
async def prime_conversation(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        session_id = body.get("session_id", "")
        messages_payload = body.get("messages", [])

        if not session_id:
            return _error_response("session_id required", 400)

        await asyncio.to_thread(
            chat_manager.inject_history, session_id, messages_payload
        )

        logger.info(
            "Primed conversation %s with %d messages.", session_id, len(messages_payload)
        )
        return _json_response({"status": "ok", "primed": len(messages_payload)})

    except Exception as exc:
        logger.exception("prime_conversation endpoint failed")
        return _error_response(str(exc))


@app.route(route="append_exchange", methods=["POST"])
async def append_exchange(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        user_id = body.get("user_id", "")
        conversation_id = body.get("conversation_id", "")
        conversation_title = body.get("conversation_title", "New Chat")
        user_msg = body.get("user_message", {})
        assistant_msg = body.get("assistant_message", {})

        if not user_id or not conversation_id:
            return _error_response("user_id and conversation_id required", 400)

        history = await chat_history.load_history_async(user_id)
        convs = history.get("conversations", [])

        existing = next((c for c in convs if c["id"] == conversation_id), None)
        if existing is None:
            existing = chat_history.build_conversation(
                conversation_id, conversation_title
            )
            convs.insert(0, existing)

        existing["title"] = conversation_title

        if user_msg.get("content"):
            chat_history.append_message(
                existing,
                role="user",
                content=user_msg["content"],
                timestamp_override=user_msg.get("timestamp"),
            )

        if assistant_msg.get("content"):
            chat_history.append_message(
                existing,
                role="assistant",
                content=assistant_msg["content"],
                suggested_questions=assistant_msg.get("suggestedQuestions"),
                timestamp_override=assistant_msg.get("timestamp"),
            )

        history["conversations"] = convs
        await chat_history.save_history_async(user_id, history)

        return _json_response({"status": "ok"})

    except Exception as exc:
        logger.exception("append_exchange endpoint failed")
        return _error_response(str(exc))


@app.route(route="chat_history", methods=["GET"])
async def get_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        user_id = _first_query_value(req, "user_id")
        if not user_id:
            return _error_response("user_id query parameter required", 400)

        conversations = await chat_history.get_user_conversations_async(user_id)
        return _json_response(conversations)

    except Exception as exc:
        logger.exception("get_history failed")
        return _error_response(str(exc))


@app.route(route="chat_history", methods=["POST"])
async def save_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        user_id = body.get("user_id", "")
        conversations = body.get("conversations", [])

        if not user_id:
            return _error_response("user_id required", 400)

        await chat_history.save_user_conversations_async(user_id, conversations)
        return _json_response({"status": "ok"})

    except Exception as exc:
        logger.exception("save_history failed")
        return _error_response(str(exc))


@app.route(route="chat_history_delete", methods=["POST"])
async def delete_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        user_id = body.get("user_id", "")
        conversation_id = body.get("conversation_id", "")

        if not user_id or not conversation_id:
            return _error_response("user_id and conversation_id required", 400)

        deleted = await chat_history.delete_conversation_async(
            user_id, conversation_id
        )
        return _json_response({"deleted": deleted})

    except Exception as exc:
        logger.exception("delete_history failed")
        return _error_response(str(exc))
