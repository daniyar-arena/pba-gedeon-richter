"""Страница «Мои отчёты»: список сохранённых отчётов со ссылками и удалением.

Вёрстка берёт тот же CSS, что и сам отчёт, чтобы список не выглядел чужой страницей.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.report_html import CSS, esc


def _human_date(raw: str) -> str:
    """Supabase отдаёт время в UTC ISO-8601. Показываем по Алматы (UTC+5),
    иначе «собрано вчера» читается как ошибка."""
    if not raw:
        return "—"
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return esc(raw)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M") + " UTC"


def _human_size(size: int | None) -> str:
    if not size:
        return "—"
    return f"{size / 1024:.0f} КБ"


def render_reports_page(rows: list[dict], note: str | None = None) -> str:
    if note:
        body = f'<div class="callout warn">{esc(note)}</div>'
    elif not rows:
        body = (
            '<div class="callout">Сохранённых отчётов пока нет. Соберите отчёт — '
            "он попадёт в этот список автоматически.</div>"
        )
    else:
        items = []
        for row in rows:
            report_id = esc(row.get("id"))
            items.append(
                "<tr>"
                f'<td><span class="cell-main">{esc(row.get("brand") or "—")}</span>'
                f'<span class="cell-sub">{esc(row.get("client") or "")}</span></td>'
                f'<td>{esc(row.get("month") or "—")}</td>'
                f'<td class="muted">{_human_date(row.get("created_at") or "")}</td>'
                f'<td class="num muted">{_human_size(row.get("size_bytes"))}</td>'
                '<td class="row-links">'
                f'<a class="btn" href="/reports/{report_id}">Открыть</a>'
                f'<a class="btn" href="/reports/{report_id}/download">Скачать</a>'
                f'<form method="post" action="/reports/{report_id}/delete" '
                'onsubmit="return confirm(\'Удалить этот отчёт навсегда?\')">'
                '<button class="btn danger" type="submit">Удалить</button></form>'
                "</td></tr>"
            )
        body = (
            '<div class="table-wrap"><table>'
            "<thead><tr><th>Бренд</th><th>Месяц</th><th>Собран</th>"
            '<th class="num">Размер</th><th></th></tr></thead>'
            f'<tbody>{"".join(items)}</tbody></table></div>'
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
</style>
</head>
<body>
<div class="toolbar">
  <a class="btn primary" href="/">Собрать новый отчёт</a>
  <span class="muted small">Отчёты хранятся отдельно от сервера, поэтому ссылки
  работают и после перезапуска сайта.</span>
</div>
<div class="wrap">
  <h1>Мои отчёты</h1>
  <p class="subtitle">Последние собранные отчёты — открываются как в день сборки.</p>
  <section class="card section">{body}</section>
</div>
</body>
</html>"""
