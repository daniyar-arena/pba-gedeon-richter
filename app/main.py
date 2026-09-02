"""Сайт сборки ПБА-отчётов Gedeon Richter.

Флоу: загрузили xlsx -> выбрали месяц и ключевые слова -> сервер посчитал ПБА,
запросил спрос в Google и вывод у Клода -> страница отчёта + кнопка «Скачать HTML».

Запуск: start.bat в корне проекта (или uvicorn app.main:app --reload).
Хранилище в памяти процесса: перезапустили сервер — прошлые отчёты потерялись,
но сами xlsx остаются в uploads/, и отчёт собирается заново за минуту.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from app.ai_summary import build_ai_summary  # noqa: E402  (после load_dotenv — читает ключ)
from app.pba_parser import ParseError, parse_pba_file  # noqa: E402
from app.report_html import render_report  # noqa: E402
from app.search_demand import GEO_TARGET_CONSTANTS, fetch_google_demand  # noqa: E402
from app import security  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# httpx на INFO печатает полный URL каждого запроса — лишний шум и риск утечки токенов.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("pba.main")

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Сколько последних загрузок и отчётов держим. Без потолка процесс на хостинге
# течёт по памяти, а uploads/ забивает диск.
MAX_KEPT_UPLOADS = 30
MAX_KEPT_JOBS = 30

app = FastAPI(title="ПБА Gedeon Richter")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def guard(request, call_next):
    """Пароль на весь сайт, включая просмотр и скачивание отчётов: в них бюджеты клиента.
    Локальный запуск пароля не требует, чтобы start.bat на своей машине работал как раньше."""
    # /healthz дёргает сам хостинг снаружи — за паролем он получал бы 401 и считал
    # сервис нерабочим. Эта ручка отдаёт только «живой», без статуса ключей.
    if request.url.path != "/healthz" and not security.local_mode():
        if not security.credentials_configured():
            return JSONResponse(
                {"detail": "Сайт не настроен: на хостинге не задан PBA_PASSWORD."},
                status_code=503,
            )
        if not security.check_basic_auth(request.headers.get("authorization")):
            return Response(
                status_code=401,
                content="Нужен логин и пароль.",
                headers={"WWW-Authenticate": f'Basic realm="{security.REALM}", charset="UTF-8"'},
            )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

# Хранилища в памяти процесса. Инструмент однопользовательский и локальный,
# поэтому без базы: интерфейс узкий, при необходимости меняется на что угодно.
_uploads: dict[str, dict] = {}
_jobs: dict[str, dict] = {}
# Ссылки на фоновые задачи: без них сборщик мусора может убрать задачу до завершения.
_tasks: set[asyncio.Task] = set()


def _trim_uploads() -> None:
    """Словари в Python сохраняют порядок вставки, поэтому первый ключ — самый старый."""
    while len(_uploads) > MAX_KEPT_UPLOADS:
        oldest_key = next(iter(_uploads))
        oldest = _uploads.pop(oldest_key)
        path = oldest.get("path")
        if path:
            Path(path).unlink(missing_ok=True)


def _trim_jobs() -> None:
    while len(_jobs) > MAX_KEPT_JOBS:
        _jobs.pop(next(iter(_jobs)), None)


class KeywordIn(BaseModel):
    keyword: str = Field(min_length=1, max_length=120)
    label: str = ""
    role: str = "other"


class ReportRequest(BaseModel):
    upload_id: str
    sheet: str
    # Потолок на ключи — прямая защита кошелька: каждый ключ это платный запрос в Apify.
    keywords: list[KeywordIn] = Field(default_factory=list, max_length=security.MAX_KEYWORDS)
    geo: str = "KZ"
    use_google: bool = True
    use_ai: bool = True


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> dict:
    """Проверка живости для хостинга: открыта, поэтому ничего лишнего не сообщает."""
    return {"ok": True}


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "google_keywords_ready": bool(os.getenv("APIFY_API_TOKEN")),
        "claude_ready": bool(os.getenv("ANTHROPIC_API_KEY")),
        "geos": sorted(GEO_TARGET_CONSTANTS),
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Нужен файл Excel (.xlsx). Пришлите выгрузку ПБА как есть.")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Файл больше 20 МБ — это не похоже на ПБА.")

    upload_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{upload_id}.xlsx"
    path.write_bytes(payload)

    try:
        parsed = parse_pba_file(path)
    except ParseError as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        path.unlink(missing_ok=True)
        logger.exception("parse failed")
        raise HTTPException(400, f"Не удалось прочитать файл: {type(exc).__name__}") from exc

    _uploads[upload_id] = {"path": path, "filename": file.filename, "parsed": parsed}
    _trim_uploads()

    return JSONResponse(
        {
            "upload_id": upload_id,
            "filename": file.filename,
            "client": parsed["client"],
            "brand": parsed["brand"],
            "months": [
                {
                    "sheet": m["sheet"],
                    "label": m["label"],
                    "period": m["meta"].get("period"),
                    "closed_weeks": m["closed_week_numbers"],
                    "pending_weeks": m["pending_week_numbers"],
                    "plan_month": m["summary"]["plan_month"],
                    "fact_closed": m["summary"]["fact_closed"],
                    "delivery_pct": m["summary"]["delivery_pct"],
                }
                for m in parsed["months"]
            ],
        }
    )


@app.post("/api/report")
async def create_report(req: ReportRequest, request: Request) -> JSONResponse:
    client_host = request.client.host if request.client else "unknown"
    if not security.local_mode() and not security.report_limiter.allow(client_host):
        wait = security.report_limiter.retry_after_seconds(client_host)
        raise HTTPException(
            429,
            f"Слишком много сборок за час (лимит {security.REPORTS_PER_HOUR}) — "
            f"защита от лишних трат на Google. Попробуйте через {wait // 60 + 1} мин.",
        )

    upload = _uploads.get(req.upload_id)
    if not upload:
        raise HTTPException(404, "Файл не найден — загрузите его заново (сервер перезапускался?).")

    month = next((m for m in upload["parsed"]["months"] if m["sheet"] == req.sheet), None)
    if month is None:
        raise HTTPException(400, f"В файле нет листа «{req.sheet}».")

    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "running", "error": None, "report": None}
    _trim_jobs()
    task = asyncio.create_task(_run_job(job_id, upload, month, req))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return JSONResponse({"job_id": job_id})


async def _run_job(job_id: str, upload: dict, month: dict, req: ReportRequest) -> None:
    try:
        keywords = [k.model_dump() for k in req.keywords]
        if req.use_google and keywords:
            demand = await fetch_google_demand(keywords, req.geo, os.getenv("APIFY_API_TOKEN"))
        else:
            demand = {
                "source": "Google Keyword Planner (через Apify)",
                "geo": req.geo.upper(),
                "available": False,
                "items": [],
                "note": "Блок спроса выключен при сборке отчёта."
                if not req.use_google
                else "Ключевые слова не заданы — блок спроса пропущен.",
            }

        client = upload["parsed"]["client"]
        brand = upload["parsed"]["brand"]
        if req.use_ai:
            ai = await build_ai_summary(month, demand, client, brand)
        else:
            from app.ai_summary import _fallback

            ai = _fallback(month, demand, reason="вывод от Клода выключен при сборке")

        report = {
            "client": client,
            "brand": brand,
            "month": month,
            "demand": demand,
            "ai": ai,
            "source_file": upload["filename"],
            "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        }
        _jobs[job_id] = {"status": "done", "error": None, "report": report}
        logger.info("report %s ready (%s / %s)", job_id, brand, month["label"])
    except Exception:
        # Наружу — общее сообщение: текст исключения может содержать внутренние детали
        # (адреса, куски ответов API). Подробности остаются в логе сервера.
        logger.exception("job %s failed", job_id)
        _jobs[job_id] = {
            "status": "failed",
            "error": "Не удалось собрать отчёт. Подробности в логах сервера.",
            "report": None,
        }


@app.get("/api/report/{job_id}")
async def job_status(job_id: str) -> JSONResponse:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Отчёт не найден.")
    return JSONResponse(
        {
            "status": job["status"],
            "error": job["error"],
            "view_url": f"/api/report/{job_id}/view" if job["status"] == "done" else None,
        }
    )


def _job_report(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Отчёт не найден.")
    if job["status"] != "done":
        raise HTTPException(409, f"Отчёт ещё не готов (статус: {job['status']}).")
    return job["report"]


@app.get("/api/report/{job_id}/view", response_class=HTMLResponse)
async def view_report(job_id: str) -> HTMLResponse:
    return HTMLResponse(render_report(_job_report(job_id), job_id=job_id))


@app.get("/api/report/{job_id}/download")
async def download_report(job_id: str) -> Response:
    report = _job_report(job_id)
    html = render_report(report, job_id=None)
    name = _safe_filename(f"ПБА_{report['brand']}_{report['month']['label']}.html")
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(name)}"},
    )


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE).strip().replace(" ", "_")


def _url_quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name)
