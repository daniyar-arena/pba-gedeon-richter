"""Рендер отчёта в автономный HTML: стили и данные внутри файла, без внешних запросов.

Одна и та же функция рисует и страницу в браузере, и файл на скачивание — отличие
только в маленькой панели сверху (toolbar), которой в скачанном файле нет.
Визуальный язык взят из уже согласованных дэшбордов wordstat-dashboards.
"""

from __future__ import annotations

from html import escape

BRAND_COLOR = "#1f6feb"
CATEGORY_COLOR = "#8a6d3b"
OTHER_COLOR = "#5b6b7a"
RIVAL_COLOR = "#7c8b9c"

SEVERITY_LABEL = {"ok": "в норме", "watch": "на контроле", "risk": "требует решения"}


def weeks_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "неделя"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "недели"
    return "недель"


# ---------- форматирование ----------

def num(value, decimals: int | None = None) -> str:
    if value is None:
        return "—"
    if decimals is None:
        decimals = 0 if abs(value) >= 100 else 2
    return f"{value:,.{decimals}f}".replace(",", " ")


def money(value) -> str:
    if value is None:
        return "—"
    return f"{num(value, 0)} ₸"


def pct(value, digits: int = 1) -> str:
    if value is None:
        return "—"
    scaled = value * 100
    if abs(round(scaled, digits)) < 10 ** -digits / 2:
        return f"{0:.{digits}f}%"  # иначе округление нуля печатается как «-0.0%»
    return f"{scaled:+.{digits}f}%"


def pct_class(value, threshold: float = 0.02) -> str:
    if value is None:
        return "muted"
    if value < -threshold:
        return "neg"
    if value > threshold:
        return "pos"
    return ""


def esc(value) -> str:
    return escape(str(value if value is not None else ""))


# ---------- графики ----------

def bar_chart(
    trend: list[dict], color: str, width: int = 760, height: int = 260, font: int = 13
) -> str:
    """Столбики по месяцам. Крупные подписи и толстые бары — отчёт смотрят на встрече."""
    if not trend:
        return '<p class="muted">Нет данных для графика.</p>'

    pad_left, pad_right, pad_top, pad_bottom = 8, 8, 34, 44
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    max_v = max((p["volume"] for p in trend), default=0) or 1
    slot = plot_w / len(trend)
    bar_w = min(slot * 0.62, 64)

    years = {p.get("year") for p in trend if p.get("year")}
    show_year = len(years) > 1

    bars, labels, values = [], [], []
    for i, point in enumerate(trend):
        month_label = esc(point["month"])
        if show_year and point.get("year"):
            month_label += f" ’{str(point['year'])[-2:]}"
        h = plot_h * (point["volume"] / max_v)
        x = pad_left + slot * i + (slot - bar_w) / 2
        y = pad_top + plot_h - h
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{max(h, 2):.1f}" '
            f'rx="4" fill="{color}" />'
        )
        labels.append(
            f'<text class="viz-tick" x="{x + bar_w / 2:.1f}" y="{pad_top + plot_h + 26:.0f}" '
            f'text-anchor="middle" style="font-size:{font}px">{month_label}</text>'
        )
        values.append(
            f'<text class="viz-value" x="{x + bar_w / 2:.1f}" y="{y - 8:.1f}" '
            f'text-anchor="middle" style="font-size:{font}px">{num(point["volume"], 0)}</text>'
        )

    baseline = (
        f'<line class="viz-axis" x1="{pad_left}" y1="{pad_top + plot_h}" '
        f'x2="{width - pad_right}" y2="{pad_top + plot_h}" />'
    )
    return (
        f'<svg class="viz-svg" viewBox="0 0 {width} {height}" role="img">'
        f'{"".join(bars)}{baseline}{"".join(labels)}{"".join(values)}</svg>'
    )


# ---------- секции ----------

def render_ai(ai: dict) -> str:
    obs = "".join(
        f'''<div class="obs obs-{esc(o.get("severity", "watch"))}">
              <div class="obs-head">
                <span class="obs-title">{esc(o.get("title"))}</span>
                <span class="obs-sev">{esc(SEVERITY_LABEL.get(o.get("severity"), o.get("severity")))}</span>
              </div>
              <p class="obs-detail">{esc(o.get("detail"))}</p>
            </div>'''
        for o in ai.get("observations", [])
    )
    recs = "".join(f"<li>{esc(r)}</li>" for r in ai.get("recommendations", []))
    recs_block = f'<div class="sub-head">Что делать дальше</div><ul class="recs">{recs}</ul>' if recs else ""
    badge_class = "demo" if ai.get("is_fallback") else "live"

    return f'''
    <section class="card section">
      <div class="section-head">
        <h2>Выводы</h2>
        <span class="status-badge"><span class="status-dot {badge_class}"></span>{esc(ai.get("source"))}</span>
      </div>
      <p class="headline">{esc(ai.get("headline"))}</p>
      {obs}
      <div class="sub-head">Комментарий по спросу</div>
      <p class="obs-detail">{esc(ai.get("search_demand_comment"))}</p>
      {recs_block}
    </section>'''


def _demand_note(block: dict, what: str) -> str:
    """Если по части ключей Google молчит, доли считаются не по всем — говорим об этом
    прямо, иначе доля выглядит точнее, чем есть."""
    if not block["missing"]:
        return ""
    total = block["missing"] + block["measured"]
    return (
        f'<p class="footer-note">Доли посчитаны по {block["measured"]} из {total} '
        f"{what}: по остальным Google не дал объёмов, они помечены в списке.</p>"
    )


def _volume_bars(items: list[dict], kind: str) -> str:
    """Один бар на ключевое слово: объём запросов и доля внутри блока."""
    measured = [i for i in items if i["volume"] is not None]
    max_v = max((i["volume"] for i in measured), default=0) or 1

    lines = []
    for item in items:
        if item["role"] == "brand":
            bar_class, mark = "ours", '<span class="bar-mark">наш бренд</span>'
        elif kind == "brands":
            bar_class, mark = "rival", ""
        else:
            bar_class, mark = "cat", ""

        if item["volume"] is None:
            bar_html = '<div class="bar-hold"></div><span class="bar-tag warn">нет данных</span>'
            share_html = f'<span class="bar-share">{esc(item["error"] or "источник не ответил")}</span>'
        else:
            bar_html = (
                f'<div class="bar-hold"><div class="bar {bar_class}" '
                f'style="width:{item["volume"] / max_v * 100:.1f}%"></div></div>'
                f'<span class="bar-tag">{num(item["volume"], 0)}</span>'
            )
            share = item.get("share")
            share_html = (
                f'<span class="bar-share">{share * 100:.0f}% блока</span>' if share else ""
            )

        lines.append(
            '<div class="bar-row">'
            '<div class="bar-label">'
            f'<span class="bar-platform">{esc(item["label"])}</span>'
            f'<span class="bar-format">«{esc(item["keyword"])}»{mark}</span>'
            "</div>"
            f'<div class="bar-pair"><div class="bar-line">{bar_html}</div></div>'
            f'<div class="bar-delta">{share_html}</div>'
            "</div>"
        )
    return f'<div class="bars">{"".join(lines)}</div>'


def _trend_grid(items: list[dict], kind: str) -> str:
    charts = []
    for item in items:
        if not item["trend"]:
            continue
        color = (
            BRAND_COLOR
            if item["role"] == "brand"
            else RIVAL_COLOR
            if kind == "brands"
            else CATEGORY_COLOR
        )
        charts.append(
            '<div class="chart-block">'
            f'<div class="chart-title"><span class="swatch" style="background:{color}"></span>'
            f'{esc(item["label"])}</div>'
            f"{bar_chart(item['trend'], color, width=430, height=230, font=15)}"
            "</div>"
        )
    if not charts:
        return ""
    return f'<div class="charts-grid">{"".join(charts)}</div>'


def _demand_header(demand: dict, title: str) -> str:
    return (
        '<div class="section-head">'
        f"<h2>{title}</h2>"
        '<span class="status-badge"><span class="status-dot live"></span>'
        f'{esc(demand.get("source"))}, гео: {esc(demand.get("geo"))}</span>'
        "</div>"
    )


def _brands_section(demand: dict, block: dict) -> str:
    if not block["items"]:
        return ""
    rank_tile = ""
    if block["our_rank"] and block["measured"] > 1:
        rank_tile = (
            '<div class="stat-tile"><div class="label">Место по объёму запросов</div>'
            f'<div class="value">{block["our_rank"]} из {block["measured"]}</div>'
            '<div class="delta">среди брендов с данными</div></div>'
        )
    share_tile = ""
    if block["our_share"]:
        share_tile = (
            '<div class="stat-tile"><div class="label">Доля голоса в поиске</div>'
            f'<div class="value">{block["our_share"] * 100:.0f}%</div>'
            f'<div class="delta">{esc(block["ours"])} от всех брендовых запросов блока</div></div>'
        )
    total_tile = (
        '<div class="stat-tile"><div class="label">Всего брендовых запросов</div>'
        f'<div class="value">{num(block["total_volume"], 0)}</div>'
        '<div class="delta">в месяц, бренд + конкуренты</div></div>'
        if block["total_volume"]
        else ""
    )

    return (
        '<section class="card section">'
        + _demand_header(demand, "Бренд и конкуренты в поиске")
        + f'<div class="stats-row">{share_tile}{rank_tile}{total_tile}</div>'
        + _volume_bars(block["items"], "brands")
        + _demand_note(block, "брендов")
        + _trend_grid(block["items"], "brands")
        + "</section>"
    )


def _category_section(demand: dict, block: dict, brand_in_category: dict | None) -> str:
    if not block["items"]:
        return ""
    total_tile = (
        '<div class="stat-tile"><div class="label">Спрос в категории</div>'
        f'<div class="value">{num(block["total_volume"], 0)}</div>'
        f'<div class="delta">запросов в месяц по {block["measured"]} широким ключам</div></div>'
        if block["total_volume"]
        else ""
    )
    ratio_tile = (
        '<div class="stat-tile"><div class="label">Бренд к категории</div>'
        f'<div class="value">{esc(brand_in_category["display"])}</div>'
        f'<div class="delta">{esc(brand_in_category["brand"])}: '
        f'{num(brand_in_category["brand_volume"], 0)} к {num(brand_in_category["category_volume"], 0)}</div></div>'
        if brand_in_category
        else ""
    )

    return (
        '<section class="card section">'
        + _demand_header(demand, "Спрос в категории")
        + f'<div class="stats-row">{total_tile}{ratio_tile}</div>'
        + _volume_bars(block["items"], "category")
        + _demand_note(block, "запросов категории")
        + _trend_grid(block["items"], "category")
        + "</section>"
    )


def render_demand(demand: dict) -> str:
    if not demand.get("items"):
        return (
            '<section class="card section"><h2>Спрос по ключевым словам</h2>'
            f'<p class="muted">{esc(demand.get("note") or "Блок не запрашивался.")}</p></section>'
        )

    groups = demand.get("groups") or {}
    brands = groups.get("brands", {"items": [], "missing": 0, "measured": 0})
    category = groups.get("category", {"items": [], "missing": 0, "measured": 0})
    others = [i for i in demand["items"] if i["role"] not in ("brand", "competitor", "category")]

    note = f'<div class="callout warn">{esc(demand["note"])}</div>' if demand.get("note") else ""
    sections = [
        _brands_section(demand, brands),
        _category_section(demand, category, groups.get("brand_in_category")),
    ]
    if others:
        sections.append(
            '<section class="card section">'
            + _demand_header(demand, "Другие запросы")
            + _volume_bars(others, "other")
            + _trend_grid(others, "other")
            + "</section>"
        )

    body = "".join(s for s in sections if s)
    disclaimer = (
        '<p class="footer-note">Источник — Google Keyword Planner: показывает спрос в Google,'
        " не весь рынок. Хвост последних месяцев обрезается, если Google отдал по ним нули"
        " (отчётная задержка, а не падение спроса). По кириллическим запросам Keyword Planner"
        " уже путал объёмы бренда и категории — расстановку сил стоит перепроверять по Wordstat.</p>"
    )
    return note + body + disclaimer


def _delta_badge(value, threshold: float = 0.02) -> str:
    """Отклонение бейджем: в пределах ±2% — серый (норма размещения), дальше цветной."""
    if value is None:
        return '<span class="badge flat">—</span>'
    cls = pct_class(value, threshold) or "flat"
    return f'<span class="badge {cls}">{pct(value)}</span>'


def _placement_bars(rows: list[dict], total_fact: float) -> str:
    """Горизонтальные бары «план vs факт» по бюджету. Структура расхода и промахи
    видны раньше, чем читаются цифры — таблица ниже нужна для точных значений."""
    max_v = max(max(r["budget_plan"] or 0, r["budget_fact"] or 0) for r in rows) or 1

    lines = []
    for r in rows:
        plan = r["budget_plan"] or 0
        fact = r["budget_fact"] or 0
        not_started = plan > 0 and fact == 0
        share = fact / total_fact if total_fact else None

        if not_started:
            fact_line = (
                '<div class="bar-line"><div class="bar-hold"></div>'
                '<span class="bar-tag warn">не стартовало</span></div>'
            )
        else:
            fact_line = (
                f'<div class="bar-line"><div class="bar-hold">'
                f'<div class="bar fact" style="width:{fact / max_v * 100:.1f}%"></div></div>'
                f'<span class="bar-tag">{money(fact)}</span></div>'
            )

        share_html = f'<span class="bar-share">доля {share * 100:.0f}%</span>' if share else ""
        lines.append(
            '<div class="bar-row">'
            '<div class="bar-label">'
            f'<span class="bar-platform">{esc(r["platform"])}</span>'
            f'<span class="bar-format">{esc(r["format"])} &middot; {esc(r["buy_model"])}</span>'
            "</div>"
            '<div class="bar-pair">'
            '<div class="bar-line"><div class="bar-hold">'
            f'<div class="bar plan" style="width:{plan / max_v * 100:.1f}%"></div></div>'
            f'<span class="bar-tag muted">{money(plan)}</span></div>'
            f"{fact_line}"
            "</div>"
            f'<div class="bar-delta">{_delta_badge(r["budget_pct"])}{share_html}</div>'
            "</div>"
        )

    legend = (
        '<div class="bars-legend">'
        '<span class="key plan"></span>план'
        '<span class="key fact"></span>факт'
        '<span class="muted small">бюджет с НДС и АК, длина бара — доля от самой '
        "крупной площадки</span></div>"
    )
    return legend + f'<div class="bars">{"".join(lines)}</div>'


def _placement_table(rows: list[dict], total_plan: float, total_fact: float) -> str:
    """Компактная таблица: план уже виден на барах, здесь факт, доля и отклонения."""
    body = []
    for r in rows:
        plan = r["budget_plan"] or 0
        fact = r["budget_fact"] or 0
        share = fact / total_fact if total_fact else None
        if plan > 0 and fact == 0:
            budget_cell = '<td class="num warn-text">не стартовало</td><td class="num muted">—</td>'
        else:
            share_text = f"{share * 100:.0f}%" if share else "—"
            budget_cell = (
                f'<td class="num">{money(fact)}</td>'
                f'<td class="num muted">{share_text}</td>'
            )
        body.append(
            "<tr>"
            f'<td><span class="cell-main">{esc(r["platform"])}</span>'
            f'<span class="cell-sub">{esc(r["format"])}</span></td>'
            f'<td class="muted">{esc(r["buy_model"])}</td>'
            f"{budget_cell}"
            f'<td class="num">{_delta_badge(r["budget_pct"])}</td>'
            f'<td class="num">{num(r["kpi_fact"])}</td>'
            f'<td class="num">{_delta_badge(r["kpi_pct"])}</td>'
            f'<td class="num">{num(r["unit_cost_fact"], 2)}</td>'
            f'<td class="num">{_delta_badge(r["unit_cost_pct"])}</td>'
            "</tr>"
        )

    total_pct = (total_fact - total_plan) / total_plan if total_plan else None
    total_row = (
        '<tr class="total-row"><td colspan="2">Всего по закрытым неделям</td>'
        f'<td class="num">{money(total_fact)}</td><td class="num muted">100%</td>'
        f'<td class="num">{_delta_badge(total_pct)}</td>'
        '<td class="num muted">—</td><td class="num muted">—</td>'
        '<td class="num muted">—</td><td class="num muted">—</td></tr>'
    )

    # Двухъярусная шапка: иначе три колонки «к плану» подряд читаются неоднозначно.
    head = (
        "<thead>"
        '<tr><th rowspan="2">Площадка</th><th rowspan="2">Закупка</th>'
        '<th colspan="3">Бюджет с НДС и АК</th>'
        '<th colspan="2">KPI</th>'
        '<th colspan="2">Цена единицы</th></tr>'
        '<tr><th class="num">факт</th><th class="num">доля</th><th class="num">к плану</th>'
        '<th class="num">факт</th><th class="num">к плану</th>'
        '<th class="num">факт</th><th class="num">к плану</th></tr>'
        "</thead>"
    )
    return (
        '<div class="table-wrap"><table class="compact">'
        f'{head}<tbody>{"".join(body)}{total_row}</tbody>'
        "</table></div>"
    )


def render_placements(rows: list[dict]) -> str:
    if not rows:
        return '<p class="muted">Закрытых недель нет — сводить факт по площадкам пока не на чем.</p>'
    total_plan = sum(r["budget_plan"] or 0 for r in rows)
    total_fact = sum(r["budget_fact"] or 0 for r in rows)
    return _placement_bars(rows, total_fact) + _placement_table(rows, total_plan, total_fact)


def _week_table(week: dict, is_closed: bool) -> str:
    body = []
    for r in week["rows"]:
        if is_closed and r["kpi_fact"] is not None:
            kpi = (
                f'<td class="num">{num(r["kpi_plan"])}</td>'
                f'<td class="num">{num(r["kpi_fact"])}</td>'
                f'<td class="num {pct_class(r["kpi_pct"])}">{pct(r["kpi_pct"])}</td>'
            )
            budget = (
                f'<td class="num">{money(r["budget_plan"])}</td>'
                f'<td class="num">{money(r["budget_fact"])}</td>'
                f'<td class="num {pct_class(r["budget_pct"])}">{pct(r["budget_pct"])}</td>'
            )
        else:
            kpi = (
                f'<td class="num">{num(r["kpi_plan"])}</td>'
                '<td class="num muted">нет данных</td><td class="num muted">—</td>'
            )
            budget = (
                f'<td class="num">{money(r["budget_plan"])}</td>'
                '<td class="num muted">нет данных</td><td class="num muted">—</td>'
            )
        body.append(
            f'<tr><td>{esc(r["platform"])}</td><td>{esc(r["format"])}</td>'
            f'<td class="muted">{esc(r["buy_model"])}</td>{kpi}{budget}</tr>'
        )

    total = week["total"]
    if is_closed:
        total_row = (
            f'<tr class="total-row"><td colspan="3">Всего</td>'
            f'<td class="num">{num(total["kpi_plan"])}</td>'
            f'<td class="num">{num(total["kpi_fact"])}</td>'
            f'<td class="num {pct_class(total["kpi_pct"])}">{pct(total["kpi_pct"])}</td>'
            f'<td class="num">{money(total["budget_plan"])}</td>'
            f'<td class="num">{money(total["budget_fact"])}</td>'
            f'<td class="num {pct_class(total["budget_pct"])}">{pct(total["budget_pct"])}</td></tr>'
        )
    else:
        total_row = (
            f'<tr class="total-row"><td colspan="3">Всего</td>'
            f'<td class="num">{num(total["kpi_plan"])}</td>'
            '<td class="num muted">—</td><td class="num muted">—</td>'
            f'<td class="num">{money(total["budget_plan"])}</td>'
            '<td class="num muted">—</td><td class="num muted">—</td></tr>'
        )

    badge = (
        '<span class="status-badge"><span class="status-dot live"></span>неделя закрыта</span>'
        if is_closed
        else '<span class="status-badge"><span class="status-dot demo"></span>неделя не завершена</span>'
    )
    return f'''
    <div class="week-block">
      <div class="section-head">
        <h3>{esc(week["label"])} <span class="date-range">{esc(week["date_range"] or "")}</span></h3>
        {badge}
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th rowspan="2">Платформа</th><th rowspan="2">Формат</th><th rowspan="2">Закупка</th>
              <th colspan="3">KPI</th><th colspan="3">Бюджет с НДС и АК</th></tr>
            <tr><th class="num">план</th><th class="num">факт</th><th class="num">%</th>
              <th class="num">план</th><th class="num">факт</th><th class="num">%</th></tr>
          </thead>
          <tbody>{"".join(body)}{total_row}</tbody>
        </table>
      </div>
    </div>'''


def render_pba(month: dict) -> str:
    s = month["summary"]
    closed = ", ".join(str(n) for n in month["closed_week_numbers"]) or "нет"
    pending = ", ".join(str(n) for n in month["pending_week_numbers"]) or "нет"
    delivery = s["delivery_pct"]
    delivery_class = (
        "" if delivery is None or abs(delivery - 1) <= 0.02 else ("neg" if delivery < 1 else "pos")
    )

    if month["pending_week_numbers"]:
        callout_text = (
            f"Закрыты (есть факт): <strong>недели {esc(closed)}</strong>. "
            f"Не завершены или не отчитаны: <strong>недели {esc(pending)}</strong> — "
            "по ним показан только план. Процент от плана по месяцу целиком сейчас ничего "
            "не говорит: часть бюджета просто ещё не наступила. Реальный темп — "
            "по закрытым неделям."
        )
        pending_tile = (
            '<div class="stat-tile"><div class="label">Осталось по плану</div>'
            f'<div class="value">{money(s["plan_pending"])}</div>'
            f'<div class="delta">недели {esc(pending)}</div></div>'
        )
    else:
        callout_text = (
            f"Месяц отчитан полностью: закрыты все {s['total_weeks']} "
            f"{weeks_word(s['total_weeks'])}, факт есть по каждой. "
            "Выполнение ниже — по всему месяцу."
        )
        pending_tile = ""

    weeks_html = "".join(
        _week_table(w, w["week_number"] in month["closed_week_numbers"]) for w in month["weeks"]
    )
    placement_html = render_placements(month["by_placement_closed"])

    return f'''
    <section class="card section">
      <div class="section-head"><h2>ПБА — {esc(month["label"])}</h2></div>
      <div class="callout">{callout_text}</div>
      <div class="stats-row">
        <div class="stat-tile">
          <div class="label">План на месяц</div>
          <div class="value">{money(s["plan_month"])}</div>
          <div class="delta">все {s["total_weeks"]} {weeks_word(s["total_weeks"])}</div>
        </div>
        <div class="stat-tile">
          <div class="label">Факт по закрытым неделям</div>
          <div class="value">{money(s["fact_closed"])}</div>
          <div class="delta">план на эти недели: {money(s["plan_closed"])}</div>
        </div>
        <div class="stat-tile">
          <div class="label">Выполнение по закрытым неделям</div>
          <div class="value {delivery_class}">{"—" if delivery is None else f"{delivery * 100:.1f}%"}</div>
          <div class="delta">{s["closed_weeks"]} из {s["total_weeks"]} недель</div>
        </div>
        {pending_tile}
      </div>

      <div class="sub-head">Сводно по площадкам — только закрытые недели</div>
      <p class="muted small">KPI суммируется внутри одной модели закупки: просмотры, показы
      и клики между собой не складываются. Цена единицы — бюджет с НДС и АК на 1000 показов
      (CPM) или на просмотр/клик (CPV, CPC).</p>
      {placement_html}

      <div class="sub-head">По неделям</div>
      {weeks_html}
    </section>'''


# ---------- страница ----------

CSS = """
  :root {
    color-scheme: light;
    --surface-1: #fcfcfb;
    --page: #f9f9f7;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #898781;
    --grid: #e1e0d9;
    --axis: #c3c2b7;
    --border: rgba(11,11,11,0.10);
    --pos: #0ca30c;
    --neg: #d02b2b;
    --bar-plan: #ccd8e8;
    --bar-fact: #1f6feb;
    --bar-rival: #8b9aab;
    --bar-cat: #a3813f;
    --warn-text: #a86500;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface-1: #1a1a19;
      --page: #0d0d0d;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #898781;
      --grid: #2c2c2a;
      --axis: #383835;
      --border: rgba(255,255,255,0.10);
      --pos: #45c745;
      --neg: #ff6b6b;
      --bar-plan: #313f57;
      --bar-fact: #5a9bff;
      --bar-rival: #7b8798;
      --bar-cat: #c19a55;
      --warn-text: #f0b24a;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 28px 16px 60px;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page);
    color: var(--text-primary);
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .wrap { max-width: 1180px; margin: 0 auto; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  h2 { font-size: 19px; margin: 0; }
  h3 { font-size: 15px; margin: 0; }
  .subtitle { color: var(--text-secondary); font-size: 14px; margin: 0 0 22px; }
  .card {
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 22px;
  }
  .section { margin-bottom: 22px; }
  .section-head { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
  .sub-head {
    font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
    color: var(--text-muted); margin: 26px 0 8px;
  }
  .callout {
    background: color-mix(in srgb, #1f6feb 8%, var(--surface-1));
    border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 16px; font-size: 13px;
    color: var(--text-secondary); margin-bottom: 18px; line-height: 1.5;
  }
  .callout.warn { background: color-mix(in srgb, #fab219 14%, var(--surface-1)); }
  .stats-row { display: flex; gap: 12px; flex-wrap: wrap; }
  .stat-tile {
    background: var(--page); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 18px; flex: 1; min-width: 210px;
  }
  .stat-tile .label {
    font-size: 12px; color: var(--text-muted); margin-bottom: 6px;
    display: flex; align-items: center; gap: 7px;
  }
  .stat-tile .value { font-size: 25px; font-weight: 650; font-variant-numeric: tabular-nums; }
  .stat-tile .delta { font-size: 12px; color: var(--text-secondary); margin-top: 3px; }
  .swatch { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
  .headline { font-size: 17px; font-weight: 600; line-height: 1.45; margin: 0 0 18px; }
  .obs { border-left: 3px solid var(--axis); padding: 2px 0 2px 14px; margin-bottom: 14px; }
  .obs-ok { border-left-color: var(--pos); }
  .obs-watch { border-left-color: #fab219; }
  .obs-risk { border-left-color: var(--neg); }
  .obs-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
  .obs-title { font-weight: 600; font-size: 14px; }
  .obs-sev {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.04em;
    color: var(--text-muted); font-weight: 700;
  }
  .obs-detail { font-size: 13px; color: var(--text-secondary); margin: 4px 0 0; line-height: 1.55; }
  .recs { margin: 6px 0 0; padding-left: 20px; font-size: 13px; color: var(--text-secondary); line-height: 1.6; }
  .status-badge {
    display: inline-flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-secondary); margin-left: auto;
  }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .status-dot.demo { background: #fab219; }
  .status-dot.live { background: #0ca30c; }
  .table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 9px 11px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
  thead th {
    background: var(--page); font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.04em; color: var(--text-muted); font-weight: 700;
  }
  tbody tr:last-child td { border-bottom: none; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .total-row td { font-weight: 700; background: var(--page); }
  .muted { color: var(--text-muted); }
  .small { font-size: 12px; }
  .neg { color: var(--neg); }
  .pos { color: var(--pos); }
  .date-range { color: var(--text-muted); font-weight: 400; font-size: 13px; }
  .week-block { margin-bottom: 20px; }
  .chart-block { margin-top: 22px; }
  .chart-title { font-size: 13px; font-weight: 600; display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .viz-svg { width: 100%; height: auto; display: block; overflow: visible; }
  .viz-axis { stroke: var(--axis); stroke-width: 1.5; }
  .viz-tick { fill: var(--text-secondary); font-size: 13px; }
  .viz-value { fill: var(--text-primary); font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .footer-note { font-size: 12px; color: var(--text-muted); margin-top: 16px; line-height: 1.55; }
  .toolbar {
    max-width: 1180px; margin: 0 auto 18px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
  }
  .btn {
    display: inline-block; padding: 9px 16px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--surface-1); color: var(--text-primary); font-size: 13px; font-weight: 600;
    text-decoration: none; cursor: pointer;
  }
  .btn.primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }

  /* Сводка по площадкам: бары «план vs факт» + компактная таблица под ними */
  .bars-legend {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    font-size: 12px; color: var(--text-secondary); margin: 6px 0 16px;
  }
  .key { width: 15px; height: 10px; border-radius: 3px; display: inline-block; }
  .key.plan { background: var(--bar-plan); }
  .key.fact { background: var(--bar-fact); }
  .bars { display: flex; flex-direction: column; gap: 15px; margin-bottom: 26px; }
  .bar-row {
    display: grid; grid-template-columns: minmax(140px, 210px) 1fr minmax(92px, auto);
    gap: 18px; align-items: center;
  }
  .bar-label { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .bar-platform { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
  .bar-format { font-size: 11.5px; color: var(--text-muted); }
  .bar-pair { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .bar-line { display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: center; }
  .bar-hold { min-width: 0; }
  .bar { height: 15px; border-radius: 4px; min-width: 2px; }
  .bar.plan { background: var(--bar-plan); }
  .bar.fact { background: var(--bar-fact); }
  .bar-tag { font-size: 12.5px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .bar-tag.warn { color: var(--warn-text); font-weight: 600; }
  .bar-delta { display: flex; flex-direction: column; gap: 3px; align-items: flex-end; }
  .bar-share { font-size: 11px; color: var(--text-muted); white-space: nowrap; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 12px;
    font-weight: 700; font-variant-numeric: tabular-nums;
  }
  .badge.flat { background: var(--page); color: var(--text-muted); border: 1px solid var(--border); }
  .badge.pos { background: color-mix(in srgb, var(--pos) 16%, transparent); color: var(--pos); }
  .badge.neg { background: color-mix(in srgb, var(--neg) 16%, transparent); color: var(--neg); }
  .cell-main { display: block; font-weight: 600; }
  .cell-sub { display: block; font-size: 11.5px; color: var(--text-muted); }
  .warn-text { color: var(--warn-text); font-weight: 600; }
  table.compact th, table.compact td { padding: 8px 11px; }
  @media (max-width: 720px) {
    .bar-row { grid-template-columns: 1fr; gap: 6px; }
    .bar-delta { align-items: flex-start; flex-direction: row; gap: 10px; }
  }
  .bar.ours { background: var(--bar-fact); }
  .bar.rival { background: var(--bar-rival); }
  .bar.cat { background: var(--bar-cat); }
  .bar-mark {
    margin-left: 8px; padding: 1px 6px; border-radius: 5px; font-size: 10px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.03em;
    background: color-mix(in srgb, var(--bar-fact) 18%, transparent); color: var(--bar-fact);
  }
  .charts-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
    gap: 10px 26px; margin-top: 18px;
  }
  @media print {
    .toolbar { display: none; }
    body { padding: 0; background: #fff; }
  }
"""


def render_report(report: dict, *, job_id: str | None = None) -> str:
    month = report["month"]
    brand = report.get("brand") or "Бренд не указан в файле"
    client = report.get("client") or ""
    period = month["meta"].get("period") or ""

    toolbar = (
        f'''<div class="toolbar">
              <a class="btn primary" href="/api/report/{job_id}/download">Скачать HTML</a>
              <a class="btn" href="/">Собрать другой отчёт</a>
              <span class="muted small">Скачанный файл автономный: открывается без сервера, можно отправить клиенту.</span>
            </div>'''
        if job_id
        else ""
    )

    title = f"ПБА {brand} — {month['label']}"
    return f'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
{toolbar}
<div class="wrap">
  <h1>{esc(brand)} — ПБА {esc(month["label"])}</h1>
  <p class="subtitle">{esc(client)}{" &middot; " if client else ""}период в файле: {esc(period or "не указан")}
    &middot; исходник: {esc(report.get("source_file") or "—")}
    &middot; собрано {esc(report.get("generated_at") or "")}</p>

  {render_ai(report["ai"])}
  {render_pba(month)}
  {render_demand(report["demand"])}

  <p class="footer-note">
    Бюджеты — тенге с НДС и агентской комиссией, как в исходном файле ПБА.
    Факт берётся только из тех недель, где он заполнен; недели без факта помечены и в
    расчёт выполнения не входят. Данные по спросу — внешний источник, помечен в своём блоке.
    Исполнитель: ТОО «Arena Media Kazakhstan».
  </p>
</div>
</body>
</html>'''
