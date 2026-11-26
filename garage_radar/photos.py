"""Bounded gallery URLs extracted from a single listing, never guessed image IDs."""
import re
from urllib.parse import urljoin, urlsplit

GALLERY_VERSION = 2
MAX_PHOTOS = 6


def image_size(url):
    path=urlsplit(url).path
    match=re.search(r'/gen(\d+)\.',path)
    if match: return int(match[1])
    size=path.rsplit('/',1)[-1]
    if size in {'max','cosmic','orig'} or '/max/' in path: return 4000
    if size in {'large','app_large'}: return 1200
    if size in {'app_middle','middle'}: return 600
    return 0


def photo_identity(url):
    parsed = urlsplit(url)
    host, path = parsed.hostname or '', parsed.path
    # Drom rotates s12/s31/etc for the same object path between catalog reads.
    if re.fullmatch(r's\d+\.auto\.drom\.ru', host):
        host = 'media.auto.drom.ru'
    if host == 'avatars.mds.yandex.net' or host.endswith('.auto.drom.ru'):
        return host, path.rsplit('/', 1)[0]
    if host == 'data.akula.com':
        return host, re.sub(r'/\d+/(?:max|\d+)/', lambda m: '/' + m[0].split('/')[1] + '/', path)
    return host, path


def supported_photo(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    try:
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password and port in (None, 443)
            and (host == 'img.avito.st' or host.endswith('.img.avito.st')
                 or host == 'avatars.mds.yandex.net' or host.endswith('.auto.drom.ru') or host == 'data.akula.com'
                 or host == 'www.ayax.ru' and re.fullmatch(r'/images/data/realty/\d+/[a-zA-Z0-9_-]+\.(?:jpe?g|png|webp)', parsed.path)
                 or re.fullmatch(r'[a-z0-9-]+\.restate\.ru', host) and re.fullmatch(r'/uploads/\d\d/\d\d/\d\d/[a-z]_\d+\.(?:jpe?g|png|webp)', parsed.path)))


def listing_photos(values, base_url=''):
    result, positions = [], {}
    for value in values:
        if not isinstance(value, str) or len(value) > 2500: continue
        url = urljoin(base_url, value)
        if not supported_photo(url):
            continue
        identity = photo_identity(url)
        if identity in positions:
            # Later full-page gallery links replace small catalog versions of the same photo.
            if image_size(url)>=image_size(result[positions[identity]]): result[positions[identity]] = url
        elif len(result) < MAX_PHOTOS:
            positions[identity] = len(result)
            result.append(url)
    return result


def image_values(value):
    if isinstance(value, str): return [value]
    if isinstance(value, list): return [url for item in value for url in image_values(item)]
    if isinstance(value, dict): return image_values(value.get('contentUrl') or value.get('url') or [])
    return []


def image_attribute(attrs):
    candidates = []
    for token in (attrs.get('srcset') or attrs.get('data-srcset') or '').split(','):
        parts = token.strip().split()
        if parts:
            size = float(parts[1][:-1]) if len(parts) > 1 and re.fullmatch(r'\d+(?:\.\d+)?[wx]', parts[1]) else 0
            candidates.append((size, parts[0]))
    return max(candidates)[1] if candidates else attrs.get('data-image-src') or attrs.get('data-src') or attrs.get('src') or ''


def catalog_photos(item):
    return listing_photos([item.photo_url, *item.attributes.get('photo_urls', [])], item.url)
