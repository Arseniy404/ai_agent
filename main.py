import os
from dotenv import load_dotenv
load_dotenv()

import aiohttp_jinja2
import jinja2
from aiohttp import web
import zabbix_bot as bot

def make_app() -> web.Application:
    app = web.Application()
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(
            os.path.join(os.path.dirname(__file__), "templates")
        ),
    )
    app.router.add_get("/",        bot.index)
    app.router.add_get("/prompt",  bot.get_prompt)
    app.router.add_post("/chat",   bot.chat)
    app.router.add_post("/upload", bot.upload_file)
    app.router.add_post("/graph",  bot.graph_item)
    return app

if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=8077)
