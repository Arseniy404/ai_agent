import asyncio
import datetime
import os
import aiohttp_jinja2
from aiohttp import web
import aiohttp
import json
import base64
import uuid
import re
import time
import ssl
from io import BytesIO
from PIL import Image, ImageDraw

# ── GigaChat config ───────────────────────────────────────────────────────────
_AUTH_URL    = os.getenv("GIGACHAT_AUTH_URL",  "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
_API_URL     = os.getenv("GIGACHAT_API_URL",   "https://gigachat.devices.sberbank.ru/api/v1/chat/completions")
_FILES_URL   = os.getenv("GIGACHAT_FILES_URL", "https://gigachat.devices.sberbank.ru/api/v1/files")
_AUTH_KEY    = os.environ["GIGACHAT_AUTH_KEY"]
_SCOPE       = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
_MODEL       = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Pro")
_EMBED_MODEL = "Embeddings"
_ALLOWED_MODELS = {"GigaChat-2-Max", "GigaChat-2-Pro", "GigaChat-2"}
_VISION_MODELS  = {"GigaChat-2-Max", "GigaChat-2-Pro"}

_token_cache = {"token": None, "expires_at": 0.0}

# ── Zabbix API ────────────────────────────────────────────────────────────────
ZABBIX_URL  = os.environ["ZABBIX_URL"]
ZABBIX_USER = os.getenv("ZABBIX_USER", "Admin")
ZABBIX_PASS = os.environ["ZABBIX_PASS"]

_CTX_HOST_LIST_MAX  = 50   # строк в контексте «список серверов» (остаток — суммаризируется)
_CTX_METRIC_LIST_MAX = 60  # строк в контексте «список метрик»
_SYSTEM_HOSTS_LIMIT = 500  # max хостов в одном запросе группы

# ── Prompts ───────────────────────────────────────────────────────────────────
PROMPT_ANALYZE = """\
Ты — система анализа запросов к мониторингу IT-инфраструктуры.
Проанализируй ТОЛЬКО текущее сообщение пользователя (не историю).

Шаг 1. Найди идентификатор сервера или системы в ТЕКУЩЕМ сообщении:
  hostname — имя хоста (буквы + цифра на конце): gachimuchi1, pookie1, yamcha4
  ip       — IPv4-адрес: 10.10.1.101
  ci       — номер КЭ (начинается с CI): CI0101289001
  system   — название подсистемы или группы серверов (НЕ конкретный хост):
             «биллинг», «авторизация», «фронтенд», «аналитика», «интеграция»,
             «Synthetic/API-шлюзы», «платёжный шлюз» и т.п.
             Используй type=system если речь о группе, окружении или подсистеме.

  НОРМАЛИЗАЦИЯ — обязательно приводи к именительному падежу:
  «авторизацию» → «авторизация», «биллинга» → «биллинг»,
  «фронтендом» → «фронтенд», «платёжного шлюза» → «платёжный шлюз».
  Если идентификатор не найден в текущем сообщении — identifier="" и type=none.
  НЕ восстанавливай идентификатор из истории диалога.

Шаг 2. Определи, хочет ли пользователь ГРАФИК (динамику, тренд, историю показателя):
  wants_graph=true:  «покажи», «нарисуй», «граф», «динамику», «историю», «как менялось»
  wants_graph=false: вопрос о состоянии, «проверь», «расскажи», «что с ...»

Шаг 3. Если wants_graph=true — metric_query: одно английское слово для поиска в Zabbix:
  cpu, disk, memory, network, temperature, load, swap, inode, ping, errors

Шаг 4. Хочет ли пользователь СПИСОК серверов системы:
  wants_server_list=true:  «какие серверы», «состав», «список серверов», «сколько хостов», «перечисли»
  wants_server_list=false: всё остальное

Шаг 5. Хочет ли пользователь СПИСОК МЕТРИК конкретного сервера:
  wants_metric_list=true:  «какие метрики», «что мониторится», «список метрик», «покажи метрики», «что собирается»
  wants_metric_list=false: всё остальное

Вызови функцию analyze_request."""
SYSTEM_PROMPT = """\
Ты — помощник службы мониторинга IT-инфраструктуры.
Общаешься с сотрудниками, которые могут не разбираться в IT.
Отвечаешь по-русски, кратко, без жаргона и аббревиатур без расшифровки.

Ключевые правила поведения:
— Ты не определяешь имя сервера самостоятельно. Идентификатор передан тебе \
  системой в блоке [РЕЗУЛЬТАТ] или [ИНСТРУКЦИЯ] ниже.
— Данные о сервере берёшь только из блока [РЕЗУЛЬТАТ]. Не придумывай и не \
  дополняй информацию из своих знаний.
— Если что-то непонятно из данных — признай это, не угадывай.
— Тон: вежливый, спокойный, как у опытного коллеги из IT-поддержки."""

_CTX_NO_IDENT = """

[ИНСТРУКЦИЯ: ИДЕНТИФИКАТОР НЕ НАЙДЕН]
Система не смогла определить, о каком сервере или системе идёт речь.

Попроси пользователя уточнить. Объясни способы — простыми словами:
1. Кодовое имя сервера — короткое слово с цифрой в конце: pookie1, franklin1.
2. Сетевой адрес — четыре числа через точку: 10.10.10.10.
3. Номер учётной записи оборудования — начинается с CI: CI0101289037.
4. Название системы-группы — если речь о целом подразделении.

Достаточно любого одного варианта. Говори тепло."""

_CTX_NOT_FOUND = """

[РЕЗУЛЬТАТ: ХОСТ НЕ НАЙДЕН]
Идентификатор «{ident}» проверен по базе системы мониторинга.
Результат: такой хост в мониторинге НЕ ЗАРЕГИСТРИРОВАН.

Сообщи пользователю об этом понятно. Возможные причины:
— опечатка в имени/адресе/номере;
— сервер ещё не добавлен в систему мониторинга;
— используется неактуальное имя или адрес.

Предложи проверить написание или обратиться к администратору.
Не придумывай никаких данных о сервере."""

_CTX_FOUND = """

[РЕЗУЛЬТАТ: ХОСТ НАЙДЕН]
Данные получены из системы мониторинга Zabbix.

Сервер:  {name}
IP:      {ip}
КЭ:      {ci}
ОС:      {os}
Система: {group}

Активные проблемы ({n_active} из {n_total} триггеров):
{trigger_lines}

Инструкция по ответу:
— Нормальные триггеры ЗАПРЕЩЕНО перечислять или упоминать по названию. \
  Вместо них — ровно одна фраза: «по другим {n_ok} измерениям отклонений нет».
— Если активных проблем нет — скажи тепло: сервер в порядке, \
  по всем {n_ok} измерениям отклонений нет.
— Если активные проблемы есть — экспертный разбор УЖЕ дан выше в отдельном блоке. \
  Не повторяй технические детали. Дай короткое (2-3 предложения) резюме: \
  насколько серьёзно, что главное сделать первым. В конце добавь фразу про {n_ok} измерений.
— Длина ответа: не больше 3 предложений."""

_CTX_SYSTEM_FOUND = """

[РЕЗУЛЬТАТ: СИСТЕМА НАЙДЕНА]
Данные получены из системы мониторинга Zabbix.

Система:             {group}
Хостов в системе:    {n_hosts}
Хостов с проблемами: {n_problem_hosts}
Активных проблем:    {n_active_total}

{host_lines}

Инструкция по ответу:
— Если активных проблем нет — скажи что система работает нормально, кратко и тепло.
— Если есть проблемы — дай краткое резюме в 2-3 предложениях: сколько хостов затронуто, \
  что самое критичное, насколько срочно.
— Не перечисляй все триггеры — дай общую картину.
— Хосты без проблем не упоминай."""

_CTX_SYSTEM_NOT_FOUND = """

[РЕЗУЛЬТАТ: СИСТЕМА НЕ НАЙДЕНА]
Запрос «{ident}» не совпал ни с одной группой в мониторинге Zabbix.

Доступные группы в мониторинге:
{suggestions}

Предложи пользователю выбрать одну из доступных групп — возможно, он имел в виду одну из них.
Говори тепло, помогая сориентироваться."""

_CTX_HOST_METRICS = """

[РЕЗУЛЬТАТ: МЕТРИКИ СЕРВЕРА]
Данные получены из системы мониторинга Zabbix.

Сервер: {name} ({ip})
Всего метрик: {n_items}

{item_lines}

Инструкция по ответу:
— Выведи список метрик компактно, по одной строке на каждую.
— Формат: название метрики, в скобках единица измерения (если есть).
— Одна вступительная фраза, затем список. Без лишних объяснений."""

_CTX_SYSTEM_HOST_LIST = """

[РЕЗУЛЬТАТ: СПИСОК СЕРВЕРОВ СИСТЕМЫ]
Данные получены из системы мониторинга Zabbix.

Система: {group}
Всего серверов: {n_hosts}

{host_lines}

Инструкция по ответу:
— Выведи список серверов компактно, по одной строке на каждый.
— Для серверов с активными проблемами добавь пометку «⚠ есть проблемы».
— Не перечисляй детали проблем — только факт их наличия.
— Одна вступительная фраза и список. Без пространных объяснений."""

_CTX_SYSTEM_CLARIFY = """

[УТОЧНЕНИЕ: НАЙДЕНО НЕСКОЛЬКО СОВПАДЕНИЙ]
По запросу «{query}» в мониторинге найдено {n} похожих группы:

{candidates}

Попроси пользователя уточнить, какую именно систему он имеет в виду.
Перечисли варианты нумерованным списком. Говори тепло."""

PROMPT_EXPERT = """\
Ты — старший инженер по надёжности (SRE) с опытом разбора инцидентов.
Тебе передан отчёт о состоянии сервера из системы мониторинга.
Твоя задача — провести детальный экспертный разбор активных проблем.

Матрица приоритетов:
  КРИТИЧНО  — сервис недоступен или данные под угрозой. Требует немедленного вмешательства.
  ВЫСОКИЙ   — деградация производительности или риск скорого сбоя. Исправить в течение часа.
  СРЕДНИЙ   — потенциальная проблема, которая усугубится без внимания. Исправить сегодня.
  НИЗКИЙ    — наблюдать, плановая работа.

Правила оценки конкретных событий:
  • Высокая загрузка CPU >90% дольше 30 мин → КРИТИЧНО если продакшн, ВЫСОКИЙ если dev.
  • Диск заполнен → КРИТИЧНО: запись упадёт, БД могут повредиться.
  • Память >85% → СРЕДНИЙ; если одновременно CPU высокий → ВЫСОКИЙ.
  • Порт 443 недоступен → КРИТИЧНО для внешних сервисов, ВЫСОКИЙ для внутренних.
  • Задержка сети >100ms → НИЗКИЙ если единичный всплеск, СРЕДНИЙ если устойчивый.
  • Перезапуск nginx → СРЕДНИЙ; если >3 раз за час → ВЫСОКИЙ.
  • Ошибки в syslog → OOM killer → ВЫСОКИЙ, прочее → НИЗКИЙ.
  • Температура CPU выше нормы → СРЕДНИЙ; выше 85°C → ВЫСОКИЙ.

Формат ответа — строго по каждой активной проблеме:
  **[ПРИОРИТЕТ] Название проблемы**
  Оценка: почему такой приоритет, с учётом длительности и контекста других проблем.
  Первые шаги: конкретные команды или действия, которые нужно выполнить прямо сейчас.
  Ссылка на инструкцию: <если есть в описании триггера>

Если проблем несколько — оцени взаимовлияние.
В конце — одна строка с итоговой рекомендацией: что сделать первым и насколько срочно.
Отвечай по-русски, без вводных фраз."""

PROMPT_EXPERT_SYSTEM = """\
Ты — старший инженер по надёжности (SRE) с опытом разбора инцидентов.
Тебе передан отчёт о состоянии системы (группы серверов) из мониторинга Zabbix.
Твоя задача — провести сводный экспертный разбор активных проблем по всей системе.

Матрица приоритетов:
  КРИТИЧНО  — сервис недоступен или данные под угрозой.
  ВЫСОКИЙ   — деградация производительности или риск скорого сбоя.
  СРЕДНИЙ   — потенциальная проблема, которая усугубится без внимания.
  НИЗКИЙ    — наблюдать, плановая работа.

Формат ответа: для каждого затронутого сервера:
  **[Hostname]**
  — **[ПРИОРИТЕТ] Проблема**: краткая оценка и первые шаги.

В конце — одна строка: что в системе самое критичное, что делать первым.
Если проблемы на разных хостах похожи — отметь возможную системную причину.
Отвечай по-русски, без вводных фраз."""

# ── Zabbix client ─────────────────────────────────────────────────────────────
_SEV_STR = {0: "INFO", 1: "INFO", 2: "LOW", 3: "MEDIUM", 4: "HIGH", 5: "HIGH"}

_zbx_token: str | None = None
_zbx_lock = asyncio.Lock()
_zbx_rid  = 0

async def _zbx_login() -> str:
    async with aiohttp.ClientSession() as sess:
        async with sess.post(ZABBIX_URL, json={
            "jsonrpc": "2.0", "method": "user.login", "id": 0,
            "params": {"username": ZABBIX_USER, "password": ZABBIX_PASS},
        }, timeout=aiohttp.ClientTimeout(total=10)) as r:
            return (await r.json())["result"]

async def _zbx(method: str, **params):
    global _zbx_token, _zbx_rid
    async with _zbx_lock:
        if _zbx_token is None:
            _zbx_token = await _zbx_login()
    _zbx_rid += 1
    payload = {"jsonrpc": "2.0", "method": method, "params": params,
               "auth": _zbx_token, "id": _zbx_rid}
    async with aiohttp.ClientSession() as sess:
        async with sess.post(ZABBIX_URL, json=payload,
                             timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
    if "error" in data:
        if "Not authorized" in str(data["error"]):
            async with _zbx_lock:
                _zbx_token = None
            return await _zbx(method, **params)
        raise RuntimeError(f"Zabbix {method}: {data['error']}")
    return data["result"]

def _norm_host(h: dict) -> tuple[str, dict]:
    """Zabbix host object → (hostname, info_dict)."""
    name   = h["host"]
    ip     = next((i["ip"] for i in h.get("interfaces", []) if i.get("main") == "1"), "")
    inv    = h.get("inventory") or {}
    groups = [g["name"] for g in h.get("groups", [])]
    group  = groups[0].split("/")[-1] if groups else ""
    return name, {
        "hostid": h["hostid"],
        "ip":     ip,
        "ci":     inv.get("alias", ""),
        "os":     inv.get("os", ""),
        "group":  group,
    }

_HOST_SELECT = dict(
    output=["hostid", "host"],
    selectInterfaces=["ip", "main"],
    selectInventory=["alias", "os"],
    selectGroups=["name"],
)

async def _zabbix_lookup(ident: str) -> tuple[str, dict] | tuple[None, None]:
    res = await _zbx("host.get", filter={"host": [ident]}, **_HOST_SELECT)
    if res:
        return _norm_host(res[0])
    ifaces = await _zbx("hostinterface.get", filter={"ip": [ident]}, output=["hostid"])
    if ifaces:
        res = await _zbx("host.get", hostids=[ifaces[0]["hostid"]], **_HOST_SELECT)
        if res:
            return _norm_host(res[0])
    res = await _zbx("host.get", searchInventory={"alias": ident}, **_HOST_SELECT)
    if res:
        return _norm_host(res[0])
    return None, None

async def _zabbix_lookup_smart(ident: str) -> tuple[str, dict, bool]:
    name, info = await _zabbix_lookup(ident)
    if info is not None:
        return name, info, False
    res = await _zbx("host.get", search={"host": f"*{ident}*"},
                     searchWildcardsEnabled=True, limit=1, **_HOST_SELECT)
    if res:
        name, info = _norm_host(res[0])
        return name, info, True
    return None, None, False

async def _zabbix_get_triggers(hostid: str) -> list[dict]:
    now   = int(time.time())
    trigs = await _zbx("trigger.get",
                        hostids=[hostid],
                        output=["description", "priority", "value", "lastchange", "comments", "url"],
                        skipDependent=1,
                        sortfield="priority", sortorder="DESC")
    result = []
    for t in trigs:
        pri    = int(t["priority"])
        active = t["value"] == "1"
        lc     = int(t["lastchange"])
        result.append({
            "title":       t["description"],
            "severity":    _SEV_STR.get(pri, "LOW"),
            "active":      active,
            "ago_min":     (now - lc) // 60 if active and lc > 0 else None,
            "description": t.get("comments") or None,
            "runbook":     t.get("url") or None,
        })
    return result

async def _zabbix_search_items(hostid: str, query: str) -> list[dict]:
    """Search numeric items on a host by name substring (case-insensitive)."""
    q = query.replace("_", " ")
    items = await _zbx("item.get",
                        hostids=[hostid],
                        search={"name": f"*{q}*"},
                        searchWildcardsEnabled=True,
                        output=["itemid", "name", "key_", "units", "value_type"],
                        limit=8)
    return [i for i in items if int(i["value_type"]) in (0, 3)]

async def _llm_pick_metric(user_msg: str, items: list[dict], model: str) -> list[dict]:
    """Select metric(s) from a list using LLM. Returns up to 4 matching items."""
    if not items:
        return []
    lines = []
    for i, it in enumerate(items, 1):
        u = f" ({it['units']})" if it.get("units") else ""
        lines.append(f"{i}. {it['name']}{u}")
    prompt = (
        "Выбери метрику из списка по запросу пользователя.\n"
        f"Запрос: «{user_msg}»\n\n"
        "Список метрик:\n" + "\n".join(lines) + "\n\n"
        "Ответь ТОЛЬКО одним или несколькими номерами через запятую.\n"
        "Если несколько метрик подходят одинаково — перечисли все.\n"
        "Если ни одна не подходит — ответь: 0"
    )
    try:
        raw = await _gigachat([{"role": "system", "content": prompt}], model)
        nums = [int(x) for x in re.findall(r'\d+', raw)]
        return [items[n - 1] for n in nums if 1 <= n <= len(items)][:4]
    except Exception:
        return []

async def _zabbix_get_item_history_timed(
    itemid: str, value_type: int, hours: int = 3,
) -> list[tuple[float, int]]:
    now = int(time.time())
    hist = await _zbx("history.get",
                       itemids=[itemid], history=value_type,
                       time_from=now - hours * 3600, time_till=now,
                       sortfield="clock", sortorder="ASC", limit=500)
    return [(float(h["value"]), int(h["clock"])) for h in hist]

async def _zabbix_search_groups(query: str) -> list[dict]:
    """Search Zabbix host groups by name. Returns list of {groupid, name}, max 5."""
    exact = await _zbx("hostgroup.get",
                        filter={"name": [query]}, output=["groupid", "name"])
    if exact:
        return exact
    res = await _zbx("hostgroup.get",
                      search={"name": query},
                      searchWildcardsEnabled=True,
                      output=["groupid", "name"], limit=5)
    return res

async def _zabbix_all_group_names() -> list[str]:
    """Return names of all host groups that contain monitored hosts."""
    res = await _zbx("hostgroup.get", output=["name"], with_monitored_hosts=True)
    return sorted(g["name"] for g in res)

async def _zabbix_get_all_items(hostid: str) -> list[dict]:
    """Return all enabled items on a host, sorted by name."""
    return await _zbx("item.get",
                      hostids=[hostid],
                      output=["itemid", "name", "units", "value_type"],
                      filter={"status": "0"},
                      sortfield="name", sortorder="ASC",
                      limit=200)

async def _zabbix_get_system_data(group_id: str) -> list[dict]:
    """Return hosts with their active triggers for a group (by groupid), up to _SYSTEM_HOSTS_LIMIT."""
    hosts = await _zbx("host.get", groupids=[group_id], limit=_SYSTEM_HOSTS_LIMIT, **_HOST_SELECT)
    if not hosts:
        return []

    host_ids = [h["hostid"] for h in hosts]
    now = int(time.time())
    active_trigs = await _zbx(
        "trigger.get",
        hostids=host_ids,
        output=["description", "priority", "lastchange", "comments", "url"],
        selectHosts=["hostid"],
        skipDependent=1,
        filter={"value": "1"},
        sortfield="priority", sortorder="DESC",
    )

    trig_by_host: dict[str, list] = {}
    for t in active_trigs:
        pri  = int(t["priority"])
        lc   = int(t["lastchange"])
        trec = {
            "title":       t["description"],
            "severity":    _SEV_STR.get(pri, "LOW"),
            "active":      True,
            "ago_min":     (now - lc) // 60 if lc > 0 else None,
            "description": t.get("comments") or None,
            "runbook":     t.get("url") or None,
        }
        for h in t.get("hosts", []):
            trig_by_host.setdefault(h["hostid"], []).append(trec)

    result = []
    for h in hosts:
        hname, hinfo = _norm_host(h)
        result.append({
            "name":     hname,
            "info":     hinfo,
            "triggers": trig_by_host.get(h["hostid"], []),
        })
    return result

_ORDINAL_MAP: dict[str, int] = {
    'первый': 0, 'первую': 0, 'первое': 0, 'первая': 0, '1': 0,
    'второй': 1, 'вторую': 1, 'второе': 1, 'вторая': 1, '2': 1,
    'третий': 2, 'третью': 2, 'третье': 2, 'третья': 2, '3': 2,
    'четвёртый': 3, 'четвертый': 3, '4': 3,
    'пятый': 4, '5': 4,
    'шестой': 5, '6': 5,
    'седьмой': 6, '7': 6,
    'восьмой': 7, '8': 7,
}

def _resolve_graph_candidate(msg: str, candidates: list[dict]) -> dict | None:
    """Resolve a graph item candidate by ordinal or name substring."""
    msg_l = msg.lower()
    for word, idx in _ORDINAL_MAP.items():
        if re.search(r'\b' + re.escape(word) + r'\b', msg_l) and idx < len(candidates):
            return candidates[idx]
    for c in candidates:
        name_l = c['name'].lower()
        if name_l in msg_l or msg_l in name_l:
            return c
    words = [w for w in msg_l.split() if len(w) > 2]
    for c in candidates:
        name_l = c['name'].lower()
        if any(w in name_l for w in words):
            return c
    return None

def _make_item_chart(title: str, unit: str, points: list[tuple[float, int]]) -> str:
    """Render a time-series chart for an arbitrary Zabbix item. Returns base64 PNG."""
    if len(points) < 2:
        return ""

    values = [p[0] for p in points]
    clocks = [p[1] for p in points]

    W, H = 720, 260
    PAD_L, PAD_R, PAD_T, PAD_B = 72, 24, 32, 36
    CW = W - PAD_L - PAD_R
    CH = H - PAD_T - PAD_B

    BG       = (20, 22, 34)
    GRID     = (45, 48, 65)
    LINE_COL = (166, 227, 161)
    FILL_COL = (166, 227, 161, 50)
    TEXT_COL = (160, 165, 195)
    TITLE_C  = (210, 215, 235)

    v_min = min(values)
    v_max = max(values)
    if v_max == v_min:
        v_max = v_min + max(1.0, abs(v_min) * 0.1 + 1)
    v_pad   = (v_max - v_min) * 0.12
    y_min   = max(0.0, v_min - v_pad) if v_min >= 0 else v_min - v_pad
    y_max   = v_max + v_pad
    y_range = y_max - y_min

    img  = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(img)

    # Y gridlines (6 marks)
    for i in range(6):
        y   = PAD_T + i * CH // 5
        val = y_max - i * y_range / 5
        draw.line([(PAD_L, y), (W - PAD_R, y)], fill=GRID, width=1)
        lbl = f"{val:.0f}" if abs(val) >= 10 else f"{val:.2f}"
        draw.text((2, y - 7), lbl, fill=TEXT_COL)

    # X gridlines (every 30 min)
    t0   = clocks[0]
    t1   = clocks[-1]
    span = max(t1 - t0, 1)
    for step_min in range(0, 181, 30):
        tc = t0 + step_min * 60
        if tc > t1 + 120:
            break
        frac = min(1.0, (tc - t0) / span)
        x    = PAD_L + int(frac * CW)
        draw.line([(x, PAD_T), (x, H - PAD_B)], fill=GRID, width=1)
        label = datetime.datetime.utcfromtimestamp(tc).strftime('%H:%M')
        draw.text((x - 14, H - PAD_B + 4), label, fill=TEXT_COL)

    def to_xy(i: int) -> tuple[int, int]:
        frac = (clocks[i] - t0) / span
        x = PAD_L + int(frac * CW)
        y = PAD_T + CH - int((values[i] - y_min) / y_range * CH)
        return x, max(PAD_T, min(PAD_T + CH, y))

    # Fill polygon
    last_x = PAD_L + int((clocks[-1] - t0) / span * CW)
    poly = [(PAD_L, H - PAD_B)] + [to_xy(i) for i in range(len(values))] + [(last_x, H - PAD_B)]
    draw.polygon(poly, fill=FILL_COL)

    # Line
    line_pts = [to_xy(i) for i in range(len(values))]
    for i in range(len(line_pts) - 1):
        draw.line([line_pts[i], line_pts[i + 1]], fill=LINE_COL, width=2)

    # Title bar
    avg = sum(values) / len(values)
    mx  = max(values)
    u   = f" {unit}" if unit else ""

    def fmt(v: float) -> str:
        return f"{v:.0f}" if abs(v) >= 10 else f"{v:.2f}"

    draw.text((PAD_L, 8), f"{title}   avg {fmt(avg)}{u}   max {fmt(mx)}{u}", fill=TITLE_C)

    rgb = img.convert("RGB")
    buf = BytesIO()
    rgb.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()

# ── GigaChat client ───────────────────────────────────────────────────────────
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

async def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {_AUTH_KEY}",
    }
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            _AUTH_URL, data=f"scope={_SCOPE}", headers=headers,
            ssl=_ssl_ctx, timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = data.get("expires_at", now + 1800) / 1000
    return _token_cache["token"]

async def _gigachat(messages: list[dict], model: str = _MODEL) -> str:
    if model not in _ALLOWED_MODELS:
        model = _MODEL
    token = await _get_token()
    payload = {"model": model, "messages": messages, "profanity_check": True}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            _API_URL, json=payload, headers=headers, ssl=_ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return data["choices"][0]["message"]["content"]

async def _gigachat_fn_call(
    messages: list[dict],
    functions: list[dict],
    model: str = _MODEL,
    force_fn: str | None = None,
) -> tuple[str, dict | None]:
    if model not in _ALLOWED_MODELS:
        model = _MODEL
    token = await _get_token()
    payload: dict = {
        "model": model,
        "messages": messages,
        "functions": functions,
        "profanity_check": True,
    }
    payload["function_call"] = {"name": force_fn} if force_fn else "auto"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            _API_URL, json=payload, headers=headers, ssl=_ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    msg = data["choices"][0]["message"]
    return msg.get("content", ""), msg.get("function_call")

async def _gigachat_stream(messages: list[dict], model: str = _MODEL):
    if model not in _ALLOWED_MODELS:
        model = _MODEL
    token = await _get_token()
    payload = {"model": model, "messages": messages, "stream": True, "profanity_check": True}
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            _API_URL, json=payload, headers=headers, ssl=_ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            resp.raise_for_status()
            async for raw in resp.content:
                line = raw.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                payload_str = line[6:]
                if payload_str == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload_str)
                    content = chunk["choices"][0]["delta"].get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass

# ── Function calling + LLM analysis ─────────────────────────────────────────
_ANALYZE_FUNCTION = {
    "name": "analyze_request",
    "description": (
        "Анализ запроса пользователя: извлечь идентификатор сервера/системы "
        "и определить намерение посмотреть график метрики."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "identifier": {
                "type": "string",
                "description": "Значение идентификатора. Пустая строка если не найден.",
            },
            "type": {
                "type": "string",
                "enum": ["hostname", "ip", "ci", "system", "none"],
                "description": "Тип идентификатора.",
            },
            "wants_graph": {
                "type": "boolean",
                "description": "True если пользователь хочет увидеть график/динамику/историю метрики.",
            },
            "metric_query": {
                "type": "string",
                "description": "Английское слово для поиска метрики в Zabbix (cpu, disk, memory, network, temperature, load, swap, inode). Пустая строка если wants_graph=false.",
            },
            "wants_server_list": {
                "type": "boolean",
                "description": "True если пользователь просит показать список/состав серверов системы.",
            },
            "wants_metric_list": {
                "type": "boolean",
                "description": "True если пользователь просит показать список метрик/показателей конкретного сервера.",
            },
        },
        "required": ["identifier", "type", "wants_graph", "metric_query", "wants_server_list", "wants_metric_list"],
    },
    "return_parameters": {
        "type": "object",
        "properties": {"found": {"type": "boolean"}},
    },
}

_PROMPT_IDENT_RETRY = (
    "Извлеки из сообщения имя сервера или системы. Нормализуй к именительному падежу.\n"
    'Ответь строго JSON одной строкой: {"identifier": "...", "type": "hostname|ip|ci|system|none"}\n'
    'Если не найдено: {"identifier": "", "type": "none"}'
)

async def _llm_analyze_request(
    user_msg: str,
    history: list[dict],
    model: str,
    custom_prompt: str = "",
) -> tuple[str | None, str | None, str | None, bool, bool]:
    """Analyze request. Returns (identifier, ident_type, graph_query, wants_server_list, wants_metric_list)."""
    prompt = custom_prompt.strip() or PROMPT_ANALYZE
    messages = [{"role": "system", "content": prompt}]
    for h in history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    def _parse(args: dict) -> tuple[str | None, str | None, str | None, bool, bool]:
        ident      = (args.get("identifier") or "").strip() or None
        ident_type = args.get("type", "none")
        if ident_type == "none":
            ident = None
            ident_type = None
        wants_graph       = bool(args.get("wants_graph", False))
        metric_query      = (args.get("metric_query") or "").strip() or None
        wants_server_list = bool(args.get("wants_server_list", False))
        wants_metric_list = bool(args.get("wants_metric_list", False))
        return ident, ident_type, (metric_query if wants_graph else None), wants_server_list, wants_metric_list

    # Attempt 1: combined function call
    try:
        _, fn_call = await _gigachat_fn_call(
            messages, [_ANALYZE_FUNCTION], model, force_fn="analyze_request"
        )
        if fn_call:
            result = _parse(json.loads(fn_call.get("arguments", "{}")))
            if result[0] is not None or result[2] is not None:
                return result
    except Exception:
        pass

    # Attempt 2: JSON fallback for combined call
    json_suffix = (
        "\n\nВерни ТОЛЬКО JSON-объект:\n"
        '{"identifier": "<значение или пустая строка>", "type": "hostname|ip|ci|system|none", '
        '"wants_graph": true/false, "metric_query": "<английское слово или пустая строка>", '
        '"wants_server_list": true/false, "wants_metric_list": true/false}'
    )
    messages[0] = {"role": "system", "content": prompt + json_suffix}
    try:
        raw = await _gigachat(messages, model)
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if m:
            result = _parse(json.loads(m.group()))
            if result[0] is not None or result[2] is not None:
                return result
    except Exception:
        pass

    # Attempt 3: focused identifier-only retry (simpler prompt, no history)
    try:
        raw = await _gigachat(
            [{"role": "system", "content": _PROMPT_IDENT_RETRY},
             {"role": "user",   "content": user_msg}],
            model,
        )
        m = re.search(r'\{[^{}]+\}', raw, re.DOTALL)
        if m:
            data  = json.loads(m.group())
            ident = (data.get("identifier") or "").strip() or None
            itype = data.get("type", "none")
            if itype != "none" and ident:
                return ident, itype, None, False, False
    except Exception:
        pass

    return None, None, None, False, False


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_format(template: str, **kwargs) -> str:
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError, IndexError):
        result = template
        for k, v in kwargs.items():
            result = result.replace('{' + k + '}', str(v))
        return result

def _build_expert_ctx(found_name, found_info, active_triggers) -> str:
    return (
        f"Сервер: {found_name} | IP: {found_info['ip']} | КЭ: {found_info['ci']}\n"
        f"ОС: {found_info['os']} | Система: {found_info['group']}\n\n"
        "Активные триггеры:\n" + "\n".join(
            f"  [{t['severity']}] {t['title']}"
            + (f" (активен {t['ago_min']} мин)" if t['ago_min'] else "")
            for t in active_triggers
        )
    )


def _build_system_expert_ctx(group_name: str, hosts_data: list[dict]) -> str:
    lines = [f"Система: {group_name}\n"]
    for h in hosts_data:
        if h["triggers"]:
            lines.append(f"Сервер: {h['name']} | IP: {h['info']['ip']}")
            for t in h["triggers"]:
                ago = f" (активен {t['ago_min']} мин)" if t["ago_min"] else ""
                lines.append(f"  [{t['severity']}] {t['title']}{ago}")
            lines.append("")
    return "\n".join(lines)

# ── Views ─────────────────────────────────────────────────────────────────────
async def graph_item(request):
    """Render a graph for a specific Zabbix item by itemid, bypassing LLM."""
    body       = await request.json()
    itemid     = body.get("itemid")
    value_type = int(body.get("value_type", 0))
    name       = body.get("name", "")
    unit       = body.get("unit", "")
    if not itemid:
        return web.json_response({"error": "no itemid"}, status=400)
    try:
        pts   = await _zabbix_get_item_history_timed(itemid, value_type)
        chart = _make_item_chart(name, unit, pts)
        return web.json_response({"chart": chart, "title": name, "unit": unit, "has_data": bool(pts)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@aiohttp_jinja2.template("index.html")
async def index(request):
    return {}

async def get_prompt(request):
    return web.json_response({
        "extract":          PROMPT_ANALYZE,
        "base":             SYSTEM_PROMPT,
        "no_ident":         _CTX_NO_IDENT,
        "not_found":        _CTX_NOT_FOUND,
        "found":            _CTX_FOUND,
        "expert":           PROMPT_EXPERT,
        "host_metrics":     _CTX_HOST_METRICS,
        "system_found":     _CTX_SYSTEM_FOUND,
        "system_host_list": _CTX_SYSTEM_HOST_LIST,
        "system_nf":        _CTX_SYSTEM_NOT_FOUND,
        "system_clarify":   _CTX_SYSTEM_CLARIFY,
        "expert_system":    PROMPT_EXPERT_SYSTEM,
    })

async def upload_file(request):
    try:
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            return web.json_response({"error": "expected field 'file'"}, status=400)
        filename = field.filename or "upload.bin"
        data = await field.read()

        token = await _get_token()
        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename, content_type="application/octet-stream")
        form.add_field("purpose", "general")

        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                _FILES_URL, data=form,
                headers={"Authorization": f"Bearer {token}"},
                ssl=_ssl_ctx, timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()

        return web.json_response({
            "file_id":  result["id"],
            "filename": result.get("filename", filename),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def chat(request):
    body        = await request.json()
    user_msg    = body.get("message", "").strip()
    history     = body.get("history", [])
    prev_server          = body.get("server_name")
    prev_system          = body.get("system_name")
    prev_candidates      = body.get("system_candidates", [])
    prev_graph_candidates = body.get("prev_graph_candidates", [])
    prev_graph_hostid     = body.get("prev_graph_hostid")
    model       = body.get("model", _MODEL)
    want_expert = bool(body.get("want_expert"))
    file_ids    = body.get("file_ids", [])

    if not user_msg:
        return web.json_response({"error": "empty message"}, status=400)

    resp = web.StreamResponse(headers={
        "Content-Type":      "text/event-stream",
        "Cache-Control":     "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)

    async def sse(event_type: str, data: dict):
        payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
        await resp.write(f"data: {payload}\n\n".encode("utf-8"))

    prompts          = body.get("prompts", {})
    p_analyze          = prompts.get("extract",           "").strip() or PROMPT_ANALYZE
    p_base             = prompts.get("base",              "").strip() or SYSTEM_PROMPT
    p_no_ident         = prompts.get("no_ident",          "").strip() or _CTX_NO_IDENT
    p_not_found        = prompts.get("not_found",         "").strip() or _CTX_NOT_FOUND
    p_found            = prompts.get("found",             "").strip() or _CTX_FOUND
    p_expert           = prompts.get("expert",            "").strip() or PROMPT_EXPERT
    p_host_metrics     = prompts.get("host_metrics",      "").strip() or _CTX_HOST_METRICS
    p_system_found     = prompts.get("system_found",      "").strip() or _CTX_SYSTEM_FOUND
    p_system_host_list = prompts.get("system_host_list",  "").strip() or _CTX_SYSTEM_HOST_LIST
    p_system_nf        = prompts.get("system_nf",         "").strip() or _CTX_SYSTEM_NOT_FOUND
    p_system_clarify   = prompts.get("system_clarify",    "").strip() or _CTX_SYSTEM_CLARIFY
    p_expert_system    = prompts.get("expert_system",     "").strip() or PROMPT_EXPERT_SYSTEM

    try:
        # ── Fast path: on-demand expert for server ────────────────────────────
        if want_expert and prev_server:
            found_name, found_info = await _zabbix_lookup(prev_server)
            if not found_info:
                await sse("expert_chunk", {"text": "Сервер не найден в мониторинге."})
                await sse("done", {})
                return resp
            triggers = await _zabbix_get_triggers(found_info["hostid"])
            active   = [t for t in triggers if t["active"]]
            if not active:
                await sse("expert_chunk", {"text": "Активных триггеров нет — анализировать нечего."})
                await sse("done", {})
                return resp
            expert_msgs = [
                {"role": "system", "content": p_expert},
                {"role": "user",   "content": _build_expert_ctx(found_name, found_info, active)},
            ]
            try:
                async for chunk in _gigachat_stream(expert_msgs, model):
                    await sse("expert_chunk", {"text": chunk})
            except Exception as e:
                await sse("expert_chunk", {"text": f"⚠️ Ошибка GigaChat: {e}"})
            await sse("done", {})
            return resp

        # ── Fast path: on-demand expert for system ────────────────────────────
        if want_expert and prev_system:
            groups = await _zabbix_search_groups(prev_system)
            if not groups:
                await sse("expert_chunk", {"text": "Система не найдена в мониторинге."})
                await sse("done", {})
                return resp
            g = groups[0]
            hosts_data = await _zabbix_get_system_data(g["groupid"])
            problem_hosts = [h for h in hosts_data if h["triggers"]]
            if not problem_hosts:
                await sse("expert_chunk", {"text": "Активных проблем в системе нет."})
                await sse("done", {})
                return resp
            expert_msgs = [
                {"role": "system", "content": p_expert_system},
                {"role": "user",   "content": _build_system_expert_ctx(g["name"], hosts_data)},
            ]
            try:
                async for chunk in _gigachat_stream(expert_msgs, model):
                    await sse("expert_chunk", {"text": chunk})
            except Exception as e:
                await sse("expert_chunk", {"text": f"⚠️ Ошибка GigaChat: {e}"})
            await sse("done", {})
            return resp

        # ── Graph candidate resolution (before ident extraction) ─────────────
        if prev_graph_candidates and prev_graph_hostid:
            selected = _resolve_graph_candidate(user_msg, prev_graph_candidates)
            if selected:
                pts   = await _zabbix_get_item_history_timed(
                    selected["itemid"], int(selected["value_type"]))
                chart = _make_item_chart(selected["name"], selected.get("units", ""), pts)
                await sse("meta", {
                    "server_name": prev_server, "server_info": None,
                    "system_name": None, "system_hosts": [], "triggers": [],
                    "n_ok": 0, "chart": None, "fuzzy_match": False, "fuzzy_query": None,
                    "graph_candidates": None, "graph_hostid": prev_graph_hostid,
                })
                if chart:
                    await sse("graph", {
                        "chart": chart,
                        "title": selected["name"],
                        "unit":  selected.get("units", ""),
                    })
                    reply = f"Вот история метрики «{selected['name']}» за последние 3 часа."
                    if not pts:
                        reply = f"Метрика «{selected['name']}» есть в мониторинге, но данных за 3 часа пока нет."
                else:
                    reply = f"Данных для метрики «{selected['name']}» нет."
                await sse("chunk", {"text": reply})
                await sse("done", {"reply": reply})
                return resp

        # ── Phase 1: analyze request (identifier + graph intent) ─────────────
        ident, ident_type, graph_query, wants_server_list, wants_metric_list = await _llm_analyze_request(
            user_msg, history, model, p_analyze)
        fresh_ident = ident is not None

        if not ident and prev_server:
            ident, ident_type = prev_server, "hostname"
        if not ident and prev_system:
            ident, ident_type = prev_system, "system"

        # Resolve ident against candidates from previous clarification turn
        ident_from_candidates = False
        if prev_candidates and ident:
            ident_l = ident.lower()
            matched = next(
                (c for c in prev_candidates
                 if ident_l in c.lower() or c.lower() in ident_l),
                None,
            )
            if matched:
                ident, ident_type = matched, "system"
                ident_from_candidates = True

        # ── Fast path: no fresh ident + no intent → conversational ──────────
        if not fresh_ident and not ident_from_candidates and not graph_query \
                and not wants_server_list and not wants_metric_list:
            if prev_system:
                ctx_block = (
                    f"\n[КОНТЕКСТ ДИАЛОГА]\n"
                    f"Последняя обсуждавшаяся система: \u00ab{prev_system}\u00bb.\n"
                    "Отвечай на вопрос в этом контексте.\n"
                    "Если пользователь явно спрашивает о ДРУГОЙ системе — попроси назвать её."
                )
            elif prev_server:
                ctx_block = (
                    f"\n[КОНТЕКСТ ДИАЛОГА]\n"
                    f"Последний обсуждавшийся сервер: \u00ab{prev_server}\u00bb.\n"
                    "Отвечай на вопрос в этом контексте.\n"
                    "Если пользователь явно спрашивает о ДРУГОМ сервере — попроси назвать его."
                )
            else:
                ctx_block = p_no_ident
            conv_messages = [{"role": "system", "content": p_base + ctx_block}]
            for h in history[-8:]:
                conv_messages.append({"role": h["role"], "content": h["content"]})
            conv_user: dict = {"role": "user", "content": user_msg}
            if file_ids and model in _VISION_MODELS:
                conv_user["attachments"] = [{"type": "image", "id": fid} for fid in file_ids]
            conv_messages.append(conv_user)
            reply_parts: list[str] = []
            try:
                async for chunk in _gigachat_stream(conv_messages, model):
                    reply_parts.append(chunk)
                    await sse("chunk", {"text": chunk})
            except Exception as e:
                err = f"\u26a0\ufe0f Ошибка GigaChat: {e}"
                reply_parts.append(err)
                await sse("chunk", {"text": err})
            await sse("done", {"reply": "".join(reply_parts)})
            return resp
        # ── Phase 2a: system lookup ───────────────────────────────────────────
        if ident and ident_type == "system":
            groups = await _zabbix_search_groups(ident)

            if not groups:
                all_names = await _zabbix_all_group_names()
                suggestions = "\n".join(f"— {n}" for n in all_names) if all_names \
                    else "— (в мониторинге нет ни одной группы с хостами)"
                ctx_block = _safe_format(p_system_nf, ident=ident, suggestions=suggestions)
                await sse("meta", {
                    "server_name": None, "server_info": None,
                    "system_name": None, "system_hosts": [],
                    "triggers": [], "chart": None,
                    "fuzzy_match": False, "fuzzy_query": None,
                    "system_not_found": True,
                    "system_candidates": all_names,
                    "graph_candidates": None, "graph_hostid": None,
                })

            elif len(groups) > 1:
                # Multiple candidates — ask user to clarify
                candidates = "\n".join(f"{i+1}. {g['name']}" for i, g in enumerate(groups))
                ctx_block = _safe_format(
                    p_system_clarify,
                    query=ident, n=len(groups), candidates=candidates,
                )
                await sse("meta", {
                    "server_name": None, "server_info": None,
                    "system_name": None, "system_hosts": [],
                    "triggers": [], "chart": None,
                    "fuzzy_match": False, "fuzzy_query": None,
                    "system_not_found": False,
                    "system_candidates": [g["name"] for g in groups],
                    "graph_candidates": None, "graph_hostid": None,
                })

            else:
                # Single match — proceed
                g = groups[0]
                actual_group = g["name"]
                hosts_data   = await _zabbix_get_system_data(g["groupid"])

                problem_hosts   = [h for h in hosts_data if h["triggers"]]
                n_problem_hosts = len(problem_hosts)
                all_triggers    = [
                    {**t, "host_name": h["name"]}
                    for h in hosts_data for t in h["triggers"]
                ]
                n_active_total  = len(all_triggers)

                if wants_server_list:
                    display = hosts_data[:_CTX_HOST_LIST_MAX]
                    tail    = len(hosts_data) - len(display)
                    all_host_lines = []
                    for h in display:
                        flag = "  ⚠ есть проблемы" if h["triggers"] else ""
                        all_host_lines.append(f"  {h['name']} ({h['info']['ip']}){flag}")
                    if tail:
                        all_host_lines.append(f"  ... и ещё {tail} серверов (показаны первые {_CTX_HOST_LIST_MAX})")
                    ctx_block = _safe_format(
                        p_system_host_list,
                        group=actual_group,
                        n_hosts=len(hosts_data),
                        host_lines="\n".join(all_host_lines),
                    )
                else:
                    host_lines = []
                    for h in problem_hosts:
                        host_lines.append(f"  {h['name']} ({h['info']['ip']}):")
                        for t in h["triggers"]:
                            ago = f" ({t['ago_min']} мин)" if t["ago_min"] else ""
                            host_lines.append(f"    🔴 [{t['severity']}] {t['title']}{ago}")

                    ctx_block = _safe_format(
                        p_system_found,
                        group=actual_group,
                        n_hosts=len(hosts_data),
                        n_problem_hosts=n_problem_hosts,
                        n_active_total=n_active_total,
                        host_lines="\n".join(host_lines) if host_lines else "  Активных проблем нет.",
                    )

                await sse("meta", {
                    "server_name": None, "server_info": None,
                    "system_name": actual_group,
                    "system_hosts": hosts_data,
                    "triggers":    all_triggers,
                    "chart":       None,
                    "fuzzy_match": False, "fuzzy_query": None,
                    "graph_candidates": None, "graph_hostid": None,
                })

                if problem_hosts and fresh_ident and not wants_server_list:
                    expert_msgs = [
                        {"role": "system", "content": p_expert_system},
                        {"role": "user",   "content": _build_system_expert_ctx(actual_group, hosts_data)},
                    ]
                    try:
                        async for chunk in _gigachat_stream(expert_msgs, model):
                            await sse("expert_chunk", {"text": chunk})
                    except Exception as e:
                        await sse("expert_chunk", {"text": f"⚠️ Ошибка анализа: {e}"})

            messages = [{"role": "system", "content": p_base + ctx_block}]
            for h in history[-8:]:
                messages.append({"role": h["role"], "content": h["content"]})
            messages.append({"role": "user", "content": user_msg})

            reply_parts: list[str] = []
            try:
                async for chunk in _gigachat_stream(messages, model):
                    reply_parts.append(chunk)
                    await sse("chunk", {"text": chunk})
            except Exception as e:
                err = f"⚠️ Ошибка GigaChat: {e}"
                reply_parts.append(err)
                await sse("chunk", {"text": err})

            await sse("done", {"reply": "".join(reply_parts)})
            return resp

        # ── Phase 2b: server lookup (exact → wildcard fallback) ───────────────
        found_name = found_info = None
        triggers = active_triggers = []
        fuzzy_match = False

        if ident:
            found_name, found_info, fuzzy_match = await _zabbix_lookup_smart(ident)

        # If graph/metric intent but ident resolved to nothing, reuse prev_server
        if (graph_query or wants_metric_list) and not found_info \
                and prev_server and prev_server.lower() != (ident or "").lower():
            found_name, found_info, _ = await _zabbix_lookup_smart(prev_server)
            fuzzy_match = False

        # ── Graph intent path ─────────────────────────────────────────────────
        effective_hostid = (found_info or {}).get("hostid") or prev_graph_hostid
        effective_name   = found_name or prev_server

        if graph_query and effective_hostid:
            all_numeric = [i for i in await _zabbix_get_all_items(effective_hostid)
                           if int(i.get("value_type", -1)) in (0, 3)]
            items = await _llm_pick_metric(user_msg, all_numeric, model)

            _base_meta = {
                "server_name": effective_name, "server_info": found_info,
                "system_name": None, "system_hosts": [], "triggers": [],
                "n_ok": 0, "chart": None,
                "fuzzy_match": fuzzy_match, "fuzzy_query": ident if fuzzy_match else None,
                "graph_hostid": effective_hostid,
            }

            if not items:
                await sse("meta", {**_base_meta, "graph_candidates": None})
                reply = (f"На хосте {effective_name} не нашёл метрик по запросу «{graph_query}». "
                         f"Попробуйте уточнить название метрики.")
                await sse("chunk", {"text": reply})
                await sse("done", {"reply": reply})
                return resp

            if len(items) == 1:
                item  = items[0]
                pts   = await _zabbix_get_item_history_timed(item["itemid"], int(item["value_type"]))
                chart = _make_item_chart(item["name"], item.get("units", ""), pts)
                await sse("meta", {**_base_meta, "graph_candidates": None})
                if chart:
                    await sse("graph", {
                        "chart": chart,
                        "title": item["name"],
                        "unit":  item.get("units", ""),
                    })
                    reply = f"Вот история метрики «{item['name']}» за последние 3 часа."
                    if not pts:
                        reply = f"Метрика «{item['name']}» есть, но данных за 3 часа пока нет."
                else:
                    reply = f"Данных для «{item['name']}» нет."
                await sse("chunk", {"text": reply})
                await sse("done", {"reply": reply})
                return resp

            # Multiple candidates — ask user to pick
            cand_list = "\n".join(f"{i+1}. {c['name']}" for i, c in enumerate(items))
            await sse("meta", {**_base_meta, "graph_candidates": items})
            reply = (f"Нашёл {len(items)} метрик по запросу «{graph_query}» "
                     f"на хосте {effective_name}. Уточните какую именно:\n{cand_list}")
            await sse("chunk", {"text": reply})
            await sse("done", {"reply": reply})
            return resp

        # ── Metric list path ──────────────────────────────────────────────────
        if wants_metric_list and effective_hostid:
            all_items = await _zabbix_get_all_items(effective_hostid)
            await sse("meta", {
                "server_name": effective_name, "server_info": found_info,
                "system_name": None, "system_hosts": [], "triggers": [],
                "n_ok": 0, "chart": None,
                "fuzzy_match": fuzzy_match, "fuzzy_query": ident if fuzzy_match else None,
                "graph_candidates": None, "graph_hostid": effective_hostid,
            })
            await sse("metric_list", {
                "host": effective_name or "",
                "total": len(all_items),
                "items": all_items,
            })
            reply = f"Метрики сервера {effective_name}: всего {len(all_items)}."
            await sse("chunk", {"text": reply})
            await sse("done", {"reply": reply})
            return resp

        # ── Normal server status flow ─────────────────────────────────────────
        if found_info:
            triggers = await _zabbix_get_triggers(found_info["hostid"])
            active_triggers = [t for t in triggers if t["active"]]
            n_ok    = len(triggers) - len(active_triggers)
            t_lines = []
            for t in active_triggers:
                ago = f" ({t['ago_min']} мин)" if t["ago_min"] else ""
                t_lines.append(f"  🔴 [{t['severity']}] {t['title']}{ago}")
        else:
            n_ok = 0
            t_lines = []

        await sse("meta", {
            "server_name": found_name,
            "server_info": found_info,
            "system_name": None,
            "system_hosts": [],
            "triggers":    active_triggers,
            "n_ok":        n_ok,
            "fuzzy_match": fuzzy_match,
            "fuzzy_query": ident if fuzzy_match else None,
            "graph_candidates": None,
            "graph_hostid":     effective_hostid,
        })

        # ── Phase 3a: auto expert analysis (only when active problems exist) ──
        if found_info and active_triggers and fresh_ident:
            expert_msgs = [
                {"role": "system", "content": p_expert},
                {"role": "user",   "content": _build_expert_ctx(found_name, found_info, active_triggers)},
            ]
            try:
                async for chunk in _gigachat_stream(expert_msgs, model):
                    await sse("expert_chunk", {"text": chunk})
            except Exception as e:
                await sse("expert_chunk", {"text": f"⚠️ Ошибка анализа: {e}"})

        # ── Phase 3b: conversational reply ────────────────────────────────────
        if found_info:
            ctx_block = _safe_format(
                p_found,
                name=found_name, ip=found_info["ip"], ci=found_info["ci"],
                os=found_info["os"], group=found_info["group"],
                n_active=len(active_triggers), n_total=len(triggers),
                n_ok=n_ok,
                trigger_lines="\n".join(t_lines) if t_lines else "  (нет активных проблем)",
            )
            if fuzzy_match:
                ctx_block = (
                    f"\n[ПРИМЕЧАНИЕ: идентификатор «{ident}» не найден точно; "
                    f"показан ближайший совпадающий сервер]"
                    + ctx_block
                )
        elif ident:
            ctx_block = _safe_format(p_not_found, ident=ident)
        else:
            ctx_block = p_no_ident

        messages = [{"role": "system", "content": p_base + ctx_block}]
        for h in history[-8:]:
            messages.append({"role": h["role"], "content": h["content"]})

        user_message: dict = {"role": "user", "content": user_msg}
        if file_ids and model in _VISION_MODELS:
            user_message["attachments"] = [{"type": "image", "id": fid} for fid in file_ids]
        messages.append(user_message)

        reply_parts: list[str] = []
        try:
            async for chunk in _gigachat_stream(messages, model):
                reply_parts.append(chunk)
                await sse("chunk", {"text": chunk})
        except Exception as e:
            err = f"⚠️ Ошибка GigaChat: {e}"
            reply_parts.append(err)
            await sse("chunk", {"text": err})

        await sse("done", {"reply": "".join(reply_parts)})

    except Exception as e:
        await sse("error", {"text": f"⚠️ Внутренняя ошибка сервера: {e}"})
        await sse("done", {})

    return resp
