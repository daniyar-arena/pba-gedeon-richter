"""Хранение собранных отчётов в Supabase (Postgres через REST).

Зачем не файлы: на бесплатном тарифе Render диск эфемерный — после сна или деплоя
контейнер поднимается чистым, и сохранённые отчёты пропали бы. Supabase живёт отдельно
от сервиса, поэтому ссылка на отчёт работает и через месяц.

Ключ используется сервисный (service_role) — он ходит в обход RLS, поэтому лежит
только в переменных окружения сервера и никогда не уезжает в браузер.

Если переменные не заданы, хранение просто выключено: сборка отчётов работает как
раньше, а страница «Мои отчёты» честно пишет, что хранилище не настроено.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("pba.storage")

TABLE = "reports"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
LIST_LIMIT = 100

# Поля для списка: html не тянем, иначе список весил бы десятки мегабайт.
LIST_FIELDS = "id,brand,month,client,source_file,created_at,size_bytes"


class StorageNotConfigured(RuntimeError):
    """Не заданы SUPABASE_URL / SUPABASE_SERVICE_KEY."""


def _config() -> tuple[str, str]:
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_KEY") or ""
    if not url or not key:
        raise StorageNotConfigured(
            "Хранение отчётов не настроено: нет SUPABASE_URL или SUPABASE_SERVICE_KEY."
        )
    return url, key


def configured() -> bool:
    try:
        _config()
    except StorageNotConfigured:
        return False
    return True


def _headers(key: str, prefer: str | None = None) -> dict[str, str]:
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


async def save_report(report_id: str, report: dict, html: str) -> bool:
    """Складывает готовую страницу целиком. Возвращает False, если не получилось —
    сборка отчёта из-за этого падать не должна, пользователь уже увидел результат."""
    try:
        url, key = _config()
    except StorageNotConfigured:
        return False

    month = report["month"]
    row = {
        "id": report_id,
        "brand": report.get("brand") or "",
        "month": month.get("label") or "",
        "client": report.get("client") or "",
        "source_file": report.get("source_file") or "",
        "size_bytes": len(html.encode("utf-8")),
        "html": html,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{url}/rest/v1/{TABLE}",
                headers=_headers(key, prefer="return=minimal,resolution=merge-duplicates"),
                json=row,
            )
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "supabase save %s failed: HTTP %s %s",
            report_id,
            exc.response.status_code,
            exc.response.text[:300],
        )
    except httpx.HTTPError as exc:
        logger.warning("supabase save %s failed: %s", report_id, exc)
    return False


async def list_reports() -> list[dict]:
    url, key = _config()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{url}/rest/v1/{TABLE}",
            headers=_headers(key),
            params={
                "select": LIST_FIELDS,
                "order": "created_at.desc",
                "limit": str(LIST_LIMIT),
            },
        )
    resp.raise_for_status()
    rows = resp.json()
    return rows if isinstance(rows, list) else []


async def get_report_html(report_id: str) -> tuple[str, dict] | None:
    url, key = _config()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{url}/rest/v1/{TABLE}",
            headers=_headers(key),
            params={"select": f"{LIST_FIELDS},html", "id": f"eq.{report_id}", "limit": "1"},
        )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    row = rows[0]
    return row.pop("html", "") or "", row


async def delete_report(report_id: str) -> bool:
    url, key = _config()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.delete(
            f"{url}/rest/v1/{TABLE}",
            headers=_headers(key, prefer="return=minimal"),
            params={"id": f"eq.{report_id}"},
        )
    resp.raise_for_status()
    return True
