import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from aiohttp import web
from aiohttp.web import Request, Response

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
    MemoryStorage,
    ConversationState,
)
from botbuilder.schema import Activity

from langgraph_chain import ThreadedChatManager
from teams_bot.my_bot import MyBot
import chat_history
import admin_tokens
import token_usage
from token_usage import TokenLimitExceeded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

chat_manager = ThreadedChatManager()

# ─────────────────────────────────────────────
# Bot Setup
# ─────────────────────────────────────────────
SETTINGS = BotFrameworkAdapterSettings(
    app_id=os.environ.get("MICROSOFT_APP_ID", ""),
    app_password=os.environ.get("MICROSOFT_APP_PASSWORD", ""),
    channel_auth_tenant=os.environ.get("MICROSOFT_APP_TENANT_ID", ""),
)

MEMORY = MemoryStorage()
CONVERSATION_STATE = ConversationState(MEMORY)

BOT = MyBot(CONVERSATION_STATE)
ADAPTER = BotFrameworkAdapter(SETTINGS)


async def on_error(context: TurnContext, error: Exception):
    logger.exception("BOT ERROR: %s", error)
    await context.send_activity(
        "Sorry, something went wrong while processing your request."
    )


ADAPTER.on_turn_error = on_error


def _validate_admin(req: Request) -> tuple[bool, str]:
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


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
async def ping(req: Request) -> Response:
    return Response(text="ok")


async def messages(req: Request) -> Response:
    try:
        body = await req.json()
        activity = Activity().deserialize(body)
        auth_header = req.headers.get("Authorization", "")

        logger.info(
            "Incoming | type=%s | channel=%s | text=%s",
            activity.type,
            activity.channel_id,
            activity.text,
        )

        response = await ADAPTER.process_activity(
            activity,
            auth_header,
            BOT.on_turn,
        )

        if response:
            return Response(
                body=json.dumps(response.body) if response.body else "",
                status=response.status,
                content_type="application/json",
            )

        return Response(status=200)

    except Exception as e:
        logger.exception("messages endpoint failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def nuvoco_frontend(req: Request) -> Response:
    try:
        body = await req.json()

        message = body.get("text", body.get("message", ""))
        channel_id = body.get("channelId", body.get("session_id", "default"))
        from_block = body.get("from") or {}
        user_id = from_block.get("id") or body.get("user_id", "")

        logger.info("Frontend | channel=%s | text=%s", channel_id, message)

        await token_usage.check_request_allowed_async(user_id, message)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            chat_manager.chat,
            message,
            channel_id,
        )

        if result:
            answer, suggested_questions, status_code = result
            usage_payload = {}
            if user_id:
                usage_payload = await token_usage.record_turn_async(
                    user_id, message, answer or ""
                )
            return Response(
                body=json.dumps({
                    "answer": answer,
                    "suggested_questions": suggested_questions or [],
                    "status": status_code,
                    "token_usage": usage_payload,
                }),
                status=200,
                content_type="application/json",
            )

        usage_payload = {}
        if user_id:
            usage_payload = await token_usage.get_usage_async(user_id)
        return Response(
            body=json.dumps({
                "answer": "No response generated.",
                "suggested_questions": [],
                "status": 200,
                "token_usage": usage_payload,
            }),
            status=200,
            content_type="application/json",
        )

    except TokenLimitExceeded as exc:
        logger.warning("Token limit | %s", exc.message)
        return Response(
            body=json.dumps({"error": exc.message, "code": "TOKEN_LIMIT"}),
            status=429,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("nuvoco_frontend endpoint failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def admin_token_overview(req: Request) -> Response:
    ok, msg = _validate_admin(req)
    if not ok:
        return Response(
            body=json.dumps({"error": msg}),
            status=403,
            content_type="application/json",
        )
    try:
        year_raw = req.query.get("year", "")
        year = int(year_raw) if year_raw else datetime.now(timezone.utc).year
        if year < 2000 or year > 2100:
            return Response(
                body=json.dumps({"error": "year out of range"}),
                status=400,
                content_type="application/json",
            )
        data = await admin_tokens.aggregate_year_async(year)
        return Response(
            body=json.dumps(data),
            status=200,
            content_type="application/json",
        )
    except ValueError:
        return Response(
            body=json.dumps({"error": "invalid year"}),
            status=400,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("admin_token_overview failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def admin_token_users(req: Request) -> Response:
    ok, msg = _validate_admin(req)
    if not ok:
        return Response(
            body=json.dumps({"error": msg}),
            status=403,
            content_type="application/json",
        )
    try:
        rows = await admin_tokens.list_users_with_limits_async()
        return Response(
            body=json.dumps({"users": rows}),
            status=200,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("admin_token_users failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def admin_token_limits(req: Request) -> Response:
    ok, msg = _validate_admin(req)
    if not ok:
        return Response(
            body=json.dumps({"error": msg}),
            status=403,
            content_type="application/json",
        )
    try:
        body = await req.json()
        emails = body.get("emails") or []
        if not isinstance(emails, list) or not emails:
            return Response(
                body=json.dumps({"error": "emails array required"}),
                status=400,
                content_type="application/json",
            )
        in_lim = body.get("input_limit")
        out_lim = body.get("output_limit")
        if in_lim is None or out_lim is None:
            return Response(
                body=json.dumps({"error": "input_limit and output_limit required"}),
                status=400,
                content_type="application/json",
            )
        in_lim_i = int(in_lim)
        out_lim_i = int(out_lim)
        if in_lim_i < 0 or out_lim_i < 0:
            return Response(
                body=json.dumps({"error": "limits must be non-negative"}),
                status=400,
                content_type="application/json",
            )
        updated = []
        for raw in emails:
            uid = str(raw).strip()
            if not uid:
                continue
            await token_usage.set_user_limits_async(uid, in_lim_i, out_lim_i)
            updated.append(uid)
        if not updated:
            return Response(
                body=json.dumps({"error": "no valid emails in list"}),
                status=400,
                content_type="application/json",
            )
        return Response(
            body=json.dumps({"updated": updated, "count": len(updated)}),
            status=200,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("admin_token_limits failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def get_token_usage(req: Request) -> Response:
    try:
        user_id = req.query.get("user_id", "")
        if not user_id:
            return Response(
                body=json.dumps({"error": "user_id query parameter required"}),
                status=400,
                content_type="application/json",
            )
        data = await token_usage.get_usage_async(user_id)
        return Response(
            body=json.dumps(data),
            status=200,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("get_token_usage failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


# ─────────────────────────────────────────────
# Chat History Routes
# ─────────────────────────────────────────────
async def get_history(req: Request) -> Response:
    try:
        user_id = req.query.get("user_id", "")
        if not user_id:
            return Response(
                body=json.dumps({"error": "user_id query parameter required"}),
                status=400,
                content_type="application/json",
            )

        conversations = await chat_history.get_user_conversations_async(user_id)
        return Response(
            body=json.dumps(conversations),
            status=200,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("get_history failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def save_history(req: Request) -> Response:
    try:
        body = await req.json()
        user_id = body.get("user_id", "")
        conversations = body.get("conversations", [])

        if not user_id:
            return Response(
                body=json.dumps({"error": "user_id required"}),
                status=400,
                content_type="application/json",
            )

        await chat_history.save_user_conversations_async(user_id, conversations)
        return Response(
            body=json.dumps({"status": "ok"}),
            status=200,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("save_history failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


async def delete_history(req: Request) -> Response:
    try:
        body = await req.json()
        user_id = body.get("user_id", "")
        conversation_id = body.get("conversation_id", "")

        if not user_id or not conversation_id:
            return Response(
                body=json.dumps({"error": "user_id and conversation_id required"}),
                status=400,
                content_type="application/json",
            )

        deleted = await chat_history.delete_conversation_async(
            user_id, conversation_id
        )
        return Response(
            body=json.dumps({"deleted": deleted}),
            status=200,
            content_type="application/json",
        )
    except Exception as e:
        logger.exception("delete_history failed")
        return Response(
            body=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
        )


# ─────────────────────────────────────────────
# App factory
# ─────────────────────────────────────────────
def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/ping", ping)
    app.router.add_post("/api/messages", messages)
    app.router.add_post("/api/nuvoco_frontend", nuvoco_frontend)
    app.router.add_get("/api/token_usage", get_token_usage)
    app.router.add_get("/api/admin/token_overview", admin_token_overview)
    app.router.add_get("/api/admin/token_users", admin_token_users)
    app.router.add_post("/api/admin/token_limits", admin_token_limits)
    app.router.add_get("/api/chat_history", get_history)
    app.router.add_post("/api/chat_history", save_history)
    app.router.add_post("/api/chat_history/delete", delete_history)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    web.run_app(create_app(), host="0.0.0.0", port=port)
