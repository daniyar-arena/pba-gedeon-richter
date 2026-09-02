"""Страница «Мои отчёты»: список отчётов со ссылками, скачиванием и удалением.

В списке два источника: отчёты, собранные в этом запуске (живут в памяти процесса),
и сохранённые в Supabase. Первые попадают в список сразу после сборки, но помечены
как несохранённые — после перезапуска сайта они пропадут, и врать об этом нельзя.

Вёрстка берёт тот же CSS, что и сам отчёт, чтобы список не выглядел чужой страницей.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.report_html import CSS, esc

# Алматы: UTC+5, без переходов на летнее время.
ALMATY = timezone(timedelta(hours=5))


def human_date(raw: str) -> str:
    """Время храним в UTC, показываем по Алматы — иначе «собрано вчера» читается
    как ошибка."""
    if not raw:
        return "—"
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return esc(raw)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(ALMATY).strftime("%d.%m.%Y %H:%M")


def _human_size(size: int | None) -> str:
    if not size:
        return "—"
    return f"{size / 1024:.0f} КБ"


def _row(report: dict) -> str:
    report_id = esc(report.get("id"))
    if report.get("stored"):
        badge = '<span class="status-badge"><span class="status-dot live"></span>сохранён</span>'
    else:
        badge = (
            '<span class="status-badge"><span class="status-dot demo"></span>'
            "только в этом запуске</span>"
        )
    return (
        "<tr>"
        f'<td><span class="cell-main">{esc(report.get("brand") or "—")}</span>'
        f'<span class="cell-sub">{esc(report.get("client") or "")}</span></td>'
        f'<td>{esc(report.get("month") or "—")}</td>'
        f'<td class="muted">{human_date(report.get("created_at") or "")}</td>'
        f"<td>{badge}</td>"
        f'<td class="num muted">{_human_size(report.get("size_bytes"))}</td>'
        '<td class="row-links">'
        f'<a class="btn" href="/reports/{report_id}">Открыть</a>'
        f'<a class="btn" href="/reports/{report_id}/download">Скачать</a>'
        f'<form method="post" action="/reports/{report_id}/delete" '
        "onsubmit=\"return confirm('Удалить этот отчёт?')\">"
        '<button class="btn danger" type="submit">Удалить</button></form>'
        "</td></tr>"
    )


def render_reports_page(reports: list[dict], note: str | None = None) -> str:
    banner = f'<div class="callout warn">{esc(note)}</div>' if note else ""

    if reports:
        table = (
            '<div class="table-wrap"><table>'
            "<thead><tr><th>Бренд</th><th>Месяц</th><th>Собран</th><th>Хранение</th>"
            '<th class="num">Размер</th><th></th></tr></thead>'
            f'<tbody>{"".join(_row(r) for r in reports)}</tbody></table></div>'
        )
    else:
        table = (
            '<div class="callout">Отчётов пока нет. Соберите отчёт — он появится '
            "в этом списке сразу.</div>"
        )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Мои отчёты — ПБА Gedeon Richter</title>
<style>{CSS}
  .row-links {{ display: flex; gap: 8px; align-items: center; }}
  .row-links form {{ margin: 0; }}
  .btn.danger {{ color: var(--neg); }}
  .btn.danger:hover {{ border-color: var(--neg); }}
  .status-badge {{ margin-left: 0; }}
</style>
</head>
<body>
<div class="toolbar">
  <a class="btn primary" href="/">Собрать новый отчёт</a>
  <span class="muted small">Сохранённые отчёты открываются и после перезапуска сайта.</span>
</div>
<div class="wrap">
  <h1>Мои отчёты</h1>
  <p class="subtitle">Последние собранные отчёты — открываются как в день сборки.</p>
  <section class="card section">{banner}{table}</section>
</div>
</body>
</html>"""
