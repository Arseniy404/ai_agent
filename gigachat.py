"""Клиент GigaChat: intent-анализ (function calling), стриминг ответов.

Портирован из исходного zabbix_bot.py на httpx. Baseline: одна модель из env,
без vision/вложений и без переключения модели с фронта.
"""
import ssl
import time
import uuid
import json

import httpx

import config

_MODEL = config.GIGACHAT_MODEL

# SSL-контекст без проверки хоста (как в исходной версии — самоподписанный CA Сбера).
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

# Bootstrap: если задан GIGACHAT_TOKEN, используем его сразу (~30 мин), не дожидаясь
# первого OAuth-запроса. После истечения — обычное автообновление по AUTH_KEY.
_token_cache = {
    "token": config.GIGACHAT_TOKEN,
    "expires_at": time.time() + 1800 if config.GIGACHAT_TOKEN else 0.0,
}


async def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {config.GIGACHAT_AUTH_KEY}",
    }
    async with httpx.AsyncClient(verify=_ssl_ctx, timeout=15) as sess:
        resp = await sess.post(
            config.GIGACHAT_AUTH_URL,
            data={"scope": config.GIGACHAT_SCOPE},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = data.get("expires_at", now + 1800) / 1000
    return _token_cache["token"]


async def complete(messages: list[dict]) -> str:
    """Синхронный (нестримовый) ответ модели."""
    token = await _get_token()
    payload = {"model": _MODEL, "messages": messages, "profanity_check": True}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(verify=_ssl_ctx, timeout=45) as sess:
        resp = await sess.post(config.GIGACHAT_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def fn_call(
    messages: list[dict],
    functions: list[dict],
    force_fn: str | None = None,
) -> tuple[str, dict | None]:
    """Вызов с function calling. Возвращает (content, function_call|None)."""
    token = await _get_token()
    payload: dict = {
        "model": _MODEL,
        "messages": messages,
        "functions": functions,
        "profanity_check": True,
        "function_call": {"name": force_fn} if force_fn else "auto",
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(verify=_ssl_ctx, timeout=45) as sess:
        resp = await sess.post(config.GIGACHAT_API_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    msg = data["choices"][0]["message"]
    return msg.get("content", ""), msg.get("function_call")


async def stream(messages: list[dict]):
    """Потоковый ответ модели: yield-ит куски текста."""
    token = await _get_token()
    payload = {"model": _MODEL, "messages": messages, "stream": True, "profanity_check": True}
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    async with httpx.AsyncClient(verify=_ssl_ctx, timeout=120) as sess:
        async with sess.stream("POST", config.GIGACHAT_API_URL, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data: "):
                    continue
                chunk_str = line[6:]
                if chunk_str == "[DONE]":
                    return
                try:
                    chunk = json.loads(chunk_str)
                    content = chunk["choices"][0]["delta"].get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass
