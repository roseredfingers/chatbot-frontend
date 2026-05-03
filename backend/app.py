import asyncio
import json
import logging
import os

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

        logger.info("Frontend | channel=%s | text=%s", channel_id, message)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            chat_manager.chat,
            message,
            channel_id,
        )

        if result:
            answer, suggested_questions, status_code = result
            return Response(
                body=json.dumps({
                    "answer": answer,
                    "suggested_questions": suggested_questions or [],
                    "status": status_code,
                }),
                status=200,
                content_type="application/json",
            )

        return Response(
            body=json.dumps({
                "answer": "No response generated.",
                "suggested_questions": [],
                "status": 200,
            }),
            status=200,
            content_type="application/json",
        )

    except Exception as e:
        logger.exception("nuvoco_frontend endpoint failed")
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
    app.router.add_get("/api/chat_history", get_history)
    app.router.add_post("/api/chat_history", save_history)
    app.router.add_post("/api/chat_history/delete", delete_history)
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    web.run_app(create_app(), host="0.0.0.0", port=port)
