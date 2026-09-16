"""Восстановление данных по ключевым словам из уже собранного отчёта.

Зачем: прогон в Google платный (~$0.012 за ключ), и если нужно пересобрать отчёт
из-за правки в ПБА, платить второй раз незачем. Для отчётов, собранных начиная с
этой версии, данные лежат отдельным JSON. Для собранных раньше — вытаскиваем их
обратно из HTML страницы: там есть и объёмы, и помесячная динамика.

Разбор опирается на разметку, которую сами же и генерируем (report_html.py):
    <span class="bar-platform">Подпись</span><span class="bar-format">«ключ»…
    <span class="chart-name">Подпись</span><span class="chart-volume">22 200 / мес</span>
    <text class="viz-tick" …>Авг ’25</text> … <text class="viz-value" …>14 800</text>
Если разметка изменится, восстановление вернёт None, а не тихо переврёт числа.
"""

from __future__ import annotations

import html as html_lib
import logging
import re

logger = logging.getLogger("pba.recover")

SECTION_RE = re.compile(r'<section class="card section">(.*?)</section>', re.S)
H2_RE = re.compile(r"<h2>([^<]+)</h2>")
BAR_RE = re.compile(
    r'<span class="bar-platform">(.*?)</span>'
    r'<span class="bar-format">«(.*?)»(.*?)</span>',
    re.S,
)
CHART_RE = re.compile(
    r'<span class="chart-name">(.*?)</span>'
    r'<span class="chart-volume[^"]*">(.*?)</span>(.*?)(?=<div class="chart-block">|$)',
    re.S,
)
TICK_RE = re.compile(r'<text class="viz-tick"[^>]*>([^<]+)</text>')
VALUE_RE = re.compile(r'<text class="viz-value"[^>]*>([^<]+)</text>')

BRAND_SECTIONS = ("Бренд и конкуренты в поиске", "Бренд в поиске")
CATEGORY_SECTION = "Спрос в категории"


def _text(raw: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _number(raw: str) -> int | None:
    digits = re.sub(r"[^\d]", "", raw.replace(" ", ""))
    return int(digits) if digits else None


def _trend(svg: str) -> list[dict]:
    """Подписи месяцев и значения над столбиками. Год проставлен только там, где он
    меняется, поэтому тянем его вперёд по списку."""
    ticks = [_text(t) for t in TICK_RE.findall(svg)]
    values = [_number(v) for v in VALUE_RE.findall(svg)]
    if not ticks or len(ticks) != len(values) or any(v is None for v in values):
        # Подписи могли быть прорежены при узком графике — тогда честнее отдать пусто,
        # чем собрать ряд не из тех месяцев.
        return []

    trend, year = [], None
    for tick, value in zip(ticks, values):
        parts = tick.split()
        if len(parts) > 1:
            year = 2000 + int(re.sub(r"[^\d]", "", parts[1]) or 0)
        trend.append({"month": parts[0], "year": year, "volume": value})
    return trend


def recover_demand(report_html: str) -> dict | None:
    """Возвращает demand в том же виде, в каком его отдаёт fetch_google_demand,
    или None, если в отчёте не нашлось блоков спроса."""
    items: list[dict] = []
    geo = "KZ"
    source = "Google Keyword Planner (через Apify)"

    geo_match = re.search(r"гео:\s*([A-Z]{2})", report_html)
    if geo_match:
        geo = geo_match.group(1)

    for section in SECTION_RE.findall(report_html):
        title_match = H2_RE.search(section)
        if not title_match:
            continue
        title = _text(title_match.group(1))
        if title in BRAND_SECTIONS:
            default_role = "competitor"
        elif title == CATEGORY_SECTION:
            default_role = "category"
        else:
            continue

        # Ключевое слово и роль — из баров, объём и динамика — из графиков.
        keywords: dict[str, tuple[str, str]] = {}
        for label_raw, keyword_raw, tail in BAR_RE.findall(section):
            label = _text(label_raw)
            role = "brand" if "наш бренд" in _text(tail) else default_role
            keywords[label] = (_text(keyword_raw), role)

        for label_raw, volume_raw, chart in CHART_RE.findall(section):
            label = _text(label_raw)
            keyword, role = keywords.get(label, (label.lower(), default_role))
            volume_text = _text(volume_raw)
            items.append(
                {
                    "keyword": keyword,
                    "label": label,
                    "role": role,
                    "volume": None if "нет данных" in volume_text else _number(volume_text),
                    "trend": _trend(chart),
                    "error": None if "нет данных" not in volume_text else "нет данных в исходном отчёте",
                }
            )

    if not items:
        return None

    from app.search_demand import _groups

    demand = {
        "source": source,
        "geo": geo,
        "available": any(i["volume"] is not None for i in items),
        "note": None,
        "items": items,
        "reused": True,
    }
    demand["groups"] = _groups(items)
    return demand
