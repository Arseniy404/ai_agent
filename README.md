# Zabbix Agent

ИИ-агент для мониторинга IT-инфраструктуры. Принимает вопросы на естественном языке, обращается к Zabbix API и отвечает через GigaChat.

## Возможности

- Статус сервера по имени, IP или номеру КЭ
- Сводка по системе (группе серверов)
- Список серверов группы
- Список метрик хоста с возможностью построить график
- График любой числовой метрики за последние 3 часа
- Экспертный разбор активных проблем (SRE-анализ)
- Прикрепление изображений (модели Pro и Max)
- Редактирование системных промптов прямо в интерфейсе

## Стек

- **Backend**: Python, aiohttp
- **LLM**: GigaChat (Sber) — function calling + streaming
- **Мониторинг**: Zabbix JSON-RPC API
- **Frontend**: vanilla JS + SSE

## Запуск

```bash
cp .env.example .env   # заполните значения
venv3/bin/python3 main.py
```

Открыть: http://localhost:8077

## Переменные окружения

Обязательные (без них приложение не стартует):

| Переменная | Описание |
|---|---|
| `GIGACHAT_AUTH_KEY` | Base64-ключ авторизации GigaChat (`client_id:secret`) |
| `ZABBIX_URL` | URL Zabbix JSON-RPC (`http://host/api_jsonrpc.php`) |
| `ZABBIX_PASS` | Пароль пользователя Zabbix |

Опциональные:

| Переменная | Дефолт | Описание |
|---|---|---|
| `ZABBIX_USER` | `Admin` | Имя пользователя Zabbix |
| `GIGACHAT_MODEL` | `GigaChat-2-Pro` | Модель по умолчанию |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` | Скоуп OAuth |
| `GIGACHAT_AUTH_URL` | стандартный Sber | URL получения токена |
| `GIGACHAT_API_URL` | стандартный Sber | URL chat completions |
| `GIGACHAT_FILES_URL` | стандартный Sber | URL загрузки файлов |
