"""
Azure Functions v2 Python app.

HTTP triggers (under /api/... per host.json):
  GET  /api/ping
  POST /api/messages                  – Teams Bot Framework
  POST /api/nuvoco_frontend           – Web/frontend chat
  GET  /api/token_usage               – Monthly token budget (blob-backed)
  GET  /api/admin/token_overview      – Admin: org token aggregates (year, daily/monthly)
  GET  /api/admin/token_users         – Admin: known users + effective limits
  POST /api/admin/token_limits        – Admin: set monthly limits by email
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
from datetime import datetime, timezone
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
import admin_tokens
import token_usage
from langgraph_chain import ThreadedChatManager
from token_usage import TokenLimitExceeded

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


def _validate_admin(req: func.HttpRequest) -> tuple[bool, str]:
    api_key_env = (os.getenv("ADMIN_API_KEY") or "").strip()
    emails_raw = (os.getenv("ADMIN_EMAILS") or "").strip()
    allowed = {e.strip().lower() for e in emails_raw.split(",") if e.strip()}
    hdr_email = (
        req.headers.get("X-Admin-User-Email")
        or req.headers.get("x-admin-user-email")
        or ""
    ).strip().lower()
    hdr_key = (
        req.headers.get("X-Admin-Api-Key") or req.headers.get("x-admin-api-key") or ""
    ).strip()

    if not api_key_env and not allowed:
        return False, "Admin access is not configured (set ADMIN_EMAILS and/or ADMIN_API_KEY)."

    if api_key_env and hdr_key != api_key_env:
        return False, "Invalid or missing admin API key."

    if allowed and hdr_email not in allowed:
        return False, "Your account is not authorized for admin access."

    return True, ""


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
        from_block = body.get("from") or {}
        user_id = from_block.get("id") or body.get("user_id", "")

        logger.info("Frontend | channel=%s | text=%s", channel_id, message)

        await token_usage.check_request_allowed_async(user_id, message)

        result = await asyncio.to_thread(chat_manager.chat, message, channel_id)

        if result:
            answer, suggested_questions, status_code = result
            usage_payload = {}
            if user_id:
                usage_payload = await token_usage.record_turn_async(
                    user_id, message, answer or ""
                )
            return _json_response({
                "answer": answer,
                "suggested_questions": suggested_questions or [],
                "status": status_code,
                "token_usage": usage_payload,
            })

        usage_payload = {}
        if user_id:
            usage_payload = await token_usage.get_usage_async(user_id)
        return _json_response({
            "answer": "No response generated.",
            "suggested_questions": [],
            "status": 200,
            "token_usage": usage_payload,
        })

    except TokenLimitExceeded as exc:
        logger.warning("Token limit | user: %s", exc.message)
        return _json_response({"error": exc.message, "code": "TOKEN_LIMIT"}, 429)
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


@app.route(route="token_usage", methods=["GET"])
async def get_token_usage(req: func.HttpRequest) -> func.HttpResponse:
    try:
        user_id = _first_query_value(req, "user_id")
        if not user_id:
            return _error_response("user_id query parameter required", 400)
        data = await token_usage.get_usage_async(user_id)
        return _json_response(data)

    except Exception as exc:
        logger.exception("get_token_usage failed")
        return _error_response(str(exc))


@app.route(route="admin/token_overview", methods=["GET"])
async def admin_token_overview(req: func.HttpRequest) -> func.HttpResponse:
    ok, msg = _validate_admin(req)
    if not ok:
        return _json_response({"error": msg}, 403)
    try:
        year_raw = _first_query_value(req, "year")
        year = int(year_raw) if year_raw else datetime.now(timezone.utc).year
        if year < 2000 or year > 2100:
            return _error_response("year out of range", 400)
        data = await admin_tokens.aggregate_year_async(year)
        return _json_response(data)
    except ValueError:
        return _error_response("invalid year", 400)
    except Exception as exc:
        logger.exception("admin_token_overview failed")
        return _error_response(str(exc))


@app.route(route="admin/token_users", methods=["GET"])
async def admin_token_users(req: func.HttpRequest) -> func.HttpResponse:
    ok, msg = _validate_admin(req)
    if not ok:
        return _json_response({"error": msg}, 403)
    try:
        rows = await admin_tokens.list_users_with_limits_async()
        return _json_response({"users": rows})
    except Exception as exc:
        logger.exception("admin_token_users failed")
        return _error_response(str(exc))


@app.route(route="admin/token_limits", methods=["POST"])
async def admin_token_limits(req: func.HttpRequest) -> func.HttpResponse:
    ok, msg = _validate_admin(req)
    if not ok:
        return _json_response({"error": msg}, 403)
    try:
        body = req.get_json()
        emails = body.get("emails") or []
        if not isinstance(emails, list) or not emails:
            return _error_response("emails array required", 400)
        in_lim = body.get("input_limit")
        out_lim = body.get("output_limit")
        if in_lim is None or out_lim is None:
            return _error_response("input_limit and output_limit required", 400)
        in_lim_i = int(in_lim)
        out_lim_i = int(out_lim)
        if in_lim_i < 0 or out_lim_i < 0:
            return _error_response("limits must be non-negative", 400)

        updated = []
        for raw in emails:
            uid = str(raw).strip()
            if not uid:
                continue
            await token_usage.set_user_limits_async(uid, in_lim_i, out_lim_i)
            updated.append(uid)

        if not updated:
            return _error_response("no valid emails in list", 400)

        return _json_response({"updated": updated, "count": len(updated)})
    except Exception as exc:
        logger.exception("admin_token_limits failed")
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
