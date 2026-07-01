"""Централизованная конфигурация приложения.

Значения читаются из окружения (.env через python-dotenv в main.py).
Здесь — только чтение и дефолты, без побочных эффектов.
"""
import os

# ── SELF-портал (корпоративная среда) ───────────────────────────────────────
# Базовый хост портала; у каждого сервиса свой префикс пути.
SELF_BASE = os.getenv("SELF_BASE", "https://selfportalift.csim.delta.sbrf.ru")

# Базовые URL по сервисам (см. OAS3 «Servers» каждого API).
SELF_URLS = {
    "auth":       f"{SELF_BASE}/api/auth",       # Auth API
    "dashboard":  f"{SELF_BASE}/api/v2",         # Event Dashboard API
    "zabbix":     f"{SELF_BASE}/api/zabbix",      # Zabbix FUNC
    "rlm":        f"{SELF_BASE}/api/rlm",         # RLM API
    "threshold":  f"{SELF_BASE}/api/threshold",   # Threshold Adjustment API
    "api":        f"{SELF_BASE}/api",             # Self / Suppressing / Maintenance / ...
}

# ── mTLS: клиентский сертификат + приватный ключ ─────────────────────────────
# Файлы лежат в директории проекта (см. решение по архитектуре).
_HERE = os.path.dirname(os.path.abspath(__file__))
SELF_CERT = os.getenv("SELF_CERT", os.path.join(_HERE, "Certificate.txt"))
SELF_KEY  = os.getenv("SELF_KEY",  os.path.join(_HERE, "Key.txt"))
# Проверка серверного сертификата портала. Путь к CA-бандлу или "0" чтобы отключить.
_verify = os.getenv("SELF_VERIFY", "")
SELF_VERIFY: "str | bool" = False if _verify in ("0", "false", "False", "") else _verify

# ── GigaChat ─────────────────────────────────────────────────────────────────
GIGACHAT_AUTH_URL = os.getenv("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
GIGACHAT_API_URL  = os.getenv("GIGACHAT_API_URL",  "https://gigachat.devices.sberbank.ru/api/v1/chat/completions")
GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]
GIGACHAT_SCOPE    = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL    = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Pro")

# ── HTTP ─────────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8077"))
