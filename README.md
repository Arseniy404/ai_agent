# AIMon — ассистент мониторинга (корпоративная версия)

ИИ-агент для мониторинга IT-инфраструктуры. Принимает вопросы на естественном
языке, берёт данные из корпоративного **SELF-портала** (REST) и отвечает через
GigaChat. Baseline-версия: только чтение, только статус конкретного хоста
(hostname/ip/ci), без графиков, списка метрик, загрузки файлов, переключения
моделей и без фичи «система/группа» (нет надёжного источника Zabbix host-групп
в присланных схемах портала — см. план миграции).

## Возможности (baseline)

- Статус сервера по имени, IP или номеру КЭ
- Экспертный разбор активных проблем (SRE-анализ)
- Автоопределение стенда/заббикса по хосту (перебор `infra`/`usi`/`bn_cluster`/`net`)

## Стек

- **Backend**: Python, FastAPI + httpx (async, SSE)
- **LLM**: GigaChat (Sber) — function calling + streaming
- **Мониторинг**: корпоративный SELF-портал (REST, mTLS)
- **Frontend**: vanilla JS + SSE

## Структура

| Файл | Назначение |
|---|---|
| `main.py` | FastAPI-приложение, роуты `/` и `/chat` (SSE) |
| `dialog.py` | Оркестрация: intent → данные портала → ответ |
| `analyze.py` | Извлечение идентификатора хоста (GigaChat function-call) |
| `gigachat.py` | Клиент GigaChat (токен, complete, fn_call, stream) |
| `self_portal.py` | mTLS-клиент SELF-портала (только сертификат, без Bearer-токена) + lookup хоста |
| `prompts.py` | Системные промпты и контекстные шаблоны |
| `config.py` | Конфигурация из окружения |

> `self_portal.py`: реализован по подтверждённым схемам (`/get_zabbix_by_type`,
> `/report/get_host`, `/events`). Требует проверки на реальном портале — см.
> раздел Verification в плане миграции.

## Запуск

```bash
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполните значения, положите Certificate.txt и Key.txt
python main.py
```

Открыть: http://localhost:8077

## Переменные окружения

| Переменная | Дефолт | Описание |
|---|---|---|
| `GIGACHAT_AUTH_KEY` | — (обязательно) | Base64-ключ авторизации GigaChat (`client_id:secret`) |
| `GIGACHAT_TOKEN` | — (опционально) | Bootstrap Bearer-токен, валиден ~30 мин с момента старта; после истечения — автообновление по `GIGACHAT_AUTH_KEY` |
| `GIGACHAT_MODEL` | `GigaChat-2-Pro` | Модель GigaChat |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` | Скоуп OAuth |
| `SELF_BASE` | `https://selfportalift.csim.delta.sbrf.ru` | Хост SELF-портала |
| `SELF_CERT` | `./Certificate.txt` | Клиентский сертификат (mTLS) |
| `SELF_KEY` | `./Key.txt` | Приватный ключ (mTLS) |
| `SELF_VERIFY` | `0` | Проверка TLS портала (путь к CA или `0`) |
| `HOST` / `PORT` | `0.0.0.0` / `8077` | Адрес веб-сервера |
