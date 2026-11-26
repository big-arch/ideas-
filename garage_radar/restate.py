"""Dates and identity on Restate's public garage sale pages."""
from datetime import datetime, timedelta
from html.parser import HTMLParser
import re

from .avito import SourceUnavailable, VOID
from .dates import MSK
from .models import utc_now
from .photos import GALLERY_VERSION, listing_photos


def garage_id(url):
    match = re.fullmatch(r"https://[a-z0-9-]+\.restate\.ru/obj/prodazha-garazha-[a-z0-9-]+-(\d+)/?", url)
    return match[1] if match else None


def possibly_recent(label, now):
    # This catalog label can be the last update. Only an old update can rule out a new posting.
    today = now.astimezone(MSK).date()
    try:
        dd, mm = map(int, label.split("."))
        day = today.replace(month=mm, day=dd)
        if day > today:
            day = day.replace(year=day.year - 1)
        return today - day <= timedelta(days=7)
    except ValueError:
        return True


class GaragePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.fields = {"description": [], "params": [], "date": [], "address": []}
        self.photos = []
        self.canonical = self.price = self.currency = ""
        self.contact_ids = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = a.get("class", "").split()
        if tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href", "")
        if a.get("data-popup-target") == "popup19--send-msg":
            self.contact_ids.add(a.get("data-id", ""))
        if a.get("itemprop") == "price":
            self.price = a.get("content", "")
        if a.get("itemprop") == "priceCurrency":
            self.currency = a.get("content", "")
        parent = self.stack[-1][1] if self.stack else ""
        if tag == "img" and parent == "gallery":
            self.photos.append(a.get("src", ""))
        fields = {"base__descr": "description", "base__obj-params": "params", "ad-params__param": "date",
                  "base__address": "address", "gallery-top": "gallery"}
        if tag not in VOID:
            self.stack.append((tag, next((fields[c] for c in classes if c in fields), parent)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][1] in self.fields:
            self.fields[self.stack[-1][1]].append(data)


def parse_detail(document, item):
    page = GaragePage()
    page.feed(document)
    ident = garage_id(item.url)
    if (not ident or ident not in page.contact_ids or page.canonical.rstrip("/") != item.url.rstrip("/")
            or page.currency != "RUB" or not page.price.isdecimal()):
        raise SourceUnavailable("Restate не подтвердил действующую карточку и её полную цену")
    values = {k: " ".join(" ".join(v).split()) for k, v in page.fields.items()}
    if "Ростовская область" not in values["address"] or item.locality.casefold() not in values["address"].casefold():
        raise SourceUnavailable("Адрес оригинала расходится с каталогом")
    match = re.search(r"(?:^|\s)с\s+(\d{2}\.\d{2}\.\d{4})(?=,|\s|$)", values["date"])
    if not match:
        raise SourceUnavailable("В оригинале отсутствует дата первой публикации")
    try:
        published = datetime.strptime(match[1], "%d.%m.%Y").replace(tzinfo=MSK)
    except ValueError:
        raise SourceUnavailable("Дата публикации Restate не распознана") from None
    return dict(availability="active", source_price=float(page.price), published_at=published.isoformat(),
                description=values["description"][:12000], params=values["params"][:6000],
                photos=listing_photos(page.photos, item.url), gallery_version=GALLERY_VERSION, url=item.url,
                scope="detail", fetched_at=utc_now().isoformat())
