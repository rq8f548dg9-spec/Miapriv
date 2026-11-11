"""Token firmati con HMAC-SHA256.

Due tipi di token, distinti dal campo "aud" perche' un token di sessione non
deve poter essere usato come biglietto di download e viceversa:

- "file"  : biglietto per un singolo download, viaggia nella query string
            (serve perche' su iOS il download puo' partire da una navigazione,
            e una navigazione non puo' portare intestazioni su misura).
- "thumb" : biglietto per la miniatura.
- "short" : token per le Scorciatoie iOS, senza scadenza.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

_SEP = "."


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return _b64e(digest)


def make_token(secret: str, aud: str, ttl_seconds: int | None, **claims: object) -> str:
    """Crea un token firmato. ttl_seconds None = nessuna scadenza."""
    payload: dict[str, object] = {"aud": aud, **claims}
    if ttl_seconds is not None:
        payload["exp"] = int(time.time()) + int(ttl_seconds)
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + _SEP + _sign(secret, body)


def read_token(secret: str, token: str, aud: str) -> dict[str, object] | None:
    """Verifica firma, pubblico e scadenza. Ritorna i claim o None."""
    if not token or token.count(_SEP) != 1:
        return None
    body, signature = token.split(_SEP, 1)
    expected = _sign(secret, body)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("aud") != aud:
        return None
    exp = payload.get("exp")
    if exp is not None:
        if not isinstance(exp, int) or exp < time.time():
            return None
    return payload


def check_key(expected: str, given: str | None) -> bool:
    """Confronto a tempo costante: la chiave non si indovina a tentativi misurati."""
    return hmac.compare_digest(expected.encode("utf-8"), (given or "").encode("utf-8"))


def shortcut_token(secret: str) -> str:
    """Token stabile per le Scorciatoie iOS: cambia solo se cambia SECRET_KEY."""
    return make_token(secret, "short", None, v=1)
