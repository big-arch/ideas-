from __future__ import annotations

from html import escape
from datetime import timedelta

from .analysis import Assessment, assess, in_scope, money
from .config import Search
from .models import Listing, duplicate_key, timestamp, utc_now
from .dates import MSK


def price_label(result: Assessment) -> str:
    if result.discount is None or result.status in {"insufficient", "excluded"}:
        return "Пока мало похожих объявлений для сравнения"
    percentage = abs(result.discount) * 100
    if percentage < 0.5:
        return "Примерно на уровне похожих предложений"
    if result.discount > 0:
        scope = " по городу" if result.comparison_scope == "city" else ""
        return f"Дешевле похожих{scope} на {percentage:.0f}%"
    scope = " по городу" if result.comparison_scope == "city" else ""
    return f"Дороже похожих{scope} на {percentage:.0f}%"


def caption(result: Assessment) -> str:
    item = result.listing
    address = item.attributes.get("visible_address")
    if not isinstance(address, str) or not address.strip():
        address = ", ".join(part for part in (item.locality, item.district.replace("улица: ", "")) if part)
    elif item.locality not in address:
        address = item.locality + ", " + address
    lines = (["<i>Демонстрация, вымышленные данные</i>"] if item.demo else []) + [
        f"<b>{escape(item.title[:160])}</b>",
        f"<b>{escape(money(item.price_rub))}</b>",
        "", "📍 " + escape(address[:220]), "", "<b>" + escape(price_label(result)) + "</b>",
    ]
    if result.reference_price is not None and result.status not in {"insufficient", "excluded"}:
        lines.append(f"Похожие предложения: около {money(result.reference_price)} · {len(result.comparable_ids)} аналогов")
        lines.append("<i>Предварительно, по городу, без поправки на район</i>" if result.comparison_scope == "city"
                     else "<i>Предварительно, по ценам объявлений</i>")
    if result.urgency_claim:
        lines.append("Срочная продажа, по словам продавца")
    if item.attributes.get("site_date_at"):
        try:
            lines.append("Дата на сайте: " + timestamp(item.attributes["site_date_at"]).astimezone(MSK).strftime("%d.%m.%Y"))
        except (ValueError, TypeError):
            pass
    facts = []
    if item.area_m2: facts.append(f"{item.area_m2:g} м²")
    for key, label in (("year", "год"), ("mileage_km", "км"), ("rooms", "комнат")):
        if key in item.attributes: facts.append(f"{item.attributes[key]} {label}")
    if item.category == "garage":
        facts.append({"parking": "машино-место", "capital": "капитальный", "metal": "металлический"}.get(item.garage_type, "тип не уточнён"))
    if facts: lines.insert(3, escape(" · ".join(facts)))
    lines.append("<i>Характеристики, текст и разбор — в кнопках ниже</i>")
    return "\n".join(lines)


def full_card(result: Assessment, detail: dict) -> str:
    item = result.listing
    labels = {"rooms": "Комнат", "year": "Год", "mileage_km": "Пробег, км", "make": "Марка", "model": "Модель",
              "engine": "Двигатель", "gearbox": "Коробка", "drive": "Привод", "building_type": "Тип дома",
              "floor_group": "Этаж", "land_area_m2": "Участок, м²", "wall_material": "Стены", "land_use": "Назначение земли"}
    lines = [item.title, money(item.price_rub), item.attributes.get("visible_address", item.locality), "",
             "ХАРАКТЕРИСТИКИ", f"Площадь: {item.area_m2:g} м²" if item.area_m2 else ""]
    lines += [label + ": " + str(item.attributes[key]) for key, label in labels.items() if key in item.attributes]
    lines += ["Права на объект: " + {"owned": "по данным продавца — собственность", "unknown": "не указаны"}.get(item.ownership, item.ownership),
              "", "СРАВНЕНИЕ ЦЕНЫ", price_label(result), "", "ОПИСАНИЕ ПРОДАВЦА", detail.get("description") or "Не получено"]
    if detail.get("params"): lines += ["", "ДОПОЛНИТЕЛЬНО НА СТРАНИЦЕ", detail["params"]]
    lines += ["", "Оригинал: " + item.url]
    if detail.get("error"): lines += ["", detail["error"]]
    return "\n".join(str(x) for x in lines if x is not None)


def ranked_feed(pool: list[Listing], search: Search, minimum_discount: float | None = None,
                *, recent_days: int | None = None, sort_mode: str = "discount") -> list[Assessment]:
    now = utc_now()
    results = [assess(item, pool, search, now) for item in pool if in_scope(item, search, now) and not item.demo]
    if recent_days is not None:
        results = [r for r in results if recent(r.listing, now, recent_days)]
    if minimum_discount is not None:
        results = [r for r in results if r.status not in {"insufficient", "excluded"}
                   and r.discount is not None and r.discount + 1e-12 >= minimum_discount]
    def order(result: Assessment):
        if sort_mode in {"price_asc", "price_desc"}:
            return (result.listing.price_rub * (1 if sort_mode == "price_asc" else -1), result.listing.id)
        if sort_mode == "newest":
            return (-timestamp(result.listing.attributes["site_date_at"]).timestamp(), result.listing.id)
        reliable = result.status not in {"insufficient", "excluded"} and result.discount is not None
        group = 0 if reliable and result.discount > 0 else 1 if not reliable else 2
        return group, -(result.discount or 0) if reliable else result.listing.price_rub, result.listing.id
    unique, seen = [], set()
    for result in sorted(results, key=order):
        identity = duplicate_key(result.listing)
        if identity is not None and identity in seen:
            continue
        if identity is not None: seen.add(identity)
        unique.append(result)
    return unique


def recent(item: Listing, now, days: int = 7) -> bool:
    try:
        published = timestamp(item.attributes.get("site_date_at", ""))
        return timedelta(0) <= now - published <= timedelta(days=days)
    except (ValueError, TypeError, AttributeError):
        return False
