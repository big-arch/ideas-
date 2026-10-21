from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

CATEGORIES = {
    "apartment": "Квартиры", "house": "Дома", "garage": "Гаражи",
    "land": "Участки", "car": "Автомобили", "commercial": "Коммерческая недвижимость",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalized(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def duplicate_key(item: Listing) -> tuple | None:
    """Only strong exact content matches; a street and price alone are not identity."""
    description = normalized(item.description)
    address = normalized(str(item.attributes.get("visible_address", "")))
    if len(description) < 160 or not address or item.category == "car":
        return None
    return (item.category, normalized(item.locality), address, item.area_m2, description)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Дата должна содержать часовой пояс")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Listing:
    id: str
    url: str
    title: str
    description: str
    region: str
    locality: str
    district: str
    price_rub: float
    area_m2: float | None
    garage_type: str
    ownership: str
    land_rights: str
    condition: str
    observed_at: str
    active: bool = True
    transaction: str = "sale"
    price_basis: str = "total"
    demo: bool = False
    category: str = "garage"
    attributes: dict = field(default_factory=dict)
    photo_url: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> Listing:
        if not isinstance(data, dict):
            raise ValueError("Объявление должно быть JSON-объектом")
        try:
            item = cls(**data)
        except TypeError as error:
            raise ValueError("Отсутствуют обязательные поля или есть неизвестные поля") from error
        for field in ("id", "url", "title", "description", "region", "locality", "district",
                      "garage_type", "ownership", "land_rights", "condition", "observed_at"):
            value = getattr(item, field)
            if not isinstance(value, str) or len(value) > 12000:
                raise ValueError(f"Некорректное текстовое поле: {field}")
        if any(not getattr(item, key).strip() for key in ("id", "url", "title", "region", "locality")):
            raise ValueError("Нужны id, url, title, region и locality")
        for field in ("price_rub", "area_m2"):
            value = getattr(item, field)
            if field == "area_m2" and item.category == "car" and value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Поле {field} должно быть конечным положительным числом")
        enums = {
            "category": set(CATEGORIES),
            "garage_type": {"capital", "metal", "parking", "unknown"},
            "ownership": {"owned", "membership", "share", "unknown"},
            "land_rights": {"owned", "lease", "unknown"},
            "condition": {"normal", "needs_repair", "unknown"},
            "transaction": {"sale", "rent"},
            "price_basis": {"total", "per_m2", "deposit", "auction_start"},
        }
        for field, allowed in enums.items():
            if not isinstance(getattr(item, field), str) or getattr(item, field) not in allowed:
                raise ValueError(f"Недопустимое значение {field}")
        if not isinstance(item.attributes, dict) or len(item.attributes) > 40:
            raise ValueError("attributes должен быть объектом характеристик")
        for key, value in item.attributes.items():
            if not isinstance(key, str) or not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError("Характеристики должны быть строками или числами")
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError("Неконечное значение характеристики")
        if type(item.active) is not bool or type(item.demo) is not bool:
            raise ValueError("active и demo должны быть true/false")
        if not isinstance(item.photo_url, str) or len(item.photo_url) > 8000:
            raise ValueError("Некорректная ссылка фотографии")
        if item.photo_url:
            photo = urlsplit(item.photo_url)
            if photo.scheme != "https" or not photo.hostname or photo.username or photo.password:
                raise ValueError("Фотография должна иметь публичную HTTPS-ссылку без пароля")
        address = urlsplit(item.url)
        if address.scheme not in {"https", "http"} or not address.hostname or address.username or address.password:
            raise ValueError("Некорректная ссылка")
        if timestamp(item.observed_at) > utc_now() + timedelta(minutes=5):
            raise ValueError("Дата наблюдения находится в будущем")
        canonical = urlunsplit((address.scheme.lower(), address.netloc.lower(), address.path.rstrip("/"), "", ""))
        return cls(**{**asdict(item), "url": canonical})

    def to_dict(self) -> dict:
        return asdict(self)
