"""Read one public listing page; never authenticate or bypass restricted pages."""
from __future__ import annotations

import json
from datetime import timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from urllib.request import urlopen, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError

from .avito import VOID, SourceUnavailable, DetailSourceUnavailable
from . import ayax, restate
from .models import Listing, utc_now, timestamp
from .photos import GALLERY_VERSION, catalog_photos, listing_photos, image_values, image_attribute
from .sources import PUBLIC_SOURCES


class ListingWithdrawn(SourceUnavailable):
    """The marketplace explicitly closed this listing."""


def page_identity(url):
    parsed = urlsplit(url)
    return (parsed.hostname or "").removeprefix("www."), parsed.path.rstrip("/")


class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.texts, self.photos, self.json_documents = [], {"description": [], "params": []}, [], []
        self.script = []
        self.in_json = False
        self.gallery_depths = []
        self.canonical = ""
        self.closed = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        marker, classes = a.get("data-marker", ""), a.get("class", "")
        if tag == "meta" and a.get("property") == "og:url":
            self.canonical = a.get("content", "")
        if marker in {"item-view/closed-warning", "item-view/archive", "item-view/sold"} or a.get("data-ftid") in {"bull-page_bull-sold", "bull-page_bull-withdrawn"}:
            self.closed = True
        gallery = (marker.startswith(('image-frame', 'item-view/gallery', 'image-preview'))
                   or a.get('data-ftid', '').startswith('bull-page_bull-gallery')
                   or any(c.startswith(('OfferPhotos__', 'OfferGallery__', 'OfferCardPhotos__', 'PhotoGallery__', 'GalleryV2__slidesContainer', 'GalleryV2Slide__wrappedImage'))
                          or c in {'object__photos', 'object__gallery'} for c in classes.split()))
        in_gallery = gallery or bool(self.gallery_depths)
        if gallery and tag not in VOID: self.gallery_depths.append(len(self.stack))
        if in_gallery:
            if tag == 'img': self.photos.append(image_attribute(a))
            if tag == 'a': self.photos.append(a.get('href', ''))
        key = ""
        if marker in {"item-view/item-description", "item-description"} or "OfferCardTextDescription__text" in classes or "object__text" in classes.split():
            key = "description"
        if marker == "item-view/item-params" or "OfferCardFeatures__" in classes or a.get("data-ftid") == "bull_description":
            key = "params"
        if tag == "script" and a.get("type") == "application/ld+json":
            self.in_json = True
            self.script = []
        if tag == "meta" and a.get("property") == "og:image": self.photos.append(a.get("content", ""))
        if tag not in VOID: self.stack.append((tag, key))

    def handle_data(self, data):
        if self.in_json:
            self.script.append(data)
            return
        if any(tag in {"script", "style"} for tag, _ in self.stack): return
        for key in {key for _, key in self.stack if key}:
            self.texts[key].append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_json:
            self.in_json = False
            try: self.json_documents.append(json.loads("".join(self.script)))
            except ValueError: pass
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        self.gallery_depths = [depth for depth in self.gallery_depths if depth < len(self.stack)]


def parse_detail(document: str, item: Listing) -> dict:
    if item.attributes.get("source") == "restate_public_html":
        return restate.parse_detail(document, item)
    if item.attributes.get("source") == "ayax_public_html":
        record = ayax.detail_record(document, item)
        if record is None:
            raise ListingWithdrawn("Аякс снял объявление с публикации")
        return dict(description=record.description, params="", photos=catalog_photos(record), availability="active",
                    source_price=record.price_rub, gallery_version=GALLERY_VERSION, url=item.url,
                    fetched_at=utc_now().isoformat(), scope="detail")
    parser = DetailParser()
    parser.feed(document)
    if parser.canonical and page_identity(urljoin(item.url, parser.canonical)) != page_identity(item.url):
        raise SourceUnavailable("Открылась другая карточка или каталог")
    if parser.closed:
        raise ListingWithdrawn("Источник снял объявление с публикации")
    description = " ".join("".join(parser.texts["description"]).split())
    params = " ".join(" ".join(parser.texts["params"]).split())
    photos = catalog_photos(item)
    availability = "unknown"
    source_price = None
    for raw in parser.json_documents:
        for node in raw if isinstance(raw, list) else [raw]:
            if not isinstance(node, dict) or node.get("@type") not in {"Product", "Apartment", "House", "Vehicle", "Car", "RealEstateListing"}: continue
            offers = node.get("offers") or {}
            if isinstance(offers, dict):
                identity_url = offers.get("url") or node.get("url")
                if identity_url and (not isinstance(identity_url, str) or page_identity(urljoin(item.url, identity_url)) != page_identity(item.url)):
                    continue
                if identity_url or (parser.canonical and item.attributes.get("source") != "drom_public_html"):
                    state = str(offers.get("availability", "")).rstrip("/").rsplit("/", 1)[-1]
                    if state in {"SoldOut", "OutOfStock", "Discontinued"}:
                        raise ListingWithdrawn("Источник пометил предложение как проданное или снятое")
                    if state in {"InStock", "LimitedAvailability"}:
                        availability = "active"
                    if offers.get("priceCurrency") == "RUB":
                        try: source_price = float(offers["price"])
                        except (TypeError, ValueError, KeyError): pass
            images = node.get("image", [])
            photos += image_values(images)
            if not description: description = str(node.get("description", ""))
    photos += parser.photos
    unique = listing_photos(photos, item.url)
    if not description and not params and not parser.json_documents:
        raise SourceUnavailable("Полная страница закрыта или её разметка изменилась; доступен только каталог")
    if availability == "unknown" and (parser.texts["description"] or parser.texts["params"]):
        availability = "page_visible"
    return {"description": (description or item.description)[:12000], "params": params[:6000], "photos": unique,
            "availability": availability, "source_price": source_price,
            'gallery_version': GALLERY_VERSION,
            "url": item.url, "fetched_at": utc_now().isoformat(), "scope": "detail" if parser.texts["description"] else "metadata"}


def read_detail(item: Listing, store, *, refresh: bool = False, max_age: int = 21600) -> dict:
    key = "detail:" + item.id
    cached = store.get_state(key)
    if cached and not refresh:
        try:
            data = json.loads(cached)
            if (data.get('gallery_version') == GALLERY_VERSION and 'availability' in data
                    and timedelta(0) <= utc_now() - timestamp(data["fetched_at"]) < timedelta(seconds=max_age)):
                return data
        except (ValueError, KeyError): pass
    host = urlsplit(item.url).hostname
    if host not in {"avito.ru", "www.avito.ru", "realty.yandex.ru", "auto.drom.ru", "rostov.akula.com"} and not (
            item.attributes.get("source") == "ayax_public_html" and ayax.listing_url(item.url) or
            item.attributes.get("source") == "restate_public_html" and restate.garage_id(item.url)):
        raise SourceUnavailable("Источник полной страницы не поддерживается")
    try:
        opener = build_opener(ProxyHandler({})).open if host == "rostov.akula.com" else urlopen
        url = item.url.rstrip("/") + "/" if item.attributes.get("source") in {"ayax_public_html", "restate_public_html"} else item.url
        with opener(url, timeout=25) as response:
            if urlsplit(response.url).scheme != "https" or page_identity(response.url) != page_identity(item.url):
                raise SourceUnavailable("Источник перенаправил на другую страницу")
            body = response.read(8_000_001)
            if len(body) > 8_000_000: raise SourceUnavailable("Страница слишком велика")
            document = body.decode(response.headers.get_content_charset() or "utf-8")
        data = parse_detail(document, item)
        store.set_state(key, json.dumps(data, ensure_ascii=False))
        return data
    except ListingWithdrawn:
        store.mark_withdrawn(item)
        raise
    except HTTPError as error:
        if error.code in {404, 410} and page_identity(error.url) == page_identity(item.url):
            store.mark_withdrawn(item)
            raise ListingWithdrawn("Страница объявления снята с публикации") from None
        if error.code in {403, 429} or error.code >= 500:
            raise DetailSourceUnavailable("Источник временно ограничил доступ к карточкам") from None
        raise SourceUnavailable("Источник пока не подтвердил доступность страницы") from None
    except (URLError, OSError, UnicodeError):
        if item.attributes.get("source") in {"ayax_public_html", "restate_public_html"}:
            raise SourceUnavailable("Не удалось прочитать страницу этого объекта") from None
        raise DetailSourceUnavailable("Полная страница временно недоступна") from None


def confirmed_for_digest(item: Listing, store) -> bool:
    if not item.active:
        return False
    if item.attributes.get("source") not in PUBLIC_SOURCES:
        return True  # Imported feeds have their own publication and active fields.
    pause_key = "digest_page_pause:" + item.attributes["source"]
    if item.attributes["source"] in {"ayax_public_html", "restate_public_html"}:
        pause_key += ":" + (urlsplit(item.url).hostname or "")
    if float(store.get_state(pause_key, "0")) > utc_now().timestamp():
        return False
    try:
        page = read_detail(item, store, max_age=300)
    except DetailSourceUnavailable:
        store.set_state(pause_key, str(utc_now().timestamp() + 900))
        return False
    except SourceUnavailable:
        return False
    status = page["availability"]
    price = page["source_price"]
    if item.attributes.get("source") in {"drom_public_html", "ayax_public_html", "restate_public_html"}:
        return status == "active" and price == item.price_rub
    return status in {"active", "page_visible"} and (price is None or price == item.price_rub)
