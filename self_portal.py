"""Клиент к корпоративному SELF-порталу (только mTLS, без Bearer-токена).

Baseline: только статус конкретного хоста (hostname/ip/ci). Фича «система/группа»
убрана — нет надёжного источника Zabbix host-групп в присланных схемах портала.

Severity в /events — та же шкала 0-5, что и в исходном Zabbix-боте.
"""
import asyncio

import httpx

import config

_SEV_STR = {0: "INFO", 1: "INFO", 2: "LOW", 3: "MEDIUM", 4: "HIGH", 5: "HIGH"}
_ZABBIX_TYPES = ("infra", "usi", "bn_cluster", "net")

# ── Транспорт ────────────────────────────────────────────────────────────────
# Один httpx-клиент с mTLS на весь процесс. Логин/токен не нужны — авторизация
# идёт через клиентский сертификат на TLS-уровне (подтверждено пользователем).
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    cert=(config.SELF_CERT, config.SELF_KEY),
                    verify=config.SELF_VERIFY,
                    timeout=httpx.Timeout(30.0),
                )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _call(service: str, method: str, path: str, **kw):
    client = await _get_client()
    url = f"{config.SELF_URLS[service]}{path}"
    resp = await client.request(method, url, **kw)
    resp.raise_for_status()
    return resp.json()


# ── Низкоуровневые вызовы конкретных эндпоинтов ──────────────────────────────

async def _get_zabbix_instances(zbx_type: str) -> list[dict]:
    """GET /get_zabbix_by_type?type=... -> [{url, name, type}]."""
    return await _call("zabbix", "GET", "/get_zabbix_by_type", params={"type": zbx_type})


async def _report_get_host(zabbix_name: str, hostname: str) -> dict | None:
    """POST /report/get_host {zabbix, hostname} -> {result, host: {hostid: {...}}} | None."""
    data = await _call("api", "POST", "/report/get_host",
                        json={"zabbix": zabbix_name, "hostname": hostname})
    hosts = data.get("host") or {}
    if not hosts:
        return None
    # host — словарь {hostid: {...}}; в baseline ожидаем ровно один хост.
    return next(iter(hosts.values()))


async def _events(*, search: str | None = None, hosts: list[str] | None = None,
                   limit: int = 30) -> list[dict]:
    """POST /events (+query search) -> rows[]. Пустой фильтр = запрос по умолчанию."""
    body = {
        "acknowledged": "", "exclude_groups": [], "exclude_hosts": [],
        "exclude_tags": [], "exclude_triggers": [], "groups": [],
        "hosts": hosts or [], "limit": limit, "offset": 0,
        "severities": [], "source": [], "tags": [], "time": 10800,
        "triggers": [], "type": "",
    }
    params = {"search": search} if search else None
    data = await _call("dashboard", "POST", "/events", json=body, params=params)
    return data.get("rows") or []


# ── Доменные функции (контракт для dialog.py) ────────────────────────────────
# info    = {"hostid": str, "ip": str, "ci": str, "os": str, "group": str, "stand": str}
# trigger = {"title": str, "severity": "INFO|LOW|MEDIUM|HIGH", "active": bool,
#            "ago_min": int | None, "description": str | None, "runbook": str | None}

def _event_row_to_trigger(row: dict) -> dict:
    sev = int(row.get("severity", 0) or 0)
    ago_min = None
    ts = row.get("timestamp")
    if ts:
        import time
        ago_min = max(0, (int(time.time()) - int(ts)) // 60)
    return {
        "title": row.get("name", ""),
        "severity": _SEV_STR.get(sev, "LOW"),
        "active": True,
        "ago_min": ago_min,
        "description": row.get("comments") or None,
        "runbook": row.get("source_url") or None,
    }


async def _resolve_hostname(ident: str, ident_type: str) -> str | None:
    """hostname — как есть; ip/ci — резолвим через /events search=<ident>."""
    if ident_type == "hostname":
        return ident
    rows = await _events(search=ident, limit=1)
    if not rows:
        return None
    return rows[0].get("host")


async def lookup_host(ident: str, ident_type: str = "hostname") -> tuple[str, dict] | tuple[None, None]:
    """Найти хост по hostname/ip/ci; резолвит стенд автоматически (перебор 4 типов)."""
    hostname = await _resolve_hostname(ident, ident_type)
    if not hostname:
        return None, None

    for zbx_type in _ZABBIX_TYPES:
        try:
            instances = await _get_zabbix_instances(zbx_type)
        except Exception:
            continue
        for inst in instances:
            zbx_name = inst.get("name")
            if not zbx_name:
                continue
            try:
                host = await _report_get_host(zbx_name, hostname)
            except Exception:
                continue
            if host:
                ip = ""
                ifaces = host.get("interfaces") or []
                if ifaces:
                    ip = ifaces[0].get("ip", "")
                info = {
                    "hostid": host.get("hostid", ""),
                    "ip": ip,
                    "ci": "",
                    "os": "",
                    "group": "",
                    "stand": zbx_name,
                }
                return host.get("name", hostname), info

    return None, None


async def get_host_triggers(hostname: str) -> list[dict]:
    """Активные проблемы хоста (POST /events {hosts:[hostname]})."""
    rows = await _events(hosts=[hostname])
    return [_event_row_to_trigger(r) for r in rows]
