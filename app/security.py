"""Пароль на весь сайт и ограничения на расход платных источников.

Логика доступа: локальный запуск (start.bat на своей машине) работает без пароля,
любой запрос извне обязан прислать HTTP Basic с PBA_USER/PBA_PASSWORD. Если на
хостинге пароль не задан — сайт отвечает 503, а не открывается всем: за формой
лежат ключи Apify и Anthropic (~$0.10 за прогон) и бюджеты клиента.

Ограничения — не «на всякий случай»: без них один запрос с сотней ключевых слов
стоил бы несколько долларов, а зацикленный клиент выкачал бы весь баланс.
"""

from __future__ import annotations

import base64
import os
import secrets
import time
from collections import deque

REALM = "PBA Gedeon Richter"

# Сколько сборок отчёта разрешаем с одного адреса в час. Восемь ключей на прогон —
# это ~$0.10, то есть потолок примерно $2 в час на адрес.
REPORTS_PER_HOUR = 20
MAX_KEYWORDS = 12


class RateLimiter:
    """Скользящее окно на адрес. В памяти процесса: инструмент однопользовательский,
    отдельное хранилище тут было бы сложнее самой защиты."""

    def __init__(self, limit: int, window_seconds: int = 3600) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def retry_after_seconds(self, key: str) -> int:
        hits = self._hits.get(key)
        if not hits:
            return 0
        return max(1, int(self.window - (time.monotonic() - hits[0])))


report_limiter = RateLimiter(REPORTS_PER_HOUR)


def local_mode() -> bool:
    """Признак локального запуска берём из переменной окружения, которую ставит start.bat,
    а не из IP клиента: за прокси хостинга адрес бывает внутренним, и «локальным» мог бы
    оказаться запрос из интернета. Нет переменной — значит хостинг, пароль обязателен."""
    return os.getenv("PBA_LOCAL", "").strip().lower() in ("1", "true", "yes")


def credentials_configured() -> bool:
    return bool(os.getenv("PBA_PASSWORD"))


def check_basic_auth(header: str | None) -> bool:
    """Сравнение через compare_digest — чтобы по времени ответа нельзя было подбирать пароль."""
    expected_user = os.getenv("PBA_USER", "havas")
    expected_password = os.getenv("PBA_PASSWORD", "")
    if not expected_password or not header or not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header.split(" ", 1)[1]).decode("utf-8")
        user, _, password = raw.partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    # Сравниваем байты, а не строки: compare_digest со строками падает на не-ASCII,
    # то есть пароль с кириллицей ронял бы запрос в 500 вместо честного 401.
    return secrets.compare_digest(
        user.encode("utf-8"), expected_user.encode("utf-8")
    ) and secrets.compare_digest(
        password.encode("utf-8"), expected_password.encode("utf-8")
    )
