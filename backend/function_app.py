"""
Azure Functions v2 Python app.
Replaces the previous aiohttp-based app.py.

HTTP triggers (all under /api/... prefix, set in host.json):
  GET  /api/ping
  POST /api/messages                  – Teams Bot Framework
  POST /api/nuvoco_frontend           – Web/frontend chat
  GET  /api/chat_history              – Load all conversations for a user
  POST /api/chat_history              – Bulk-save conversations
  POST /api/chat_history_delete       – Delete a single conversation
  POST /api/prime_conversation        – Inject prior history into RAG thread (first open)
  POST /api/append_exchange           – Persist a single user/assistant exchange
"""

import json
import logging
import os

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

# Deferred import so teams_bot is optional during local dev
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
    )


def _error_response(message: str, status_code: int = 500) -> func.HttpResponse:
    return _json_response({"error": message}, status_code)


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────
@app.route(route="ping", methods=["GET"])
def ping(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("ok", status_code=200)


# ─────────────────────────────────────────────
# Teams Bot Framework
# ─────────────────────────────────────────────
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
            )
        return func.HttpResponse(status_code=200)

    except Exception as exc:
        logger.exception("messages endpoint failed")
        return _error_response(str(exc))


# ─────────────────────────────────────────────
# Web / Frontend Chat
# ─────────────────────────────────────────────
@app.route(route="nuvoco_frontend", methods=["POST"])
def nuvoco_frontend(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        message = body.get("text", body.get("message", ""))
        channel_id = body.get("channelId", body.get("session_id", "default"))

        logger.info("Frontend | channel=%s | text=%s", channel_id, message)

        result = chat_manager.chat(message, channel_id)

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


# ─────────────────────────────────────────────
# Prime Conversation (inject prior history into RAG thread on first open)
# ─────────────────────────────────────────────
@app.route(route="prime_conversation", methods=["POST"])
def prime_conversation(req: func.HttpRequest) -> func.HttpResponse:
    """
    Called once per login session when the user opens a stored conversation
    for the first time. Seeds the in-memory LangGraph checkpoint with the
    conversation's persisted messages so the RAG flow has full context.

    Body: { "session_id": str, "messages": [{"role": "user"|"bot", "content": str, "timestamp": str}] }
    """
    try:
        body = req.get_json()
        session_id = body.get("session_id", "")
        messages_payload = body.get("messages", [])

        if not session_id:
            return _error_response("session_id required", 400)

        chat_manager.inject_history(session_id, messages_payload)

        logger.info(
            "Primed conversation %s with %d messages.", session_id, len(messages_payload)
        )
        return _json_response({"status": "ok", "primed": len(messages_payload)})

    except Exception as exc:
        logger.exception("prime_conversation endpoint failed")
        return _error_response(str(exc))


# ─────────────────────────────────────────────
# Append Exchange (save one user+assistant turn immediately)
# ─────────────────────────────────────────────
@app.route(route="append_exchange", methods=["POST"])
def append_exchange(req: func.HttpRequest) -> func.HttpResponse:
    """
    Appends a single user/assistant exchange to a conversation in blob storage.
    Called right after each assistant response.

    Body:
    {
      "user_id": str,
      "conversation_id": str,
      "conversation_title": str,
      "user_message": { "content": str, "timestamp": str },
      "assistant_message": { "content": str, "timestamp": str, "suggestedQuestions": [...] }
    }
    """
    try:
        body = req.get_json()
        user_id = body.get("user_id", "")
        conversation_id = body.get("conversation_id", "")
        conversation_title = body.get("conversation_title", "New Chat")
        user_msg = body.get("user_message", {})
        assistant_msg = body.get("assistant_message", {})

        if not user_id or not conversation_id:
            return _error_response("user_id and conversation_id required", 400)

        history = chat_history.load_history(user_id)
        convs = history.get("conversations", [])

        existing = next((c for c in convs if c["id"] == conversation_id), None)
        if existing is None:
            existing = chat_history.build_conversation(conversation_id, conversation_title)
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
        chat_history.save_history(user_id, history)

        return _json_response({"status": "ok"})

    except Exception as exc:
        logger.exception("append_exchange endpoint failed")
        return _error_response(str(exc))


# ─────────────────────────────────────────────
# Chat History — Bulk Load / Save / Delete
# ─────────────────────────────────────────────
@app.route(route="chat_history", methods=["GET"])
def get_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        user_id = req.params.get("user_id", "")
        if not user_id:
            return _error_response("user_id query parameter required", 400)

        conversations = chat_history.get_user_conversations(user_id)
        return _json_response(conversations)

    except Exception as exc:
        logger.exception("get_history failed")
        return _error_response(str(exc))


@app.route(route="chat_history", methods=["POST"])
def save_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        user_id = body.get("user_id", "")
        conversations = body.get("conversations", [])

        if not user_id:
            return _error_response("user_id required", 400)

        chat_history.save_user_conversations(user_id, conversations)
        return _json_response({"status": "ok"})

    except Exception as exc:
        logger.exception("save_history failed")
        return _error_response(str(exc))


@app.route(route="chat_history_delete", methods=["POST"])
def delete_history(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
        user_id = body.get("user_id", "")
        conversation_id = body.get("conversation_id", "")

        if not user_id or not conversation_id:
            return _error_response("user_id and conversation_id required", 400)

        deleted = chat_history.delete_conversation(user_id, conversation_id)
        return _json_response({"deleted": deleted})

    except Exception as exc:
        logger.exception("delete_history failed")
        return _error_response(str(exc))
