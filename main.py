import os
import json
import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

import config
import dialog
import self_portal


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await self_portal.aclose()


app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat")
async def chat(request: Request):
    body = await request.json()

    async def gen():
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event_type: str, data: dict):
            payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
            await queue.put(f"data: {payload}\n\n")

        async def run():
            try:
                await dialog.handle_chat(body, emit)
            except Exception as e:
                await emit("error", {"text": f"⚠️ Внутренняя ошибка сервера: {e}"})
                await emit("done", {})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
