from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from .config import Search
from .models import CATEGORIES, Listing, duplicate_key, normalized, timestamp, utc_now
from .sources import PUBLIC_SOURCES

# Missing fields prevent a comparison rather than silently broadening the market.
MATCH_ATTRIBUTES = {
    "apartment": ("rooms", "building_type", "market_segment", "floor_group"),
    "house": ("wall_material", "utilities", "house_use"),
    "land": ("land_use", "utilities", "access"),
    "commercial": ("property_type", "purpose"),
    "car": ("make", "model", "generation", "engine", "gearbox", "drive", "accident_status"),
    "garage": (),
}
PUBLIC_REQUIRED = {
    "apartment": ("rooms", "market_segment", "floor_group"),
    "house": ("wall_material", "house_use"),
    "land": ("land_use",),
    "commercial": ("property_type",),
    "car": ("make", "model", "engine", "gearbox", "drive"),
    "garage": (),
}
ATTRIBUTE_LABELS = {"rooms": "число комнат", "building_type": "тип дома", "market_segment": "сегмент рынка",
                    "floor_group": "этаж", "wall_material": "материал стен", "utilities": "коммуникации",
                    "house_use": "тип дома", "land_use": "назначение земли", "access": "подъезд",
                    "property_type": "тип помещения", "purpose": "назначение помещения", "make": "марка",
                    "model": "модель", "generation": "поколение", "engine": "двигатель", "gearbox": "коробка",
                    "drive": "привод", "accident_status": "история ДТП"}


def complete(item: Listing) -> bool:
    preliminary = item.attributes.get("source") in PUBLIC_SOURCES
    if (not item.district.strip() and not preliminary) or item.ownership not in ({"owned", "unknown"} if preliminary else {"owned"}):
        return False
    if item.condition == "unknown" and not preliminary:
        return False
    if item.category == "garage" and item.garage_type == "unknown":
        return False
    if item.category in {"garage", "house", "land"} and item.land_rights == "unknown" and not preliminary:
        return False
    required = PUBLIC_REQUIRED[item.category] if preliminary else MATCH_ATTRIBUTES[item.category]
    for key in required:
        value = item.attributes.get(key)
        if value is None or normalized(str(value)) in {"", "unknown", "неизвестно"}:
            return False
    numeric = {"house": ("land_area_m2",), "car": ("year", "mileage_km")}.get(item.category, ())
    for key in numeric:
        value = item.attributes.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return False
    if item.category == "house" and item.attributes["land_area_m2"] <= 0:
        return False
    if item.category == "car" and not 1900 <= item.attributes["year"] <= utc_now().year + 1:
        return False
    return True


def similar(item: Listing, other: Listing, search: Search, *, city_level: bool = False) -> bool:
    if not complete(other):
        return False
    if (normalized(other.locality), other.category, other.ownership, other.condition) != (
            normalized(item.locality), item.category, item.ownership, item.condition):
        return False
    if not city_level and normalized(other.district) != normalized(item.district):
        return False
    if item.category == "garage" and item.garage_type != other.garage_type:
        return False
    if item.category in {"garage", "house", "land"} and item.land_rights != other.land_rights:
        return False
    for key in MATCH_ATTRIBUTES[item.category]:
        left, right = item.attributes.get(key), other.attributes.get(key)
        if item.attributes.get("source") in PUBLIC_SOURCES and other.attributes.get("source") in PUBLIC_SOURCES and (left is None or right is None):
            continue
        if normalized(str(left)) != normalized(str(right)):
            return False
    if item.category == "car":
        return (abs(item.attributes["year"] - other.attributes["year"]) <= 1
                and abs(item.attributes["mileage_km"] - other.attributes["mileage_km"]) <= 25000)
    if abs(other.area_m2 / item.area_m2 - 1) > search.area_tolerance:
        return False
    if item.category == "house":
        return abs(other.attributes["land_area_m2"] / item.attributes["land_area_m2"] - 1) <= search.area_tolerance
    return True


RISK_PATTERNS = (
    (r"без\s+документ|документ\w*\s+нет|самостро|под\s+снос", "Документы или статус постройки требуют проверки"),
    (r"\bдол[яию]\b|\b1/\d+\b", "Возможно, продаётся доля, а не целый объект"),
    (r"\bзадат|\bпредоплат|\bаванс", "Упоминается аванс или задаток: уточнить полную цену и условия"),
    (r"\bаукцион|\bторг[иа]\b|стартов\w*\s+цен", "Возможно, указана начальная цена торгов"),
    (r"\bаренд|в\s+месяц|\bпомесяч", "Есть упоминание аренды: проверить предмет сделки"),
    (r"\bза\s+(?:м2|м²|кв)|за\s+квадрат", "Возможно, цена указана за площадь, а не за весь гараж"),
    (r"\bзатаплива|\bаварийн|\bтрещин|\bпротека", "Упоминаются технические проблемы"),
)


def text_risks(item: Listing) -> tuple[str, ...]:
    text = normalized(item.title + " " + item.description)
    return tuple(label for pattern, label in RISK_PATTERNS if re.search(pattern, text))


def risks(item: Listing) -> tuple[str, ...]:
    found = list(text_risks(item))
    if item.ownership != "owned":
        found.append("Право собственности на целый объект не подтверждено данными")
    if item.category in {"garage", "house", "land"} and item.land_rights == "unknown":
        found.append("Не указан статус земли")
    if item.condition == "unknown":
        found.append("Состояние объекта не подтверждено данными")
    if item.attributes.get("source") in PUBLIC_SOURCES:
        found.append("Предварительный отбор по видимой части каталога; охват рынка ограничен")
        missing = [key for key in MATCH_ATTRIBUTES[item.category] if key not in item.attributes]
        if missing:
            found.append("Не указаны характеристики: " + ", ".join(ATTRIBUTE_LABELS.get(key, key) for key in missing))
    return tuple(dict.fromkeys(found))


def in_scope(item: Listing, search: Search, now: datetime) -> bool:
    age = now - timestamp(item.observed_at)
    return (item.active and item.category == search.category and item.transaction == "sale" and item.price_basis == "total"
            and normalized(item.region) == normalized(search.region)
            and (not search.locality or normalized(item.locality) == normalized(search.locality))
            and timedelta(0) <= age <= timedelta(days=search.max_age_days))


@dataclass(frozen=True)
class Assessment:
    listing: Listing
    status: str
    comparable_ids: tuple[str, ...] = ()
    reference_price: float | None = None
    discount: float | None = None
    spread: float | None = None
    risk_flags: tuple[str, ...] = ()
    urgency_claim: bool = False
    reason: str = ""
    comparison_scope: str = "local"


def assess(item: Listing, pool: list[Listing], search: Search, now: datetime | None = None) -> Assessment:
    now = now or utc_now()
    flags = risks(item)
    urgent = bool(re.search(r"\bсрочн|\bпереезд|быстр\w*\s+продаж", normalized(item.title + " " + item.description)))
    common = {"listing": item, "risk_flags": flags, "urgency_claim": urgent}
    if not in_scope(item, search, now):
        return Assessment(**common, status="excluded", reason="Не соответствует региону, сроку актуальности или типу сделки")
    if not complete(item):
        return Assessment(**common, status="insufficient", reason="Не хватает характеристик для сопоставления целых объектов")
    peers = []
    city_peers = []
    seen_urls = {item.url}
    seen_objects = {duplicate_key(item)} - {None}
    for other in pool:
        identity = duplicate_key(other)
        if other.id == item.id or other.url in seen_urls or other.demo != item.demo or identity is not None and identity in seen_objects:
            continue
        if not in_scope(other, search, now) or text_risks(other):
            continue
        if not similar(item, other, search, city_level=True):
            continue
        seen_urls.add(other.url)
        if identity is not None: seen_objects.add(identity)
        city_peers.append(other)
        if item.district.strip() and similar(item, other, search):
            peers.append(other)
    if len(peers) < search.min_comparables and item.attributes.get("source") in PUBLIC_SOURCES:
        peers = city_peers
        common["comparison_scope"] = "city"
        common["risk_flags"] = tuple(flags) + ("Сравнение по городу, без поправки на микрорайон",)
    if len(peers) < search.min_comparables:
        return Assessment(**common, status="insufficient", comparable_ids=tuple(p.id for p in peers),
                          reason=f"Нужно минимум {search.min_comparables} подходящих аналогов; найдено {len(peers)}")
    unit_prices = [p.price_rub if item.category == "car" else p.price_rub / p.area_m2 for p in peers]
    midpoint = median(unit_prices)
    spread = median(abs(p - midpoint) for p in unit_prices) / midpoint
    reference = midpoint if item.category == "car" else midpoint * item.area_m2
    discount = 1 - item.price_rub / reference
    # The median resists a minority of outliers; dispersed samples cannot support an alert.
    if spread > 0.25:
        status, reason = "insufficient", "Слишком большой разброс цен аналогов"
    elif discount + 1e-12 >= search.discount:
        status = "review" if flags else "candidate"
        reason = "Ниже порога сравнения; требуется проверка объекта" if flags else "Цена ниже выбранного порога"
    else:
        status, reason = "ordinary", "Скидка меньше выбранного порога"
    return Assessment(**common, status=status, comparable_ids=tuple(p.id for p in peers),
                      reference_price=reference, discount=discount, spread=spread, reason=reason)


def shortlist(pool: list[Listing], search: Search) -> list[Assessment]:
    now = utc_now()
    results = [assess(item, pool, search, now) for item in pool]
    return sorted((r for r in results if r.status in {"candidate", "review"}),
                  key=lambda r: r.discount or 0, reverse=True)


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def render(result: Assessment) -> str:
    item = result.listing
    labels = {"candidate": "КАНДИДАТ НА ПРОВЕРКУ", "review": "НИЗКАЯ ЦЕНА — ЕСТЬ ВОПРОСЫ",
              "ordinary": "ОБЫЧНАЯ ЦЕНА", "insufficient": "ОЦЕНКА НЕДОСТАТОЧНА", "excluded": "ВНЕ ПОИСКА"}
    size = f" · площадь: {item.area_m2:g} м²" if item.area_m2 is not None else ""
    if item.category == "car":
        size = f" · {item.attributes.get('year', '?')} г. · {item.attributes.get('mileage_km', '?')} км"
    lines = ["[ДЕМО — ВЫМЫШЛЕННЫЕ ДАННЫЕ]" if item.demo else "Радар объявлений", labels[result.status],
             CATEGORIES[item.category],
             item.title[:180], f"{item.locality}, {item.district}",
             f"Цена: {money(item.price_rub)}{size}"]
    if result.reference_price is not None:
        lines += [f"Ориентир по предложениям: {money(result.reference_price)}",
                  f"Отклонение вниз: {result.discount:.1%} · аналогов: {len(result.comparable_ids)}",
                  "Это цены объявлений, а не подтверждённых сделок."]
    else:
        lines.append(f"Аналогов: {len(result.comparable_ids)}. Размер скидки не установлен.")
    lines.append(result.reason)
    if result.urgency_claim:
        lines.append("Срочность заявлена продавцом; независимо не подтверждена.")
    lines.extend("⚠ " + flag for flag in result.risk_flags)
    checklist = ("Проверить: собственника, VIN, историю автомобиля, состояние и полную цену."
                 if item.category == "car" else "Проверить: собственника, документы на объект, состояние и полную цену.")
    lines += [checklist,
              f"Данные наблюдались: {item.observed_at}", f"ID: {item.id}", item.url]
    return "\n".join(lines)
