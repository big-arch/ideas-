"""Public, visible catalog adapters. No private APIs or access-control workarounds."""
from __future__ import annotations

import re
import time
from dataclasses import replace
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen, build_opener, ProxyHandler

from .avito import SourceUnavailable, DetailSourceUnavailable, VOID, number
from . import ayax, restate
from .models import Listing, normalized, utc_now
from .dates import site_date
from .photos import listing_photos, image_attribute

YANDEX_PATHS = {"garage": "garazh", "apartment": "kvartira/vtorichniy-rynok", "house": "dom",
                "land": "uchastok", "commercial": "kommercheskaya-nedvizhimost"}
AKULA_PATHS = {"garage": "garage", "apartment": "kvartira", "house": "dom"}
CATALOG_PATHS = {"yandex": YANDEX_PATHS, "akula": AKULA_PATHS, "ayax": ayax.CATALOG_PATHS,
                 "restate": {"garage": "kupit-garazh-rostovskaya-oblast"}, "drom": {"car": "auto/all"}}
CATALOG_BASE = {"yandex": "https://realty.yandex.ru/rostov-na-donu/kupit/", "akula": "https://rostov.akula.com/prodaja/",
                "ayax": "https://rostov-na-donu.ayax.ru/", "drom": "https://rostov-na-donu.drom.ru/",
                "restate": "https://rostovskaya-oblast.restate.ru/"}
PUBLIC_SOURCES = {source + "_public_html" for source in (*CATALOG_PATHS, "avito")}


def source_name(item: Listing) -> str:
    host = urlsplit(item.url).hostname or ""
    for domain, name in (("avito.ru", "Авито"), ("realty.yandex.ru", "Яндекс Недвижимость"), ("drom.ru", "Дром"),
                         ("akula.com", "Акула"), ("ayax.ru", "Аякс"), ("restate.ru", "Restate")):
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
        root = ("sri" in classes and str(a.get("data-id", "")).isdigit() if self.source == "restate" else
                "offer-snippet" in classes if self.source == "akula" else
                a.get("data-test") == "OffersSerpItem" if self.source == "yandex"
                else a.get("data-ftid") == "bulls-list_bull")
        if root and self.current is None:
            self.current = {"title": [], "price": [], "address": [], "description": [], "date": [], "url": "", "photo": "", 'photos': [], "active": True}
            self.depth = len(self.stack)
            if self.source == "restate":
                self.current["id"] = a["data-id"]
        marker = ""
        if self.current is not None:
            if a.get("data-ftid") == "bull_sold" or any(c in {"offer-snippet_sold", "OffersSerpItem_archived"} for c in classes):
                self.current["active"] = False
            href = a.get("href", "")
            if self.source == "akula":
                if tag == "a" and re.fullmatch(r"/prodaja/(?:garage|kvartira|dom)/(?:[^/]+/)?[^/]+-r\d+\.html", href): self.current["url"] = href
            if tag == "a" and ((self.source == "yandex" and re.fullmatch(r"/offer/\d+/", href))
                               or (self.source == "drom" and a.get("data-ftid") == "bull_title")):
                self.current["url"] = href
            if self.source == "restate":
                if tag == "a" and "sri__slider" in classes and restate.garage_id(href) == self.current["id"]:
                    self.current["url"] = href
                for css, field in (("sri__header", "title"), ("sri__price", "price"), ("sri__area", "address"),
                                   ("sri__descr-text", "description"), ("sri__date", "date")):
                    if css in classes:
                        marker = field
            elif self.source == "yandex":
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
                if self.source == "restate" and "sri__slide-image" in classes:
                    self.current["photos"].extend(listing_photos([photo], self.current["url"]))
                elif photo.startswith("https://") and (host == "avatars.mds.yandex.net" or host.endswith(".auto.drom.ru") or host == "data.akula.com"):
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
        elif source == "restate":
            ident = restate.garage_id(url)
            zones = [v.strip() for v in address.split(",") if v.strip()]
            if (not ident or category != "garage" or not zones or zones[0] != "Ростовская область"):
                continue
            locality = next((v for v in zones[1:] if not re.search(r"округ|район|р-н|поселение", v, re.I)), "")
            if not locality:
                continue
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
        if source == "restate":
            street = re.split(r",\s*[\d.,]+\s*м[²2]", title.partition(",")[2], maxsplit=1)[0].strip(" ,")
            attributes["visible_address"] = ", ".join(v for v in (locality, street) if v)
            group = next((v for v in zones if re.search(r"район|р-н", v, re.I)), "")
        date_text = " ".join(" ".join(card["date"]).split())
        if source == "akula":
            match = re.search(r"\b\d{1,2}\s+[а-я]+\s+20\d{2}\b", date_text)
            date_text = match[0] if match else ""
        attributes.update(site_date_label=date_text, site_date_at=site_date(date_text, utc_now()))
        if source == "restate":
            attributes["site_date_at"] = ""  # The detail page supplies the first publication date.
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
                observed_at=utc_now().isoformat(), category=category, attributes=attributes,
                price_basis="auction_start" if source == "restate" and re.search(r"на торгах|на аукционе|начальная цена", text) else "total",
                photo_url=next(iter(attributes["photo_urls"]), "") if source == "restate" else card["photo"], active=card["active"]))
            found[item.id] = item
        except ValueError:
            continue
    if not found:
        raise SourceUnavailable("В видимых карточках не удалось подтвердить цену, категорию и местоположение")
    return list(found.values())


class PublicCatalog:
    def __init__(self, source: str, refresh_seconds: int = 900, *, detail_reader=None):
        self.name = source
        self.refresh_seconds = max(900, refresh_seconds)
        self.due = {}
        self.detail_reader = detail_reader
        self.published = {}

    def restate_sales(self, document, category):
        rows = parse_public(document, category, "restate")
        dates = {}
        result = []
        unreadable = 0
        for item in rows:
            if item.price_basis != "total" or not restate.possibly_recent(item.attributes["site_date_label"], utc_now()):
                continue
            published = self.published.get(item.id)
            if not published:
                try:
                    published = self.detail_reader(item)["published_at"]
                except DetailSourceUnavailable:
                    raise
                except SourceUnavailable:
                    unreadable += 1
                    continue
            dates[item.id] = published
            result.append(replace(item, attributes={**item.attributes, "site_date_at": published}))
        self.published = dates
        if unreadable and not result:
            raise SourceUnavailable("Не удалось сверить дату публикации гаражей в оригинальных карточках")
        return result

    def fetch(self, category: str) -> list[Listing] | None:
        if category not in CATALOG_PATHS[self.name]:
            return None
        now = time.monotonic()
        if now < self.due.get(category, 0): return None
        self.due[category] = now + self.refresh_seconds
        url = CATALOG_BASE[self.name] + CATALOG_PATHS[self.name][category] + "/"
        if self.name == "restate":
            url += "?o=3"  # Site's public "by original posting date, newest first" sort.
        try:
            # The Akula catalog is public; direct TLS avoids an incompatible local proxy certificate.
            opener = build_opener(ProxyHandler({})).open if self.name == "akula" else urlopen
            with opener(url, timeout=25) as response:
                if any(marker in urlsplit(response.url).path.lower() for marker in ("captcha", "showcaptcha")):
                    raise SourceUnavailable("Страница запрашивает проверку посетителя")
                if urlsplit(response.url).hostname != urlsplit(url).hostname:
                    raise SourceUnavailable("Каталог перенаправил на другой источник")
                body = response.read(10_000_001)
                if len(body) > 10_000_000: raise SourceUnavailable("Каталог превышает ограничение размера")
                document = body.decode(response.headers.get_content_charset() or "utf-8")
            if self.name == "ayax":
                return ayax.parse_catalog(document, category)
            if self.name == "restate":
                if self.detail_reader is None:
                    raise SourceUnavailable("Нужна проверка даты в оригинале Restate")
                return self.restate_sales(document, category)
            return parse_public(document, category, self.name)
        except HTTPError as error:
            self.due[category] = now + 3600
            raise SourceUnavailable(f"Площадка вернула HTTP {error.code}, повтор через час") from None
        except SourceUnavailable:
            self.due[category] = now + 3600
            raise
        except (URLError, TimeoutError, OSError, UnicodeError):
            self.due[category] = now + 3600
            raise SourceUnavailable("Не удалось прочитать каталог по HTTPS, повтор через час") from None
