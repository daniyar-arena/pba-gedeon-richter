"""Спрос по ключевым словам: Google Keyword Planner через Apify.

Почему через Apify, а не напрямую: собственный developer token Google Ads до сих пор
имеет уровень Explorer access, официальный API отвечает DEVELOPER_TOKEN_NOT_APPROVED.
Актор aitorsm/keyword-volume ходит в настоящий Keyword Planner под своим одобренным
токеном — данные реальные, просто через посредника ($0.012 за ключевое слово).

ВАЖНО про geo: буквенный код страны актор резолвит неверно ("kz" и "ru" дали одинаковые
объёмы на казахстанском бренде). Передаём числовой geoTargetConstant — 2398 для KZ.

ВАЖНО про достоверность: Keyword Planner уже подводил на кириллице (на паре
«стопдиар»/«диарея» бренд показал больше объёма, чем категория, что не подтвердилось
Wordstat). Поэтому каждое число в отчёте помечается источником, а если ответа нет —
пишем «нет данных», а не подставляем выдуманное значение.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger("pba.search_demand")

RUN_SYNC_URL = "https://api.apify.com/v2/acts/aitorsm~keyword-volume/run-sync-get-dataset-items"
REQUEST_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
MAX_PARALLEL = 3
TREND_MONTHS = 12

GEO_TARGET_CONSTANTS = {
    "KZ": 2398,
    "RU": 2643,
    "UZ": 2860,
    "KG": 2417,
    "AZ": 2031,
    "BY": 2112,
    "UA": 2804,
    "GE": 2268,
    "AM": 2051,
}

MONTH_LABELS = {
    "JANUARY": "Янв",
    "FEBRUARY": "Фев",
    "MARCH": "Мар",
    "APRIL": "Апр",
    "MAY": "Май",
    "JUNE": "Июн",
    "JULY": "Июл",
    "AUGUST": "Авг",
    "SEPTEMBER": "Сен",
    "OCTOBER": "Окт",
    "NOVEMBER": "Ноя",
    "DECEMBER": "Дек",
}


def _trend(monthly_searches: list) -> list[dict]:
    points = [
        {
            "month": MONTH_LABELS.get(p.get("month", ""), p.get("month", "")),
            "year": p.get("year"),
            "volume": int(p.get("monthly_searches") or p.get("monthlySearches") or 0),
        }
        for p in (monthly_searches or [])[-TREND_MONTHS:]
    ]
    return _trim_unavailable_tail(points)


def _trim_unavailable_tail(points: list[dict]) -> list[dict]:
    """Google регулярно отдаёт 0 за последние 1-2 месяца — это отчётная задержка,
    а не обвал спроса. Показывать такой хвост как факт нечестно, обрезаем."""
    if not any(p["volume"] for p in points):
        return points
    end = len(points)
    while end > 0 and points[end - 1]["volume"] == 0:
        end -= 1
    return points[:end]


async def _fetch_one(client: httpx.AsyncClient, token: str, keyword: str, geo_id: int) -> dict:
    result = {"keyword": keyword, "volume": None, "trend": [], "error": None}
    try:
        resp = await client.post(
            RUN_SYNC_URL,
            # Токен в заголовке, а не в query: httpx пишет URL запроса в лог, и токен
            # оказывался в логах сервера в открытом виде.
            headers={"Authorization": f"Bearer {token}"},
            json={
                "keywords": [keyword],
                "mode": "metrics",
                # Язык пустой (= все языки): актор принимает только один язык за раз,
                # а гео-таргетинг и так ограничивает страну.
                "geo": str(geo_id),
                "network": "GOOGLE_SEARCH",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json()
    except httpx.HTTPStatusError as exc:
        result["error"] = f"Apify ответил HTTP {exc.response.status_code}"
        logger.warning("apify http %s for %r", exc.response.status_code, keyword)
        return result
    except (httpx.HTTPError, ValueError) as exc:
        result["error"] = f"запрос не удался: {type(exc).__name__}"
        logger.warning("apify failed for %r: %s", keyword, exc)
        return result

    if not isinstance(items, list) or not items:
        result["error"] = "Keyword Planner не вернул данных по этому запросу"
        return result

    item = items[0]
    volume = item.get("search_volume")
    if volume is None:
        result["error"] = "Keyword Planner не вернул объём (обычно = слишком низкая частота)"
        return result

    result["volume"] = int(volume)
    result["trend"] = _trend(item.get("monthly_searches"))
    return result


BRAND_ROLES = ("brand", "competitor")


def _groups(items: list[dict]) -> dict:
    """Два блока: «бренд и конкуренты» (доля голоса в поиске) и «категория»
    (объём широких запросов и доля бренда в нём).

    Доли считаются только по тем ключам, где Google реально отдал объём: если по
    конкуренту данных нет, он не превращается в ноль и не завышает нашу долю —
    вместо этого блок помечает, что расчёт неполный.
    """
    brands = [i for i in items if i["role"] in BRAND_ROLES]
    category = [i for i in items if i["role"] == "category"]
    ours = next((i for i in brands if i["role"] == "brand" and i["volume"] is not None), None)

    brands_measured = [i for i in brands if i["volume"] is not None]
    brands_total = sum(i["volume"] for i in brands_measured)
    for item in brands:
        item["share"] = (item["volume"] / brands_total) if (brands_total and item["volume"]) else None
    ranked = sorted(brands_measured, key=lambda i: -i["volume"])
    for place, item in enumerate(ranked, start=1):
        item["rank"] = place

    category_measured = [i for i in category if i["volume"] is not None]
    category_total = sum(i["volume"] for i in category_measured)
    for item in category:
        item["share"] = (
            (item["volume"] / category_total) if (category_total and item["volume"]) else None
        )

    brand_in_category = None
    if ours and category_total:
        ratio = ours["volume"] / category_total
        brand_in_category = {
            "brand": ours["label"],
            "brand_volume": ours["volume"],
            "category_volume": category_total,
            "ratio": ratio,
            # Больше 1 бывает: бренд ищут чаще, чем категорию словами. Показываем «×»,
            # а не процент больше 100, который читался бы как опечатка.
            "display": f"{ratio:.1f}×" if ratio >= 1 else f"{ratio * 100:.1f}%",
        }

    return {
        "brands": {
            "items": brands,
            "total_volume": brands_total or None,
            "measured": len(brands_measured),
            "missing": len(brands) - len(brands_measured),
            "ours": ours["label"] if ours else None,
            "our_rank": ours.get("rank") if ours else None,
            "our_share": ours.get("share") if ours else None,
        },
        "category": {
            "items": category,
            "total_volume": category_total or None,
            "measured": len(category_measured),
            "missing": len(category) - len(category_measured),
        },
        "brand_in_category": brand_in_category,
    }

async def fetch_google_demand(
    keywords: list[dict], geo: str, apify_token: str | None
) -> dict:
    """keywords: [{'keyword': 'гроприносин', 'label': 'Гроприносин', 'role': 'brand'}]

    Роли: brand — наш бренд, competitor — бренд конкурента, category — широкий запрос
    категории, other — всё остальное. Роли задают два блока отчёта: доля голоса среди
    брендов и объём спроса в категории.

    Возвращает {'source', 'geo', 'available', 'note', 'items': [...]} — items в том же
    порядке, что и на входе, с volume=None там, где данных нет.
    """
    geo_id = GEO_TARGET_CONSTANTS.get((geo or "KZ").upper())
    base = {
        "source": "Google Keyword Planner (через Apify)",
        "geo": (geo or "KZ").upper(),
        "available": False,
        "note": None,
        "items": [],
    }

    if not keywords:
        base["note"] = "Ключевые слова не заданы — блок спроса пропущен."
        return base
    if geo_id is None:
        base["note"] = f"Нет geo-константы Google для «{geo}» — блок спроса пропущен."
        return base
    if not apify_token:
        base["note"] = (
            "Не задан APIFY_API_TOKEN — данные Google по ключевым словам не запрашивались. "
            "Впишите токен в файл .env и соберите отчёт заново."
        )
        return base

    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def guarded(client, kw):
        async with semaphore:
            return await _fetch_one(client, apify_token, kw["keyword"], geo_id)

    async with httpx.AsyncClient() as client:
        fetched = await asyncio.gather(*(guarded(client, kw) for kw in keywords))

    items = []
    for kw, data in zip(keywords, fetched):
        items.append(
            {
                "keyword": kw["keyword"],
                "label": kw.get("label") or kw["keyword"],
                "role": kw.get("role") or "other",
                "volume": data["volume"],
                "trend": data["trend"],
                "error": data["error"],
            }
        )

    base["items"] = items
    base["available"] = any(i["volume"] is not None for i in items)
    if not base["available"]:
        base["note"] = "Google не отдал объёмы ни по одному ключевому слову — см. пометки в таблице."
    elif any(i["error"] for i in items):
        base["note"] = "По части ключевых слов данных нет — они помечены отдельно."

    base["groups"] = _groups(items)
    return base
