from __future__ import annotations

import json
import secrets
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, ProxyHandler


class RemoteError(RuntimeError):
    def __init__(self, status: int = 0, retry_after: int = 5):
        super().__init__(f"Внешний сервис недоступен (код {status}); секреты скрыты")
        self.status = status
        self.retry_after = min(300, max(1, retry_after))


def post_json(url: str, payload: dict, timeout: int = 45) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise RemoteError()
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise RemoteError()
        return parsed
    except HTTPError as error:
        delay = 5
        try:
            body = json.loads(error.read(8192))
            delay = int(body.get("parameters", {}).get("retry_after", 5))
        except (ValueError, AttributeError, TypeError):
            pass
        raise RemoteError(error.code, delay) from None
    except (URLError, TimeoutError, OSError, ValueError):
        # URLError may contain a URL with the Telegram token; do not propagate it.
        raise RemoteError() from None


def photo_bytes(url: str) -> tuple[bytes, str]:
    """Only images hosted by the three supported public catalogs."""
    host = urlsplit(url).hostname or ""
    if not url.startswith("https://") or not (host == "img.avito.st" or host.endswith(".img.avito.st")
            or host == "avatars.mds.yandex.net" or host.endswith(".auto.drom.ru") or host == "data.akula.com"):
        raise RemoteError(400)
    try:
        opener = build_opener(ProxyHandler({})).open if host == "data.akula.com" else urlopen
        with opener(url, timeout=20) as response:
            final_host = urlsplit(response.url).hostname
            if final_host != host:
                raise RemoteError(400)
            kind = response.headers.get_content_type()
            data = response.read(8_000_001)
            if kind not in {"image/jpeg", "image/png", "image/webp"} or not data or len(data) > 8_000_000:
                raise RemoteError(400)
            return data, kind
    except (HTTPError, URLError, TimeoutError, OSError):
        raise RemoteError(400) from None


def post_photos(url: str, payload: dict, files: dict[str, tuple[bytes, str]]) -> dict:
    boundary = "radar" + secrets.token_hex(16)
    chunks = []
    for key, value in payload.items():
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{text}\r\n'.encode())
    for key, (data, kind) in files.items():
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[kind]
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="photo.{extension}"\r\nContent-Type: {kind}\r\n\r\n'.encode() + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    request = Request(url, data=b"".join(chunks), headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urlopen(request, timeout=60) as response:
            parsed = json.loads(response.read(2_000_000))
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            raise RemoteError(int(parsed.get("error_code", 0)) if isinstance(parsed, dict) else 0)
        return parsed
    except HTTPError as error:
        raise RemoteError(error.code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RemoteError() from None
