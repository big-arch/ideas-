"""Read one public listing page; never authenticate or bypass restricted pages."""
from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

from .avito import VOID, SourceUnavailable
from .models import Listing, utc_now, timestamp


class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.texts, self.photos, self.json_documents = [], {"description": [], "params": []}, [], []
        self.script = []
        self.in_json = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        marker, classes = a.get("data-marker", ""), a.get("class", "")
        key = ""
        if marker in {"item-view/item-description", "item-description"} or "OfferCardTextDescription__text" in classes:
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


def parse_detail(document: str, item: Listing) -> dict:
    parser = DetailParser()
    parser.feed(document)
    description = " ".join("".join(parser.texts["description"]).split())
    params = " ".join(" ".join(parser.texts["params"]).split())
    photos = [item.photo_url] if item.photo_url else []
    photos += parser.photos
    for raw in parser.json_documents:
        for node in raw if isinstance(raw, list) else [raw]:
            if not isinstance(node, dict) or node.get("@type") not in {"Product", "Apartment", "House", "Vehicle", "Car", "RealEstateListing"}: continue
            images = node.get("image", [])
            photos += images if isinstance(images, list) else [images] if isinstance(images, str) else []
            if not description: description = str(node.get("description", ""))
    unique, seen = [], set()
    for raw in photos:
        if not isinstance(raw, str): continue
        photo = urljoin(item.url, raw)
        parsed = urlsplit(photo)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host == "img.avito.st" or host.endswith(".img.avito.st")
                or host == "avatars.mds.yandex.net" or host.endswith(".auto.drom.ru")): continue
        # Yandex includes several sizes of the same image in schema.org.
        identity = (host, parsed.path.rsplit("/", 1)[0]) if host == "avatars.mds.yandex.net" else photo
        if identity not in seen:
            unique.append(photo)
            seen.add(identity)
    if not description and not params and not parser.json_documents:
        raise SourceUnavailable("Полная страница закрыта или её разметка изменилась; доступен только каталог")
    return {"description": (description or item.description)[:12000], "params": params[:6000], "photos": unique[:6],
            "url": item.url, "fetched_at": utc_now().isoformat(), "scope": "detail" if parser.texts["description"] else "metadata"}


def read_detail(item: Listing, store, *, refresh: bool = False) -> dict:
    key = "detail:" + item.id
    cached = store.get_state(key)
    if cached and not refresh:
        try:
            data = json.loads(cached)
            if (utc_now() - timestamp(data["fetched_at"])).total_seconds() < 21600: return data
        except (ValueError, KeyError): pass
    host = urlsplit(item.url).hostname
    if host not in {"avito.ru", "www.avito.ru", "realty.yandex.ru", "auto.drom.ru"}:
        raise SourceUnavailable("Источник полной страницы не поддерживается")
    try:
        with urlopen(item.url, timeout=25) as response:
            if urlsplit(response.url).hostname not in {host, "www.avito.ru", "avito.ru"}:
                raise SourceUnavailable("Источник перенаправил на другую страницу")
            body = response.read(8_000_001)
            if len(body) > 8_000_000: raise SourceUnavailable("Страница слишком велика")
            document = body.decode(response.headers.get_content_charset() or "utf-8")
        data = parse_detail(document, item)
        store.set_state(key, json.dumps(data, ensure_ascii=False))
        return data
    except (HTTPError, URLError, OSError, UnicodeError):
        raise SourceUnavailable("Полная страница временно недоступна. Защита сайта не обходится.") from None
