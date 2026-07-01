"""Анализ запроса пользователя: извлечение идентификатора хоста.

Порт _llm_analyze_request. Baseline: только hostname/ip/ci (фича «система» убрана).
Трёхступенчатая устойчивость: function call → JSON fallback → focused retry.
"""
import re
import json

import gigachat
import prompts


def _parse(args: dict) -> tuple[str | None, str | None]:
    ident = (args.get("identifier") or "").strip() or None
    ident_type = args.get("type", "none")
    if ident_type == "none":
        ident = None
        ident_type = None
    return ident, ident_type


async def analyze_request(
    user_msg: str,
    history: list[dict],
) -> tuple[str | None, str | None]:
    """Вернёт (identifier, ident_type)."""
    messages = [{"role": "system", "content": prompts.PROMPT_ANALYZE}]
    for h in history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    # Попытка 1: function call
    try:
        _, fn_call = await gigachat.fn_call(
            messages, [prompts.ANALYZE_FUNCTION], force_fn="analyze_request"
        )
        if fn_call:
            result = _parse(json.loads(fn_call.get("arguments", "{}")))
            if result[0] is not None:
                return result
    except Exception:
        pass

    # Попытка 2: JSON fallback
    json_suffix = (
        "\n\nВерни ТОЛЬКО JSON-объект:\n"
        '{"identifier": "<значение или пустая строка>", "type": "hostname|ip|ci|none"}'
    )
    messages[0] = {"role": "system", "content": prompts.PROMPT_ANALYZE + json_suffix}
    try:
        raw = await gigachat.complete(messages)
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if m:
            result = _parse(json.loads(m.group()))
            if result[0] is not None:
                return result
    except Exception:
        pass

    # Попытка 3: focused retry только на идентификатор (без истории)
    try:
        raw = await gigachat.complete(
            [{"role": "system", "content": prompts.PROMPT_IDENT_RETRY},
             {"role": "user", "content": user_msg}],
        )
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            ident = (data.get("identifier") or "").strip() or None
            itype = data.get("type", "none")
            if itype != "none" and ident:
                return ident, itype
    except Exception:
        pass

    return None, None
