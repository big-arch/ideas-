"""Official ChatGPT plan OAuth. Credentials never leave the server-side Windows store."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt

from .network import RemoteError

ROOT = Path(__file__).resolve().parent.parent / "runtime" / "chatgpt"
RESOURCE = "https://api.openai.com/v1"
AUTH = "https://auth.openai.com/api/accounts/authorize"
TOKEN = "https://auth.openai.com/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
LOCK = threading.RLock()
# Allow a small clock difference between Windows and the issuer. Signature,
# issuer, audience, nonce and grant validation remain mandatory.
CLOCK_SKEW_SECONDS = 30


@contextmanager
def credential_lock():
    """Serialize rotating refresh tokens between the bot and local application."""
    import msvcrt
    ROOT.mkdir(parents=True, exist_ok=True)
    with LOCK, (ROOT / "session.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if not stream.tell(): stream.write(b"0"); stream.flush()
        acquired = False
        for _ in range(200):
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
                break
            except OSError: time.sleep(.1)
        if not acquired: raise ValueError("Обновление сеанса уже выполняется")
        try: yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise ValueError("Этот локальный вход настроен для защищённого хранилища Windows")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "Business Radar", None, None, None, 1, ctypes.byref(target))
    if not ok: raise ValueError("Не удалось открыть защищённое хранилище Windows")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def save(record: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / "credentials.bin"
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(protect(json.dumps(record).encode()))
    temporary.replace(path)


def load() -> dict:
    path = ROOT / "credentials.bin"
    if not path.exists(): return {}
    return json.loads(protect(path.read_bytes(), decrypt=True))


def request_json(url: str, data: dict | None = None, token: str = "") -> dict:
    headers = {"Accept": "application/json"}
    body = None
    if data is not None:
        body = urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token: headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=30) as response:
            parsed = json.loads(response.read(2_000_000))
        if not isinstance(parsed, dict): raise RemoteError()
        return parsed
    except HTTPError as error:
        raise RemoteError(error.code) from None
    except (OSError, URLError, ValueError):
        raise RemoteError() from None


def begin(redirect: str) -> tuple[str, dict]:
    ROOT.mkdir(parents=True, exist_ok=True)
    host_path = ROOT / "host-id.txt"
    if not host_path.exists(): host_path.write_text("urn:uuid:" + str(uuid.uuid4()), encoding="utf8")
    host = host_path.read_text("utf8")
    old = load()
    pending = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
               "verifier": secrets.token_urlsafe(64), "redirect": redirect, "time": time.time(),
               "client_id": old.get("client_id", "dynamic_agent_client"), "host": host,
               "subject": old.get("subject", "")}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).rstrip(b"=").decode()
    params = dict(client_id=pending["client_id"], ext_agent_host_id=host, response_type="code", redirect_uri=redirect,
                  scope=SCOPES, resource=RESOURCE, state=pending["state"], nonce=pending["nonce"],
                  code_challenge_method="S256", code_challenge=challenge)
    if pending["client_id"] == "dynamic_agent_client": params["agent_name_hint"] = "Business Radar"
    return AUTH + "?" + urlencode(params), pending


def finish(query: dict, pending: dict) -> None:
    if not pending or time.time() - pending["time"] > 600 or not hmac.compare_digest(query.get("state", ""), pending["state"]):
        raise ValueError("Сеанс входа истёк или не совпадает. Начни вход заново.")
    if query.get("error"): raise ValueError("Вход или разрешение не завершены. Доступ к ChatGPT не подключён.")
    client = query.get("client_id", pending["client_id"])
    if not client.startswith("oaiapp_") or (pending["client_id"] != "dynamic_agent_client" and client != pending["client_id"]):
        raise ValueError("Регистрация приложения не подтверждена")
    if not query.get("code"): raise ValueError("OpenAI не вернул код входа")
    tokens = request_json(TOKEN, dict(grant_type="authorization_code", client_id=client, code=query["code"],
                                    code_verifier=pending["verifier"], redirect_uri=pending["redirect"], resource=RESOURCE))
    try:
        encoded = tokens["id_token"]
        # Enforce the allow-list before JWK selection as well as during verification.
        if jwt.get_unverified_header(encoded).get("alg") != "RS256": raise ValueError()
        key = jwt.PyJWKClient("https://auth.openai.com/.well-known/jwks.json", timeout=20).get_signing_key_from_jwt(encoded)
        claims = jwt.decode(encoded, key.key, algorithms=["RS256"], audience=client, issuer="https://auth.openai.com",
                            options={"require": ["sub", "exp", "iat", "nonce"]}, leeway=CLOCK_SKEW_SECONDS)
    except jwt.ImmatureSignatureError:
        raise ValueError("Время Windows отстаёт от OpenAI более чем на 30 секунд. Синхронизируй часы в настройках Windows и повтори вход.") from None
    except jwt.ExpiredSignatureError:
        raise ValueError("Подтверждение входа истекло или часы Windows спешат. Проверь время и начни новый вход.") from None
    except jwt.InvalidIssuerError:
        raise ValueError("Издатель подтверждения входа не совпадает с OpenAI. Подключение отклонено.") from None
    except jwt.InvalidAudienceError:
        raise ValueError("Подтверждение входа выдано другому приложению. Начни новый вход из Радара.") from None
    except jwt.PyJWKClientConnectionError:
        raise ValueError("Не удалось получить ключи проверки OpenAI. Проверь доступ к auth.openai.com и повтори вход.") from None
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        raise ValueError("Не удалось проверить подпись или обязательные поля подтверждения OpenAI. Подключение отклонено.") from None
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], pending["nonce"]):
        raise ValueError("Подтверждение относится к другому сеансу входа. Начни вход заново из Радара.")
    if pending["subject"] and claims["sub"] != pending["subject"]:
        raise ValueError("Выполнен вход в другой аккаунт ChatGPT. Выбери ранее подключённый аккаунт.")
    if "chatgpt.tokens.use.direct" not in str(tokens.get("scope", "")).split():
        raise ValueError("Вход в аккаунт подтверждён, но OpenAI не предоставил разрешение использовать план ChatGPT. Включи использование плана для Радара и повтори вход.")
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise ValueError("OpenAI не предоставил полный доступ для анализа и обновления сеанса. Подключение не сохранено; повтори вход с разрешением использования плана.")
    with credential_lock():
        save({**tokens, "client_id": client, "subject": claims["sub"], "email": claims.get("email", ""),
              "expires_at": time.time() + int(tokens.get("expires_in", 3600))})


def access_token() -> str:
    with credential_lock():
        record = load()
        if not record: raise ValueError("Сначала подключи ChatGPT в локальном приложении")
        if record["expires_at"] < time.time() + 120:
            fresh = request_json(TOKEN, dict(grant_type="refresh_token", client_id=record["client_id"],
                                refresh_token=record["refresh_token"], resource=RESOURCE))
            if not fresh.get("access_token"): raise RemoteError(401)
            record.update(fresh)
            record["expires_at"] = time.time() + int(fresh.get("expires_in", 3600))
            save(record)
        return record["access_token"]


def status() -> dict:
    try:
        record = load()
        return {"connected": bool(record), "email": record.get("email", "")}
    except (OSError, ValueError):
        return {"connected": False, "email": "", "error": "Хранилище требует повторного входа"}


def disconnect() -> bool:
    confirmed = False
    with credential_lock():
        record = load()
        if record:
            try:
                discovery = request_json("https://auth.openai.com/.well-known/openid-configuration")
                endpoint = discovery.get("revocation_endpoint", "")
                if endpoint.startswith("https://auth.openai.com/"):
                    request = Request(endpoint, data=urlencode({"token": record["refresh_token"], "token_type_hint": "refresh_token",
                                      "client_id": record["client_id"]}).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
                    with urlopen(request, timeout=20) as response: confirmed = response.status == 200
            except (RemoteError, OSError, HTTPError, URLError):
                pass
        (ROOT / "credentials.bin").unlink(missing_ok=True)
    return confirmed


def models() -> list[dict]:
    response = request_json(RESOURCE + "/models", token=access_token())
    return [{"id": row["slug"], "name": row.get("display_name", row["slug"])}
            for row in response.get("models", []) if row.get("visibility") == "list" and row.get("slug")]


def respond(instructions: str, content: list, *, model: str, web: bool = False) -> dict:
    if not model: raise ValueError("Выбери модель своего аккаунта в приложении")
    payload = {"model": model, "instructions": instructions, "input": [{"role": "user", "content": content}],
               "store": False, "stream": True}
    if web: payload["tools"] = [{"type": "web_search"}]
    request = Request(RESOURCE + "/responses", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "Authorization": "Bearer " + access_token()})
    output, citations, completed = [], [], False
    try:
        with urlopen(request, timeout=180) as response:
            for raw in response:
                if len(raw) > 2_000_000: raise RemoteError()
                if not raw.startswith(b"data: "): continue
                value = raw[6:].strip()
                if value == b"[DONE]": continue
                event = json.loads(value)
                kind = event.get("type")
                if kind == "response.output_text.delta": output.append(event.get("delta", ""))
                elif kind in {"response.failed", "response.incomplete", "error"}: raise RemoteError()
                elif kind == "response.completed":
                    completed = True
                    for item in event.get("response", {}).get("output", []):
                        for block in item.get("content", []):
                            for annotation in block.get("annotations", []):
                                if annotation.get("type") == "url_citation":
                                    citations.append({"url": annotation.get("url"), "title": annotation.get("title", "Источник")})
        if not completed or not "".join(output).strip(): raise RemoteError()
        return {"text": "".join(output), "citations": citations}
    except HTTPError as error:
        raise RemoteError(error.code) from None
    except (OSError, URLError, ValueError):
        raise RemoteError() from None
