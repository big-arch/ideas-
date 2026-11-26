"""Sale listings from the public catalog and individual listing pages of Аякс."""
import json
import math
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .avito import SourceUnavailable
from .models import Listing, timestamp, utc_now
from .photos import listing_photos


CATALOG_PATHS = {"apartment": "kvartiry", "house": "kupit-dom", "land": "kupit-zemelnyj-uchastok",
                 "commercial": "kommercheskaya-nedvizhimost", "garage": "kupit-garazh"}
ESTATE_TYPES = {"apartment": "flat", "house": "house", "land": "land", "commercial": "commerce", "garage": "garage"}


def listing_url(url):
    if not isinstance(url, str):
        return False
    return bool(re.fullmatch(r"https://[a-z0-9-]+\.ayax\.ru/[a-z0-9-]+/[0-9]+/?", url))


class CatalogPage(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.script = False
        self.raw = []
        self.links = set()
        self.canonical = ""
        self.feed(document)
        try:
            self.table = json.loads("".join(self.raw))
        except ValueError:
            raise SourceUnavailable("На странице отсутствуют данные каталога Аякс") from None
        if not isinstance(self.table, list):
            raise SourceUnavailable("Структура каталога Аякс изменилась")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script = attrs.get("id") == "__NUXT_DATA__" and attrs.get("type") == "application/json"
        elif tag == "a" and attrs.get("href"):
            url = urlsplit(attrs["href"])
            self.links.add(url.path.rstrip("/"))
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.canonical = attrs.get("href", "")

    def handle_endtag(self, tag):
        if tag == "script":
            self.script = False

    def handle_data(self, data):
        if self.script:
            self.raw.append(data)

    def ref(self, index):
        if type(index) is int and 0 <= index < len(self.table):
            return self.table[index]

    def get(self, obj, *keys):
        for key in keys:
            obj = self.ref(obj.get(key)) if isinstance(obj, dict) else None
        return obj

    def identity(self, node, category):
        url = self.get(node, "url")
        code = str(self.get(node, "code") or "")
        if (not listing_url(url) or url.rstrip("/").rsplit("/", 1)[1] != code
                or self.get(node, "estate_type", "key") != ESTATE_TYPES[category]
                or self.get(node, "target_type_id") != 1
                or self.get(node, "address", "sub_region", "region", "name") != "Ростовская область"):
            return None
        return url, code

    def listing(self, node, category):
        identity = self.identity(node, category)
        if not identity:
            return None
        url, code = identity
        get = self.get
        locality = get(node, "address", "locality", "name")
        if not locality:
            return None
        now = utc_now()
        try:
            published = timestamp(str(get(node, "created_at")))
            if published > now:
                return None
            price = float(get(node, "price"))
            area = float(get(node, "properties", "area_land" if category == "land" else "area_total"))
            if category == "land":
                area *= 100
        except (ValueError, TypeError, OverflowError):
            return None
        street = get(node, "address", "street", "name")
        house = get(node, "address", "house_number")
        address = ", ".join(str(v) for v in (locality, street, house) if v)
        photos = []
        for index in get(node, "image") or []:
            obj = self.ref(index)
            filename = get(obj, "image")
            if (get(obj, "active") is True and isinstance(filename, str)
                    and re.fullmatch(r"images/data/realty/" + code + r"/[A-Za-z0-9_-]+\.(?:jpe?g|webp|png)", filename, re.I)):
                photos.append("https://www.ayax.ru/" + filename)
        photos = listing_photos(photos, url)
        attrs = dict(source="ayax_public_html", source_scope="partial_catalog", visible_address=address,
                     site_date_at=published.isoformat(), site_date_label=published.date().isoformat(), photo_urls=photos)
        for field, key in (("rooms", "room_count_type"), ("land_use", "land_type"), ("property_type", "commerce_type")):
            value = get(node, key, "name")
            if value:
                attrs[field] = str(value)
        if category == "house":
            try:
                land = float(get(node, "properties", "area_land")) * 100
                if math.isfinite(land) and land > 0:
                    attrs["land_area_m2"] = land
            except (ValueError, TypeError):
                pass
        title = plain(get(node, "title"))
        try:
            return Listing.from_dict(dict(id="ayax-" + code, url=url, title=title, description=plain(get(node, "description"))[:12000],
                locality=locality, region="Ростовская область", district=get(node, "address", "district", "name") or "",
                price_rub=price, area_m2=area, category=category, garage_type="unknown", ownership="unknown",
                land_rights="unknown", condition="unknown", observed_at=now.isoformat(), attributes=attrs,
                photo_url=photos[0] if photos else ""))
        except ValueError:
            return None


def plain(value):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def parse_catalog(document, category):
    page = CatalogPage(document)
    for obj in page.table:
        if not isinstance(obj, dict) or not {"data", "count"} <= obj.keys():
            continue
        rows = page.get(obj, "data")
        count = str(page.get(obj, "count"))
        if not isinstance(rows, list) or not count.isdecimal():
            continue
        if not rows and int(count) == 0:
            return []
        found = {}
        for index in rows:
            item = page.listing(page.ref(index), category)
            if item and urlsplit(item.url).path.rstrip("/") in page.links:
                found[item.id] = item
        if found:
            return list(found.values())
    raise SourceUnavailable("Не удалось подтвердить объекты и категорию на странице Аякс")


def detail_record(document, item):
    page = CatalogPage(document)
    if page.canonical and page.canonical.rstrip("/") != item.url.rstrip("/"):
        raise SourceUnavailable("Страница Аякс относится к другому объекту")
    for node in page.table:
        if not isinstance(node, dict) or not {"active", "code", "realty_type"} <= node.keys():
            continue
        url = page.get(node, "url")
        if not isinstance(url, str) or url.rstrip("/") != item.url.rstrip("/") or not page.identity(node, item.category):
            continue
        state = page.get(node, "active")
        if state in {"archive", "archived", "inactive", "sold"}:
            return None
        if state == "active" and page.get(node, "target_type", "key") == "buy":
            result = page.listing(node, item.category)
            if result and result.id == item.id:
                return result
    raise SourceUnavailable("Аякс пока не подтвердил активную продажу этого объекта")
