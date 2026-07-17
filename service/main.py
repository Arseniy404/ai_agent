"""aiohttp-приложение. Роутинг детерминированный: кнопка -> action-функция.
LLM участвует только в /action/server_status (форматирование) и /chat/followup
(ответ по уже полученным данным) — ни в одном из этих мест LLM не выбирает,
что запросить у Zabbix.
"""
import json
import logging
from pathlib import Path

from aiohttp import web

from service import config
from service.actions.server_status import ActionError, action_server_status
from service.llm import gigachat_client
from service.llm.prompts import PROMPT_FOLLOWUP, PROMPT_STATUS
from service.session import get_session
from service.sse import send_event, start_sse

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()


def _render_data(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    return web.FileResponse(
        request.app["templates_dir"] / "index.html",
    )


@routes.post("/action/server_status")
async def action_server_status_route(request: web.Request) -> web.StreamResponse:
    resp = await start_sse(request)
    session = get_session()

    try:
        structured_data = await action_server_status()
    except ActionError as exc:
        await send_event(resp, "error", text=str(exc))
        await send_event(resp, "done", reply="")
        return resp

    session.set_structured_data(structured_data)
    await send_event(resp, "meta", data=structured_data)

    messages = [
        {"role": "system", "content": PROMPT_STATUS.format(data=_render_data(structured_data))},
    ]

    full_reply = []
    async for delta in gigachat_client.stream(messages):
        full_reply.append(delta)
        await send_event(resp, "chunk", text=delta)

    reply_text = "".join(full_reply)
    session.add_message("assistant", reply_text)
    await send_event(resp, "done", reply=reply_text)
    return resp


@routes.post("/chat/followup")
async def chat_followup(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    user_message = (body.get("message") or "").strip()

    resp = await start_sse(request)
    session = get_session()

    if not user_message:
        await send_event(resp, "error", text="Пустое сообщение")
        await send_event(resp, "done", reply="")
        return resp

    if session.last_structured_data is None:
        await send_event(
            resp, "error",
            text="Данных пока нет — сначала нажмите кнопку «Как дела с сервером?».",
        )
        await send_event(resp, "done", reply="")
        return resp

    session.add_message("user", user_message)

    messages = [
        {"role": "system", "content": PROMPT_FOLLOWUP.format(
            data=_render_data(session.last_structured_data),
        )},
        *session.history,
    ]

    full_reply = []
    async for delta in gigachat_client.stream(messages):
        full_reply.append(delta)
        await send_event(resp, "chunk", text=delta)

    reply_text = "".join(full_reply)
    session.add_message("assistant", reply_text)
    await send_event(resp, "done", reply=reply_text)
    return resp


def create_app() -> web.Application:
    app = web.Application()
    app["templates_dir"] = Path(__file__).parent / "templates"
    app.add_routes(routes)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    web.run_app(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
