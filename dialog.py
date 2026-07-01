"""Оркестрация диалога: intent → данные из портала → ответ.

Baseline: только статус конкретного хоста (hostname/ip/ci). Без графиков, списка
метрик, загрузки файлов, переопределения промптов и фичи «система/группа».
"""
from typing import Awaitable, Callable

import analyze
import gigachat
import prompts
import self_portal as sp

Emit = Callable[[str, dict], Awaitable[None]]


def _safe_format(template: str, **kwargs) -> str:
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError, IndexError):
        result = template
        for k, v in kwargs.items():
            result = result.replace('{' + k + '}', str(v))
        return result


def _build_expert_ctx(name: str, info: dict, active: list[dict]) -> str:
    return (
        f"Сервер: {name} | IP: {info['ip']} | КЭ: {info['ci']}\n"
        f"ОС: {info['os']} | Система: {info['group']}\n\n"
        "Активные триггеры:\n" + "\n".join(
            f"  [{t['severity']}] {t['title']}"
            + (f" (активен {t['ago_min']} мин)" if t.get('ago_min') else "")
            for t in active
        )
    )


async def _stream_reply(emit: Emit, messages: list[dict]) -> str:
    parts: list[str] = []
    try:
        async for chunk in gigachat.stream(messages):
            parts.append(chunk)
            await emit("chunk", {"text": chunk})
    except Exception as e:
        err = f"⚠️ Ошибка GigaChat: {e}"
        parts.append(err)
        await emit("chunk", {"text": err})
    return "".join(parts)


async def _stream_expert(emit: Emit, system_prompt: str, user_ctx: str) -> None:
    msgs = [{"role": "system", "content": system_prompt},
            {"role": "user", "content": user_ctx}]
    try:
        async for chunk in gigachat.stream(msgs):
            await emit("expert_chunk", {"text": chunk})
    except Exception as e:
        await emit("expert_chunk", {"text": f"⚠️ Ошибка анализа: {e}"})


async def handle_chat(body: dict, emit: Emit) -> None:
    user_msg = (body.get("message") or "").strip()
    history = body.get("history", [])
    prev_server = body.get("server_name")

    if not user_msg:
        await emit("error", {"text": "Пустое сообщение."})
        await emit("done", {})
        return

    # ── Phase 1: анализ запроса ───────────────────────────────────────────────
    ident, ident_type = await analyze.analyze_request(user_msg, history)
    fresh_ident = ident is not None

    if not ident and prev_server:
        ident, ident_type = prev_server, "hostname"

    # ── Fast path: болтовня без свежего идентификатора ────────────────────────
    if not fresh_ident and not ident:
        ctx = prompts.CTX_NO_IDENT
        messages = [{"role": "system", "content": prompts.SYSTEM_PROMPT + ctx}]
        for h in history[-8:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_msg})
        reply = await _stream_reply(emit, messages)
        await emit("done", {"reply": reply})
        return

    if not fresh_ident and prev_server:
        ctx = (f"\n[КОНТЕКСТ ДИАЛОГА]\nПоследний обсуждавшийся сервер: «{prev_server}».\n"
               "Отвечай в этом контексте. Если речь о ДРУГОМ сервере — попроси назвать его.")
        messages = [{"role": "system", "content": prompts.SYSTEM_PROMPT + ctx}]
        for h in history[-8:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_msg})
        reply = await _stream_reply(emit, messages)
        await emit("done", {"reply": reply})
        return

    # ── Phase 2: поиск хоста (hostname/ip/ci → авто-резолв стенда) ────────────
    found_name = found_info = None
    if ident:
        found_name, found_info = await sp.lookup_host(ident, ident_type or "hostname")

    if found_info:
        triggers = await sp.get_host_triggers(found_name)
        active = [t for t in triggers if t["active"]]
        n_ok = len(triggers) - len(active)
        t_lines = [f"  🔴 [{t['severity']}] {t['title']}"
                   + (f" ({t['ago_min']} мин)" if t.get("ago_min") else "") for t in active]
    else:
        triggers, active, n_ok, t_lines = [], [], 0, []

    await emit("meta", {"server_name": found_name, "server_info": found_info,
                        "triggers": active, "n_ok": n_ok})

    if found_info and active and fresh_ident:
        await _stream_expert(emit, prompts.PROMPT_EXPERT,
                             _build_expert_ctx(found_name, found_info, active))

    if found_info:
        ctx_block = _safe_format(
            prompts.CTX_FOUND,
            name=found_name, ip=found_info["ip"], ci=found_info["ci"],
            os=found_info["os"], group=found_info["group"],
            stand=found_info.get("stand", ""),
            n_active=len(active), n_total=len(triggers), n_ok=n_ok,
            trigger_lines="\n".join(t_lines) if t_lines else "  (нет активных проблем)",
        )
    elif ident:
        ctx_block = _safe_format(prompts.CTX_NOT_FOUND, ident=ident)
    else:
        ctx_block = prompts.CTX_NO_IDENT

    messages = [{"role": "system", "content": prompts.SYSTEM_PROMPT + ctx_block}]
    for h in history[-8:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})
    reply = await _stream_reply(emit, messages)
    await emit("done", {"reply": reply})
