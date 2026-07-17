"""Конфигурация из переменных окружения. Один пользователь, один привязанный сервер."""
import os

# Обязательные
GIGACHAT_AUTH_KEY = os.environ["GIGACHAT_AUTH_KEY"]
ZABBIX_URL = os.environ["ZABBIX_URL"]
ZABBIX_PASS = os.environ["ZABBIX_PASS"]

# Привязанный сервер (single-tenant MVP) — identifier ищется в Zabbix по имени хоста
BOUND_HOST = os.environ["BOUND_HOST"]

# Необязательные
ZABBIX_USER = os.environ.get("ZABBIX_USER", "Admin")
GIGACHAT_MODEL = os.environ.get("GIGACHAT_MODEL", "GigaChat-2-Pro")
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_AUTH_URL = os.environ.get(
    "GIGACHAT_AUTH_URL",
    "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
)
GIGACHAT_API_URL = os.environ.get(
    "GIGACHAT_API_URL",
    "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
)

HISTORY_MAX_MESSAGES = int(os.environ.get("HISTORY_MAX_MESSAGES", "8"))
ZABBIX_TIMEOUT_SECONDS = int(os.environ.get("ZABBIX_TIMEOUT_SECONDS", "10"))
GIGACHAT_STREAM_TIMEOUT_SECONDS = int(os.environ.get("GIGACHAT_STREAM_TIMEOUT_SECONDS", "120"))
GIGACHAT_AUTH_TIMEOUT_SECONDS = int(os.environ.get("GIGACHAT_AUTH_TIMEOUT_SECONDS", "15"))

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
