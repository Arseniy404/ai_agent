"""Клиент GigaChat. Единственная используемая роль — потоковое форматирование текста
(_stream). Function calling / выбор инструментов сюда сознательно не перенесены —
роутинг данных делает action-слой, а не LLM.
"""
import base64
import json
import ssl
import time
from collections.abc import AsyncIterator

import aiohttp

from service import config

_token_cache = {"token": None, "expires_at": 0.0}


def _ssl_context() -> ssl.SSLContext:
    # Sber CA не входит в стандартные хранилища доверенных сертификатов.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _get_token(session: aiohttp.ClientSession) -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    headers = {
        "Authorization": f"Basic {config.GIGACHAT_AUTH_KEY}",
        "RqUID": base64.b16encode(str(time.time()).encode()).decode()[:32],
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"scope": config.GIGACHAT_SCOPE}
    timeout = aiohttp.ClientTimeout(total=config.GIGACHAT_AUTH_TIMEOUT_SECONDS)

    async with session.post(
        config.GIGACHAT_AUTH_URL, headers=headers, data=data,
        timeout=timeout, ssl=_ssl_context(),
    ) as resp:
        payload = await resp.json()

    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = payload["expires_at"] / 1000
    return _token_cache["token"]


async def stream(messages: list[dict], model: str | None = None) -> AsyncIterator[str]:
    """Потоковый вызов chat/completions. Возвращает текстовые фрагменты ответа.
    Никаких functions/function_call — LLM тут только форматирует, ничего не вызывает."""
    model = model or config.GIGACHAT_MODEL

    async with aiohttp.ClientSession() as session:
        token = await _get_token(session)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        timeout = aiohttp.ClientTimeout(total=config.GIGACHAT_STREAM_TIMEOUT_SECONDS)

        async with session.post(
            config.GIGACHAT_API_URL, headers=headers, json=body,
            timeout=timeout, ssl=_ssl_context(),
        ) as resp:
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if chunk == "[DONE]":
                    break
                try:
                    parsed = json.loads(chunk)
                except ValueError:
                    continue
                delta = parsed["choices"][0]["delta"].get("content")
                if delta:
                    yield delta
