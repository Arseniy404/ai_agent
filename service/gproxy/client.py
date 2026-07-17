"""Прокси-слой к Zabbix JSON-RPC API. Единственная задача — детерминированно получать
данные (хосты, триггеры). Никакой интерпретации намерений, никакой LLM здесь нет.

Сохраняем паттерны, которые в старой архитектуре были спроектированы правильно:
кэш токена сессии, авто-релогин при "Not authorized", asyncio.Lock против гонки при
логине, инкрементный id для JSON-RPC запросов.
"""
import asyncio
import itertools

import aiohttp

from service import config


class ZabbixError(Exception):
    pass


class ZabbixClient:
    def __init__(self, url: str, user: str, password: str, timeout: int):
        self._url = url
        self._user = user
        self._password = password
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._rid = itertools.count(1)

    async def _login(self, session: aiohttp.ClientSession) -> str:
        async with self._lock:
            if self._token is not None:
                return self._token
            payload = {
                "jsonrpc": "2.0",
                "method": "user.login",
                "params": {"user": self._user, "password": self._password},
                "id": next(self._rid),
            }
            async with session.post(self._url, json=payload, timeout=self._timeout) as resp:
                data = await resp.json()
            if "error" in data:
                raise ZabbixError(f"Zabbix login failed: {data['error']}")
            self._token = data["result"]
            return self._token

    async def _call(self, session: aiohttp.ClientSession, method: str, params: dict,
                     auth_required: bool = True, _retried: bool = False) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._rid),
        }
        if auth_required:
            token = self._token or await self._login(session)
            payload["auth"] = token

        async with session.post(self._url, json=payload, timeout=self._timeout) as resp:
            data = await resp.json()

        if "error" in data:
            message = data["error"].get("data", "") or data["error"].get("message", "")
            if auth_required and not _retried and "not authorized" in message.lower():
                self._token = None
                return await self._call(session, method, params, auth_required, _retried=True)
            raise ZabbixError(f"Zabbix API error on {method}: {data['error']}")

        return data["result"]

    async def get_hostid(self, session: aiohttp.ClientSession, hostname: str) -> str | None:
        result = await self._call(session, "host.get", {
            "filter": {"host": [hostname]},
            "output": ["hostid", "host"],
        })
        if not result:
            return None
        return result[0]["hostid"]

    async def get_host_info(self, session: aiohttp.ClientSession, hostid: str) -> dict:
        result = await self._call(session, "host.get", {
            "hostids": [hostid],
            "output": ["hostid", "host"],
            "selectInterfaces": ["ip"],
            "selectInventory": ["os"],
        })
        if not result:
            raise ZabbixError(f"Host {hostid} not found")
        host = result[0]
        ip = host["interfaces"][0]["ip"] if host.get("interfaces") else None
        os_name = (host.get("inventory") or {}).get("os")
        return {"name": host["host"], "ip": ip, "os": os_name}

    async def get_triggers(self, session: aiohttp.ClientSession, hostid: str) -> list[dict]:
        result = await self._call(session, "trigger.get", {
            "hostids": [hostid],
            "output": ["triggerid", "description", "priority", "value", "lastchange"],
            "sortfield": "priority",
            "sortorder": "DESC",
        })
        return result


_client: ZabbixClient | None = None


def get_client() -> ZabbixClient:
    global _client
    if _client is None:
        _client = ZabbixClient(
            url=config.ZABBIX_URL,
            user=config.ZABBIX_USER,
            password=config.ZABBIX_PASS,
            timeout=config.ZABBIX_TIMEOUT_SECONDS,
        )
    return _client
