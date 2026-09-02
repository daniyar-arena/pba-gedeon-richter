"""Локальный запуск сайта: включает режим «без пароля» и поднимает сервер.

Почему отдельный файл, а не переменная в start.bat: `set PBA_LOCAL=1` в .bat до
python не доходит — в этой сборке Python `.venv\\Scripts\\python.exe` перезапускает
базовый интерпретатор через обёртку, и окружение по пути теряется. Здесь режим
ставится в самом процессе, до импорта приложения.

На хостинге этот файл не используется: там команда запуска — `uvicorn app.main:app`,
то есть локальный режим включиться не может даже случайно, и пароль обязателен.
"""

from __future__ import annotations

import os

os.environ["PBA_LOCAL"] = "1"

import uvicorn  # noqa: E402  (после установки переменной)

HOST = "127.0.0.1"
PORT = 8765

if __name__ == "__main__":
    print(f"ПБА Gedeon Richter — сайт на http://{HOST}:{PORT}")
    print("Закройте это окно, когда закончите.")
    uvicorn.run("app.main:app", host=HOST, port=PORT)
