"""Общий помощник для SSE-событий вида `data: {...}\\n\\n`."""
import json

from aiohttp import web


async def start_sse(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)
    return resp


async def send_event(resp: web.StreamResponse, event_type: str, **payload) -> None:
    data = {"type": event_type, **payload}
    await resp.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
