# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Команды

Установка и запуск:
```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить GIGACHAT_AUTH_KEY, ZABBIX_URL, ZABBIX_PASS, BOUND_HOST
python -m service.main  # поднимает aiohttp на 127.0.0.1:8080 (см. HOST/PORT в .env)
```

Проверка синтаксиса (автоматических тестов и линтера в проекте пока нет):
```bash
python -m py_compile service/*.py service/*/*.py
```

## Архитектура

Это чат-инструмент для мониторинга одного Zabbix-хоста. Центральный архитектурный
инвариант проекта: **LLM (GigaChat) никогда не решает, какие данные запросить у
Zabbix — только форматирует то, что уже получил детерминированный код.** Это
осознанная реакция на предыдущий прототип, где LLM сама разбирала намерение
пользователя через function calling (с трёхуровневым fallback) и сама выбирала
метрику из списка — и регулярно ошибалась. Не возвращайте LLM в роль роутера/
диспетчера данных при добавлении новых фич — вместо этого добавляйте новую кнопку
и новый action.

Поток запроса (кнопка → ответ):
1. Фронтенд (`service/templates/index.html`) шлёт `POST /action/server_status` без
   параметров — единственный привязанный сервер (`BOUND_HOST` из конфига) уже
   известен, никакого поиска/уточнения хоста нет (single-tenant).
2. `service/actions/server_status.py` — чистая детерминированная функция: достаёт
   hostid и триггеры через `service/gproxy/client.py`, сама (в коде, не в LLM)
   мапит severity и считает `ago_min`, собирает фиксированную по схеме структуру
   `structured_data`. Ошибки Zabbix (недоступен, хост не найден) — это `ActionError`,
   уходят пользователю напрямую, до LLM не доходят.
3. `structured_data` кладётся в сессию (`service/session.py`) и одновременно
   отправляется в GigaChat (`service/llm/gigachat_client.py`, только потоковый
   `stream()`, никакого function calling) с промптом `PROMPT_STATUS`
   (`service/llm/prompts.py`) — единственная задача LLM здесь: изложить уже
   готовые числа человеческим языком.
4. Ответ стримится клиенту через SSE (`service/sse.py`): события `meta` (сырые
   данные), `chunk` (текст по кусочкам), `error`, `done`.
5. Follow-up-вопросы (`POST /chat/followup`) не делают новых обращений к Zabbix —
   LLM отвечает только на основе `structured_data`, сохранённого в сессии на
   шаге 3, и истории диалога, с промптом `PROMPT_FOLLOWUP`. Если ответа в данных
   нет, LLM обязана сказать об этом прямо, а не выдумывать.

Расширение новой кнопкой = новый модуль в `service/actions/` (возвращает свою
`structured_data` с полем `action`) + новый роут в `service/main.py` + при
необходимости новый промпт в `service/llm/prompts.py`. Роутинг остаётся
1:1 кнопка → функция.

Ключевые модули:
- `service/gproxy/client.py` — Zabbix JSON-RPC клиент: кэш токена сессии,
  авто-релогин при ошибке "Not authorized" (сброс токена + один повтор запроса),
  `asyncio.Lock` защищает от гонки при параллельном логине.
- `service/session.py` — сессия в памяти процесса на одного пользователя
  (`last_structured_data` + `history`, максимум `HISTORY_MAX_MESSAGES` сообщений).
  Инструмент внутренний и одно-пользовательский — сознательно без БД/Redis/аутентификации;
  при рестарте процесса контекст обнуляется.
- `service/config.py` — вся конфигурация из env-переменных. Обязательные:
  `GIGACHAT_AUTH_KEY`, `ZABBIX_URL`, `ZABBIX_PASS`, `BOUND_HOST` — при отсутствии
  падает с `KeyError` при импорте.
- `service/sse.py` — общий хелпер для событий вида `data: {...}\n\n`.
