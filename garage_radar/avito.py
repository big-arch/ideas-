"""Read visible public catalog HTML; no authenticated or hidden Avito endpoints."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

from .models import Listing, normalized, utc_now
from .dates import site_date

PATHS = {
    "garage": "garazhi_i_mashinomesta",
    "apartment": "kvartiry/prodam/vtorichka-ASgBAgICAkSSA8YQ5geMUg",
    "house": "doma_dachi_kottedzhi/prodam-ASgBAgICAUSUA9AQ",
    "land": "zemelnye_uchastki",
    "car": "avtomobili",
    "commercial": "kommercheskaya_nedvizhimost/prodam-ASgBAgICAUSwCNJW",
}
MARKERS = {"item-title", "item-price", "item-price-value", "item-specific-params", "item-location", "item-address", "item-line", "item-date"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class SourceUnavailable(RuntimeError):
    pass


@dataclass
class Card:
    id: str
    url: str = ""
    price: float = 0
    currency: str = ""
    photo_url: str = ""
    photo_width: int = 0
    texts: dict[str, list[str]] = field(default_factory=dict)

    def text(self, marker: str) -> str:
        return " ".join("".join(self.texts.get(marker, [])).split())


class CatalogParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str]] = []
        self.cards: list[Card] = []
        self.current: Card | None = None
        self.root_depth = 0
        self.next_link = ""

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        marker = values.get("data-marker", "")
        if tag == "a" and marker == "pagination-button/nextPage":
            self.next_link = values.get("href", "")
        if marker == "item" and values.get("data-item-id"):
            self.current = Card(values["data-item-id"])
            self.root_depth = len(self.stack)
        if self.current:
            if marker.startswith("slider-image/image-") and not self.current.photo_url:
                image = marker.removeprefix("slider-image/image-")
                host = urlsplit(image).hostname or ""
                if image.startswith("https://") and (host == "img.avito.st" or host.endswith(".img.avito.st")):
                    self.current.photo_url = image
            if tag == "img":
                candidates = []
                for value in values.get("srcset", "").split(","):
                    parts = value.strip().split()
                    if parts and parts[0].startswith("https://"):
                        width = int(parts[1][:-1]) if len(parts) > 1 and re.fullmatch(r"\d+w", parts[1]) else 0
                        candidates.append((width, parts[0]))
                width, image = max(candidates) if candidates else (0, values.get("data-src", values.get("src", "")))
                host = urlsplit(image).hostname or ""
                if (image.startswith("https://") and (host == "img.avito.st" or host.endswith(".img.avito.st"))
                        and (not self.current.photo_url or width > self.current.photo_width)):
                    self.current.photo_url = image
                    self.current.photo_width = width
            if values.get("itemprop", "").lower() == "price":
                try:
                    self.current.price = float(values.get("content", "0"))
                except ValueError:
                    pass
            if values.get("itemprop", "").lower() == "pricecurrency":
                self.current.currency = values.get("content", "")
            if tag == "a" and marker in {"item-title", "item-photo-sliderLink"}:
                self.current.url = values.get("href", "")
        if tag not in VOID:
            self.stack.append((tag, marker))

    def handle_data(self, data):
        if self.current and not any(tag in {"script", "style"} for tag, _ in self.stack):
            for marker in {marker for _, marker in self.stack if marker in MARKERS}:
                self.current.texts.setdefault(marker, []).append(data)

    def handle_endtag(self, tag):
        if self.current and tag == "p":
            for marker in {marker for _, marker in self.stack if marker in MARKERS}:
                self.current.texts.setdefault(marker, []).append("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if self.current and len(self.stack) <= self.root_depth:
            self.cards.append(self.current)
            self.current = None


def number(text: str, pattern: str) -> float | None:
    match = re.search(pattern, normalized(text))
    if not match:
        return None
    return float(match.group(1).replace(" ", "").replace(",", "."))


def locality_and_group(card: Card) -> tuple[str, str]:
    lines = [" ".join(line.split()) for line in "".join(card.texts.get("item-location", [])).splitlines() if line.strip()]
    if not lines:
        return "", ""
    locality = lines[-1]
    address = lines[0] if len(lines) > 1 else ""
    group = ""
    neighborhood = re.search(r"(?:мкр-н|микрорайон|р-н|район)\s+([^,]+)", address, re.I)
    if neighborhood:
        group = neighborhood.group(0)
    elif re.search(r"ул\.|улица|проспект|пр-т|переулок|пер\.", address, re.I):
        group = "улица: " + address.split(",")[0]
    return locality, group


def convert(card: Card, category: str) -> Listing | None:
    title = card.text("item-title")
    description = card.text("item-line")
    params = card.text("item-specific-params")
    text = normalized(" ".join((title, description, params)))
    price_text = normalized(card.text("item-price") or card.text("item-price-value"))
    url = urljoin("https://www.avito.ru", card.url)
    parsed = urlsplit(url)
    expected_slug = PATHS[category].split("/")[0]
    if (parsed.hostname not in {"avito.ru", "www.avito.ru"} or f"/{expected_slug}/" not in parsed.path
            or not title or card.price <= 0 or card.currency != "RUB"):
        return None
    locality, group = locality_and_group(card)
    if not locality:
        return None
    if category == "car":
        group = "город целиком"
    area = number(title, r"(\d+(?:[.,]\d+)?)\s*м[²2]")
    if re.search(r"(?:[<>]|\bот\s|\bдо\s)\s*\d+[.,\d]*\s*м[²2]", normalized(title)):
        return None
    if category == "land" and area is None:
        plots = number(title, r"(\d+(?:[.,]\d+)?)\s*сот")
        hectares = number(title, r"(\d+(?:[.,]\d+)?)\s*га\b")
        area = plots * 100 if plots is not None else hectares * 10000 if hectares is not None else None
    if category != "car" and area is None:
        return None
    sale_route = "/prodam" in PATHS[category]
    is_rent = bool(re.search(r"в\s+(?:месяц|сутки|день|час)|за\s+(?:месяц|сутки)", price_text))
    if not sale_route and re.match(r"(?:сдам|сдаю|сдается|сдаётся|аренда)\b", normalized(description)):
        is_rent = True
    if not sale_route and re.search(r"сдам|аренда", normalized(title)):
        is_rent = True
    garage_type = "unknown"
    if "машиномест" in text or "паркинг" in normalized(title):
        garage_type = "parking"
    elif "металлическ" in text:
        garage_type = "metal"
    elif any(word in text for word in ("кирпич", "железобетон", "капитальн")):
        garage_type = "capital"
    attributes: dict = {"source": "avito_public_html", "location_group": group,
                        "source_scope": "partial_catalog", "visible_params": params,
                        "visible_address": card.text("item-address")}
    attributes["site_date_label"] = card.text("item-date")
    attributes["site_date_at"] = site_date(card.text("item-date"), utc_now())
    if category == "apartment":
        rooms = re.search(r"(\d+)[- ]к[.\s]|(студия)", normalized(title))
        if rooms:
            attributes["rooms"] = int(rooms.group(1)) if rooms.group(1) else 0
        attributes["market_segment"] = "resale"
        for word, kind in (("кирпич", "brick"), ("панел", "panel"), ("монолит", "monolith"), ("блочн", "block")):
            if word in normalized(params) or re.search(r"(?:дом[^.!?]{0,50}" + word + "|" + word + r"\w*\s+дом)", text):
                attributes["building_type"] = kind
                break
        floors = re.search(r"(\d+)\s*/\s*(\d+)\s*эт", normalized(title))
        if floors:
            floor, total = map(int, floors.groups())
            attributes["floor_group"] = "first" if floor == 1 else "last" if floor == total else "middle"
    if category == "house":
        plot = number(title + " " + params, r"(\d+(?:[.,]\d+)?)\s*сот")
        if plot:
            attributes["land_area_m2"] = plot * 100
        for word, kind in (("кирпич", "brick"), ("блок", "block"), ("дерев", "wood"), ("каркас", "frame")):
            if word in normalized(params):
                attributes["wall_material"] = kind
                break
        if normalized(title).startswith("дом "):
            attributes["house_use"] = "house"
        utilities = [name for word, name in (("электричество", "electricity"), ("газ", "gas"),
                     ("водоснабжение", "water"), ("канализация", "sewer")) if word in normalized(params)]
        if utilities:
            attributes["utilities"] = ",".join(sorted(utilities))
    if category == "land" and area is None:
        return None
    if category == "land":
        use = re.search(r"\b(ижс|лпх|снт|днп)\b", normalized(title + " " + params))
        if use:
            attributes["land_use"] = use[1]
    if category == "commercial":
        for word, kind in (("офис", "office"), ("склад", "warehouse"), ("магазин", "retail"),
                           ("свободного назначения", "free"), ("помещение", "premises"), ("здание", "building")):
            if word in normalized(title):
                attributes["property_type"] = kind
                if kind not in {"premises", "building"}:
                    attributes["purpose"] = kind
                break
    if category == "car":
        year = re.search(r"\b(19\d{2}|20\d{2})\b", title)
        if year:
            attributes["year"] = int(year.group(1))
        mileage = number(params, r"([\d ]+)\s*км")
        if mileage is not None:
            attributes["mileage_km"] = mileage
        elif re.search(r"новый\s+автомобил|без\s+пробега|пробег\s*[:—-]?\s*0\s*км", text):
            attributes["mileage_km"] = 0
        engine = re.search(r"\b(\d+[.,]\d+)\s+(AMT|AT|MT|CVT|DCT|АМТ|АТ|МТ)\b", title, re.I)
        if engine:
            before_engine = title[:engine.start()].strip().split(maxsplit=1)
            if len(before_engine) == 2:
                attributes["make"], attributes["model"] = before_engine
            attributes["engine"] = engine[1].replace(",", ".")
            attributes["gearbox"] = engine[2].upper()
        for word, kind in (("передний", "front"), ("задний", "rear"), ("полный", "all")):
            if word in normalized(params):
                attributes["drive"] = kind
                break
    basis = "per_m2" if re.search(r"за\s*(?:м[²2]|кв)|/\s*м[²2]", price_text) else "total"
    try:
        return Listing.from_dict({
            "id": "avito-" + card.id, "url": url, "title": title, "description": description,
            "region": "Ростовская область", "locality": locality, "district": group,
            "price_rub": card.price, "area_m2": area, "garage_type": garage_type,
            "ownership": "unknown", "land_rights": "unknown", "condition": "unknown",
            "observed_at": utc_now().isoformat(), "category": category, "attributes": attributes,
            "transaction": "rent" if is_rent else "sale", "price_basis": basis,
            "photo_url": card.photo_url,
        })
    except ValueError:
        return None


def parse_catalog(document: str, category: str) -> list[Listing]:
    parser = CatalogParser()
    parser.feed(document)
    if not parser.cards:
        raise SourceUnavailable("Авито не вернул видимых карточек: доступ ограничен или разметка изменилась")
    found = {}
    for card in parser.cards:
        item = convert(card, category)
        if item:
            found[item.id] = item
    return list(found.values())


class PublicAvito:
    def __init__(self, refresh_seconds: int = 900, max_pages: int = 3):
        self.refresh_seconds = max(300, refresh_seconds)
        self.due: dict[str, float] = {}
        self.max_pages = min(3, max(1, max_pages))

    def fetch(self, category: str) -> list[Listing] | None:
        now = time.monotonic()
        if now < self.due.get(category, 0):
            return None
        self.due[category] = now + self.refresh_seconds
        url = "https://www.avito.ru/rostovskaya_oblast/" + PATHS[category]
        try:
            items: dict[str, Listing] = {}
            for page in range(self.max_pages):
                with urlopen(url, timeout=25) as response:
                    body = response.read(10_000_001)
                    if len(body) > 10_000_000:
                        raise SourceUnavailable("Страница Авито превышает ограничение размера")
                    document = body.decode(response.headers.get_content_charset() or "utf-8")
                for item in parse_catalog(document, category):
                    items[item.id] = item
                parser = CatalogParser()
                parser.feed(document)
                next_url = urljoin(url, parser.next_link)
                next_path = urlsplit(next_url)
                if (not parser.next_link or next_path.hostname != "www.avito.ru"
                        or not next_path.path.startswith("/rostovskaya_oblast/")):
                    break
                url = next_url
                if page + 1 < self.max_pages:
                    time.sleep(1)
            return list(items.values())
        except HTTPError as error:
            self.due[category] = now + 3600
            raise SourceUnavailable(f"Авито недоступен, HTTP {error.code}. Повтор отложен, обход защиты не используется") from None
        except (URLError, TimeoutError, OSError, UnicodeError):
            raise SourceUnavailable("Не удалось прочитать публичную страницу Авито") from None
        except SourceUnavailable:
            self.due[category] = now + 3600
            raise
