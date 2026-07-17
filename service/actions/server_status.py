"""Action-слой: детерминированное получение статуса привязанного сервера.

Никакого LLM здесь нет — это чистая функция "нажатие кнопки -> структурированные
данные". LLM подключается только после того, как structured_data уже собран.
"""
import time
from datetime import datetime, timezone

import aiohttp

from service import config
from service.gproxy.client import ZabbixError, get_client

_SEVERITY = {
    "0": "INFO",
    "1": "INFO",
    "2": "LOW",
    "3": "MEDIUM",
    "4": "HIGH",
    "5": "HIGH",
}


class ActionError(Exception):
    """Ожидаемая ошибка (хост не найден, Zabbix недоступен) — показывается пользователю
    напрямую, без обращения к LLM."""


async def get_bound_server() -> str:
    return config.BOUND_HOST


async def action_server_status() -> dict:
    hostname = await get_bound_server()
    client = get_client()

    async with aiohttp.ClientSession() as session:
        try:
            hostid = await client.get_hostid(session, hostname)
            if hostid is None:
                raise ActionError(f"Хост '{hostname}' не найден в Zabbix")

            host_info = await client.get_host_info(session, hostid)
            triggers = await client.get_triggers(session, hostid)
        except ZabbixError as exc:
            raise ActionError(f"Zabbix недоступен: {exc}") from exc

    now = time.time()
    triggers_active = []
    for trig in triggers:
        if trig["value"] != "1":
            continue
        lastchange = int(trig["lastchange"])
        ago_min = max(0, int((now - lastchange) / 60))
        triggers_active.append({
            "title": trig["description"],
            "severity": _SEVERITY.get(trig["priority"], "INFO"),
            "ago_min": ago_min,
        })

    return {
        "action": "server_status",
        "server": host_info,
        "triggers_active": triggers_active,
        "n_active": len(triggers_active),
        "n_total": len(triggers),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
