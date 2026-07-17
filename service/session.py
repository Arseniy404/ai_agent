"""Сессия одного пользователя, в памяти процесса. Инструмент внутренний и
одно-пользовательский, поэтому без Redis/БД — при рестарте контекст обнуляется, это ок.
"""
from service import config


class Session:
    def __init__(self):
        self.last_structured_data: dict | None = None
        self.history: list[dict] = []

    def set_structured_data(self, data: dict) -> None:
        self.last_structured_data = data

    def add_message(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        max_len = config.HISTORY_MAX_MESSAGES
        if len(self.history) > max_len:
            self.history = self.history[-max_len:]


_session = Session()


def get_session() -> Session:
    return _session
