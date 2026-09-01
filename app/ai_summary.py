"""Вывод от Клода по данным ПБА + спросу. Структурированный ответ через tool use.

Без ANTHROPIC_API_KEY работает rule-based fallback: те же поля, посчитанные из данных
арифметикой. Отчёт остаётся собираемым, но в нём честно написано, что выводы не от Клода.

Главное правило промпта — честность отчёта (его читает клиент): не считать незакрытые
недели недоосвоением, не додумывать цифры, которых нет в данных, и прямо писать «нет
данных», если источник молчит.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("pba.ai")


def _n(value, decimals: int = 0) -> str:
    """Число с пробелом в разряде тысяч. Отдельной функцией, потому что .replace(',', ' ')
    по всей строке съедал бы запятые в самом тексте наблюдения."""
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ")


def _weeks_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "неделя"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "недели"
    return "недель"


DEFAULT_MODEL = os.getenv("PBA_AI_MODEL", "claude-sonnet-5")

SUMMARY_TOOL = {
    "name": "submit_summary",
    "description": "Вернуть структурированный вывод по кампании.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "Один короткий вывод по кампании за месяц, до 200 знаков.",
            },
            "observations": {
                "type": "array",
                "description": "3-6 наблюдений, каждое с конкретным числом из данных.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Суть, до 80 знаков."},
                        "detail": {
                            "type": "string",
                            "description": "Пояснение с цифрами и причиной, 1-3 предложения.",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["ok", "watch", "risk"],
                            "description": "ok — всё в норме, watch — держать на контроле, risk — требует решения.",
                        },
                    },
                    "required": ["title", "detail", "severity"],
                },
            },
            "search_demand_comment": {
                "type": "string",
                "description": (
                    "2-4 предложения по спросу: сначала наш бренд против конкурентов "
                    "(доля голоса, место), затем спрос в категории и доля бренда в ней. "
                    "Если данных нет — так и написать, без выводов."
                ),
            },
            "recommendations": {
                "type": "array",
                "description": "2-4 действия на следующий период, каждое проверяемое.",
                "items": {"type": "string"},
            },
        },
        "required": ["headline", "observations", "search_demand_comment", "recommendations"],
    },
}

SYSTEM_PROMPT = """Ты медиа-аналитик агентства Arena Media Kazakhstan. Пишешь блок выводов
в отчёт ПБА (план-бюджет-анализ), который читает клиент — фармкомпания Gedeon Richter.

Правила, которые важнее полноты:
1. Никогда не выдумывай числа. Пиши только те, что есть во входном JSON.
2. Незакрытые недели (pending) — это недели, которые ещё не наступили или не отчитаны.
   Их отсутствующий факт НЕ является недоосвоением бюджета. Темп исполнения оценивай
   только по закрытым неделям (поле summary.delivery_pct и plan_closed/fact_closed).
3. Отклонение в пределах ±2% по бюджету или KPI — это норма размещения, а не проблема.
   Не называй такое «перерасходом» или «недобором».
4. Если данных по спросу нет (search_demand.available = false) — прямо скажи, что
   Google по этим ключевым словам данных не дал, и не делай выводов о спросе.
5. Данные Google Keyword Planner по кириллическим запросам подводили раньше (бренд
   показывался больше категории). Если соотношение выглядит подозрительно, отметь это
   как «требует проверки по Wordstat», а не как факт о рынке.
5а. Блок search_demand.groups.brands — наш бренд и конкуренты: доля голоса в поиске
   (our_share), место (our_rank) и объёмы. Доля считается только по ключам, где Google
   дал объём: если groups.brands.missing > 0, скажи, что расстановка неполная, и не
   выдавай долю за точную. Это спрос в Google, а не доля рынка — так и называй.
5б. Блок search_demand.groups.category — широкие запросы категории и доля бренда в
   ней (brand_in_category). Прокомментируй и сезонность по trend, если она видна.
6. К каждой динамике давай причину или гипотезу с числом, а не только констатацию.
7. Язык — живой рабочий русский, как пишет медиапланер коллеге. Без канцелярита,
   без «в рамках», «осуществляется», «наблюдается положительная динамика».

Отвечай только вызовом инструмента submit_summary."""


def _prompt_payload(month: dict, demand: dict, client: str, brand: str) -> dict:
    """Оставляем в промпте только то, на чём можно делать выводы, — без html и мусора."""
    return {
        "client": client,
        "brand": brand,
        "month": month["label"],
        "summary": month["summary"],
        "closed_weeks": month["closed_week_numbers"],
        "pending_weeks": month["pending_week_numbers"],
        "weeks": [
            {
                "label": w["label"],
                "date_range": w["date_range"],
                "closed": w["week_number"] in month["closed_week_numbers"],
                "total": w["total"],
                "rows": w["rows"],
            }
            for w in month["weeks"]
        ],
        "by_placement_closed": month["by_placement_closed"],
        "search_demand": {
            "available": demand.get("available"),
            "source": demand.get("source"),
            "geo": demand.get("geo"),
            "note": demand.get("note"),
            "groups": demand.get("groups"),
            "items": [
                {
                    "label": i["label"],
                    "keyword": i["keyword"],
                    "role": i["role"],
                    "volume": i["volume"],
                    "trend": i["trend"],
                    "error": i["error"],
                }
                for i in demand.get("items", [])
            ],
        },
        "notes": {
            "budget_units": "тенге, с НДС и агентской комиссией",
            "kpi_units": "CPV — просмотры, CPM — показы, CPC — клики",
        },
    }


async def build_ai_summary(month: dict, demand: dict, client: str, brand: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback(month, demand, reason="не задан ANTHROPIC_API_KEY")

    try:
        from anthropic import AsyncAnthropic

        anthropic = AsyncAnthropic(api_key=api_key)
        payload = _prompt_payload(month, demand, client, brand)
        response = await anthropic.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=[SUMMARY_TOOL],
            tool_choice={"type": "tool", "name": "submit_summary"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Вот данные ПБА и спроса. Собери блок выводов.\n\n"
                        + json.dumps(payload, ensure_ascii=False, default=str)
                    ),
                }
            ],
        )
    except Exception as exc:  # сеть, ключ, лимиты — отчёт всё равно должен собраться
        logger.warning("claude call failed: %s: %s", type(exc).__name__, exc)
        return _fallback(month, demand, reason=f"Клод не ответил ({type(exc).__name__})")

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_summary":
            data = dict(block.input)
            data["source"] = f"Claude ({DEFAULT_MODEL})"
            data["is_fallback"] = False
            return data

    return _fallback(month, demand, reason="Клод не вернул структурированный ответ")


def _fallback(month: dict, demand: dict, reason: str) -> dict:
    """Арифметический разбор без Клода — те же поля, только без интерпретации."""
    s = month["summary"]
    pct = s["delivery_pct"]
    observations = []

    if s["closed_weeks"] == 0:
        observations.append(
            {
                "title": "Закрытых недель ещё нет",
                "detail": "Ни в одной неделе нет факта бюджета — оценивать исполнение пока не на чем.",
                "severity": "watch",
            }
        )
    else:
        delta = (pct - 1) * 100 if pct else 0
        severity = "ok" if abs(delta) <= 2 else ("watch" if abs(delta) <= 10 else "risk")
        observations.append(
            {
                "title": f"Исполнение по закрытым неделям — {pct * 100:.1f}%" if pct else "Исполнение",
                "detail": (
                    f"Факт {_n(s['fact_closed'])} ₸ против плана {_n(s['plan_closed'])} ₸ "
                    f"по {s['closed_weeks']} из {s['total_weeks']} недель. "
                    f"Отклонение {delta:+.1f}%."
                ),
                "severity": severity,
            }
        )

    if month["pending_week_numbers"]:
        weeks = ", ".join(str(n) for n in month["pending_week_numbers"])
        observations.append(
            {
                "title": f"Недели {weeks} ещё не отчитаны",
                "detail": (
                    f"На них запланировано {_n(s['plan_pending'])} ₸ — это не недоосвоение, "
                    "факта по ним пока просто нет."
                ),
                "severity": "watch",
            }
        )

    worst = sorted(
        (p for p in month["by_placement_closed"] if p["kpi_pct"] is not None),
        key=lambda p: abs(p["kpi_pct"]),
        reverse=True,
    )[:2]
    for p in worst:
        if abs(p["kpi_pct"]) <= 0.02:
            continue
        observations.append(
            {
                "title": f"{p['platform']} / {p['format']}: KPI {p['kpi_pct'] * 100:+.1f}%",
                "detail": (
                    f"План {_n(p['kpi_plan'])}, факт {_n(p['kpi_fact'])} "
                    f"по модели {p['buy_model']}."
                ),
                "severity": "watch" if abs(p["kpi_pct"]) < 0.1 else "risk",
            }
        )

    if demand.get("available"):
        groups = demand.get("groups") or {}
        brands = groups.get("brands") or {}
        category = groups.get("category") or {}
        parts = []
        if brands.get("our_share"):
            parts.append(
                f"{brands['ours']}: {brands['our_share'] * 100:.0f}% брендовых запросов блока"
                f" (место {brands['our_rank']} из {brands['measured']})"
            )
        parts += [
            f"{i['label']}: {_n(i['volume'])}"
            for i in brands.get("items", [])
            if i["volume"] is not None and i["role"] != "brand"
        ]
        if category.get("total_volume"):
            parts.append(f"категория: {_n(category['total_volume'])} запросов/мес")
        ratio = groups.get("brand_in_category")
        if ratio:
            parts.append(f"бренд к категории — {ratio['display']}")
        if brands.get("missing"):
            parts.append(f"без данных ключей: {brands['missing']}")
        demand_comment = "; ".join(parts) or "нет данных"
    else:
        demand_comment = demand.get("note") or "Данных по спросу нет."

    return {
        "headline": (
            f"Исполнение по закрытым неделям {pct * 100:.1f}% от плана."
            if pct
            else "Закрытых недель пока нет — исполнение оценивать рано."
        ),
        "observations": observations,
        "search_demand_comment": demand_comment,
        "recommendations": [],
        "source": f"Без Клода ({reason}) — только арифметика по данным файла",
        "is_fallback": True,
    }
