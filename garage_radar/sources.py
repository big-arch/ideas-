"""Public, visible catalog adapters. No private APIs or access-control workarounds."""
from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen, build_opener, ProxyHandler

from .avito import SourceUnavailable, VOID, number
from .models import Listing, normalized, utc_now
from .dates import site_date
from .photos import listing_photos, image_attribute

YANDEX_PATHS = {"garage": "garazh", "apartment": "kvartira/vtorichniy-rynok", "house": "dom",
                "land": "uchastok", "commercial": "kommercheskaya-nedvizhimost"}
AKULA_PATHS = {"garage": "garage", "apartment": "kvartira", "house": "dom"}
PUBLIC_SOURCES = {"avito_public_html", "yandex_public_html", "drom_public_html", "akula_public_html"}


def source_name(item: Listing) -> str:
    host = urlsplit(item.url).hostname or ""
    for domain, name in (("avito.ru", "Авито"), ("realty.yandex.ru", "Яндекс Недвижимость"), ("drom.ru", "Дром"), ("akula.com", "Акула")):
        if host == domain or host.endswith("." + domain):
            return name
    return "Источник"


class VisibleCards(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.stack = []
        self.current = None
        self.depth = 0
        self.cards = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = a.get("class", "").split()
        root = ("offer-snippet" in classes if self.source == "akula" else
                a.get("data-test") == "OffersSerpItem" if self.source == "yandex"
                else a.get("data-ftid") == "bulls-list_bull")
        if root and self.current is None:
            self.current = {"title": [], "price": [], "address": [], "description": [], "date": [], "url": "", "photo": "", 'photos': []}
            self.depth = len(self.stack)
        marker = ""
        if self.current is not None:
            href = a.get("href", "")
            if self.source == "akula":
                if tag == "a" and re.fullmatch(r"/prodaja/(?:garage|kvartira|dom)/(?:[^/]+/)?[^/]+-r\d+\.html", href): self.current["url"] = href
            if tag == "a" and ((self.source == "yandex" and re.fullmatch(r"/offer/\d+/", href))
                               or (self.source == "drom" and a.get("data-ftid") == "bull_title")):
                self.current["url"] = href
            if self.source == "yandex":
                if a.get("data-test") == "OffersSerpItemLinkTitle": marker = "title"
                if "OffersSerpItem__price" in classes: marker = "price"
                if any(c.startswith("AddressWithGeoLinks__addressContainer") for c in classes): marker = "address"
                if "OffersSerpItem__description" in classes: marker = "description"
                if any(c.startswith("OffersSerpItem__publish-date") for c in classes): marker = "date"
            elif self.source == "akula":
                if tag == "strong": marker = "title"
                marker = {"offer-snippet__price": "price", "offer-snippet__address": "address",
                          "offer-snippet__description": "description", "offer-snippet-tags__item": "date"}.get(a.get("class", ""), marker)
            else:
                marker = {"bull_title": "title", "bull_price": "price", "bull_location": "address",
                          "component_inline-bull-description": "description", "bull_date": "date"}.get(a.get("data-ftid"), "")
            if tag == "img":
                photo = image_attribute(a)
                photo = urljoin("https://realty.yandex.ru", photo)
                host = urlsplit(photo).hostname or ""
                if photo.startswith("https://") and (host == "avatars.mds.yandex.net" or host.endswith(".auto.drom.ru") or host == "data.akula.com"):
                    self.current['photos'].append(photo)
                    if not self.current['photo']: self.current["photo"] = photo
        if tag not in VOID:
            self.stack.append((tag, marker))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if self.current is not None and len(self.stack) <= self.depth:
            self.cards.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.current is None or any(t in {"script", "style"} for t, _ in self.stack):
            return
        for _, marker in self.stack:
            if marker:
                self.current[marker].append(data)


def parse_public(document: str, category: str, source: str) -> list[Listing]:
    parser = VisibleCards(source)
    parser.feed(document)
    if not parser.cards:
        raise SourceUnavailable("Каталог не вернул видимых карточек: доступ ограничен или разметка изменилась")
    found = {}
    for card in parser.cards:
        title, price, address, description = (" ".join("".join(card[k]).split()) for k in ("title", "price", "address", "description"))
        total = re.match(r"([\d\s]+)(?:₽|$)", price)
        if not title or not total or re.search(r"в месяц|за м|/м", price):
            continue
        price_rub = float(re.sub(r"\s", "", total[1]))
        url = urljoin("https://rostov.akula.com" if source == "akula" else "https://realty.yandex.ru", card["url"])
        path = urlsplit(url)
        if source == "yandex":
            ident = re.fullmatch(r"/offer/(\d+)/", path.path)
            if path.hostname != "realty.yandex.ru" or not ident: continue
            ident = ident[1]
            locality = "Ростов-на-Дону"
            first = address.split(",")[0].strip()
            towns = {"Батайск", "Азов", "Аксай", "Таганрог", "Новочеркасск", "Шахты", "Новошахтинск",
                     "Сальск", "Волгодонск", "Каменск-Шахтинский", "Миллерово", "Донецк", "Гуково"}
            if first in towns:
                locality = first
            elif re.search(r"\b(?:пос[её]лок|село|хутор|станица|деревня)\b", first, re.I):
                locality = first
        elif source == "akula":
            ident = re.fullmatch(r"/prodaja/" + AKULA_PATHS.get(category, "unsupported") + r"/(?:[^/]+/)?[^/]+-r(\d+)\.html", path.path)
            if category not in AKULA_PATHS or path.hostname != "rostov.akula.com" or not ident: continue
            if "ростов-на-дону" not in normalized(address): continue
            ident = ident[1]
            locality = "Ростов-на-Дону"
        else:
            # Reject advertised cards from other regions and the city Rostov (Yaroslavl).
            ident = re.fullmatch(r"/rostov-na-donu/[^/]+/[^/]+/(\d+)\.html", path.path)
            if path.hostname != "auto.drom.ru" or not ident or normalized(address) != "ростов-на-дону": continue
            ident = ident[1]
            locality = "Ростов-на-Дону"
        text = normalized(title + " " + description)
        area = number(title, r"(\d+(?:[.,]\d+)?)\s*м[²2]")
        if re.search(r"(?:[<>]|\bот\s|\bдо\s)\s*\d+[.,\d]*\s*м[²2]", normalized(title)): continue
        if category == "land":
            plots = number(title, r"(\d+(?:[.,]\d+)?)\s*сот")
            hectares = number(title, r"(\d+(?:[.,]\d+)?)\s*га\b")
            area = plots * 100 if plots else hectares * 10000 if hectares else area
        if category != "car" and not area: continue
        group = "улица: " + address.split(",")[0] if address else ""
        attributes = {"source": source + "_public_html", "source_scope": "partial_catalog", "visible_address": address}
        attributes['photo_urls'] = listing_photos(card['photos'], url)
        date_text = " ".join(" ".join(card["date"]).split())
        if source == "akula":
            match = re.search(r"\b\d{1,2}\s+[а-я]+\s+20\d{2}\b", date_text)
            date_text = match[0] if match else ""
        attributes.update(site_date_label=date_text, site_date_at=site_date(date_text, utc_now()))
        garage_type = ("parking" if "машиномест" in text or "паркинг" in text else "metal" if "металлическ" in text
                       else "capital" if any(w in text for w in ("кирпич", "капитальн", "железобетон")) else "unknown")
        if category == "car":
            attributes.update({"make": path.path.split("/")[2], "model": path.path.split("/")[3]})
            year = re.search(r",\s*((?:19|20)\d{2})\b", title)
            if year: attributes["year"] = int(year[1])
            mileage = number(description, r"([\d ]+)\s*км")
            if mileage is not None: attributes["mileage_km"] = mileage
            engine = number(description, r"(\d+(?:[.,]\d+)?)\s*л\b")
            if engine: attributes["engine"] = str(engine)
            for word, value in (("механика", "MT"), ("автомат", "AT"), ("вариатор", "CVT"), ("робот", "AMT")):
                if word in text: attributes["gearbox"] = value
            for word, value in (("передний", "front"), ("задний", "rear"), ("4wd", "all")):
                if word in text: attributes["drive"] = value
        try:
            item = Listing.from_dict(dict(id=source + "-" + ident, url=url, title=title, description=description,
                region="Ростовская область", locality=locality, district=group, price_rub=price_rub, area_m2=area,
                garage_type=garage_type, ownership="unknown", land_rights="unknown", condition="unknown",
                observed_at=utc_now().isoformat(), category=category, attributes=attributes, photo_url=card["photo"]))
            found[item.id] = item
        except ValueError:
            continue
    if not found:
        raise SourceUnavailable("В видимых карточках не удалось подтвердить цену, категорию и местоположение")
    return list(found.values())


class PublicCatalog:
    def __init__(self, source: str, refresh_seconds: int = 900):
        self.name = source
        self.refresh_seconds = max(900, refresh_seconds)
        self.due = {}

    def fetch(self, category: str) -> list[Listing] | None:
        if (self.name == "drom" and category != "car" or self.name == "yandex" and category not in YANDEX_PATHS
                or self.name == "akula" and category not in AKULA_PATHS):
            return None
        now = time.monotonic()
        if now < self.due.get(category, 0): return None
        self.due[category] = now + self.refresh_seconds
        url = ("https://rostov.akula.com/prodaja/" + AKULA_PATHS[category] + "/" if self.name == "akula" else
               "https://rostov-na-donu.drom.ru/auto/all/" if self.name == "drom" else
               "https://realty.yandex.ru/rostov-na-donu/kupit/" + YANDEX_PATHS[category] + "/")
        try:
            # The Akula catalog is public; direct TLS avoids an incompatible local proxy certificate.
            opener = build_opener(ProxyHandler({})).open if self.name == "akula" else urlopen
            with opener(url, timeout=25) as response:
                if urlsplit(response.url).hostname != urlsplit(url).hostname:
                    raise SourceUnavailable("Каталог перенаправил на другой источник")
                body = response.read(10_000_001)
                if len(body) > 10_000_000: raise SourceUnavailable("Каталог превышает ограничение размера")
                document = body.decode(response.headers.get_content_charset() or "utf-8")
            return parse_public(document, category, self.name)
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, SourceUnavailable):
            self.due[category] = now + 3600
            raise SourceUnavailable("Источник временно недоступен; повтор через час, сохранённые объявления доступны") from None
