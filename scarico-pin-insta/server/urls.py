"""Riconoscimento e pulizia dei link, piu' la difesa contro le richieste interne.

Due controlli separati, con scopi diversi:

1. `normalize_shared_text` guarda il link che arriva dal telefono. Accetta solo
   Pinterest e Instagram: senza questo filtro il server diventerebbe un proxy
   aperto con cui chiunque conosca la password potrebbe scaricare qualsiasi cosa.
2. `assert_public_url` guarda il link del file che il server sta per aprire.
   Serve a impedire che un URL restituito da terzi faccia bussare il server a
   indirizzi della rete locale (127.0.0.1, 192.168.x.x, metadata dei cloud).
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PINTEREST = "pinterest"
INSTAGRAM = "instagram"

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Parametri che vale la pena tenere: tutto il resto e' tracciamento.
_KEEP_QUERY = {"img_index"}

# Host interi, per gli accorciatori che non hanno il nome del sito nel dominio.
_HOST_ESATTI = {"pin.it": PINTEREST, "ig.me": INSTAGRAM, "instagr.am": INSTAGRAM}

# Nome registrabile del dominio, cioe' la parte prima del suffisso pubblico.
_NOMI = {"pinterest": PINTEREST, "instagram": INSTAGRAM}

# Suffissi a due livelli in cui il nome sta una etichetta piu' indietro
# (pinterest.co.uk, instagram.com.br).
_SUFFISSI_DOPPI = {"co", "com", "net", "org", "ne", "or"}


class UrlNotSupported(ValueError):
    """Il link non e' di Pinterest ne' di Instagram."""


class UnsafeUrl(ValueError):
    """Il link punta dentro la rete locale, o non e' http(s)."""


def _nome_registrabile(etichette: list[str]) -> str | None:
    """La parte di dominio che conta: "pinterest" in www.pinterest.co.uk.

    Guardare solo l'inizio o un pezzo qualsiasi dell'host non basta:
    pinterest.evil.com e instagram.com.attacco.net contengono il nome giusto
    ma appartengono a qualcun altro.
    """
    if len(etichette) < 2:
        return None
    if len(etichette) >= 3 and etichette[-2] in _SUFFISSI_DOPPI and len(etichette[-1]) == 2:
        return etichette[-3]
    return etichette[-2]


def classify(host: str | None) -> str | None:
    """Ritorna "pinterest", "instagram" oppure None."""
    if not host:
        return None
    h = host.lower().strip().strip(".")
    if h.startswith("www."):
        h = h[4:]
    if h in _HOST_ESATTI:
        return _HOST_ESATTI[h]
    nome = _nome_registrabile(h.split("."))
    return _NOMI.get(nome) if nome else None


def clean_url(raw: str) -> tuple[str, str]:
    """Pulisce un singolo URL. Ritorna (url_pulito, sorgente)."""
    parsed = urlparse(raw.strip())
    source = classify(parsed.hostname)
    if parsed.scheme not in ("http", "https") or source is None:
        raise UrlNotSupported(raw)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k in _KEEP_QUERY]
    # Le URL dei pin e dei reel finiscono spesso con una / di troppo quando
    # arrivano dal foglio di condivisione: non cambia nulla, ma tenerle
    # uniformi evita di risolvere due volte lo stesso link.
    path = re.sub(r"/{2,}", "/", parsed.path)
    cleaned = urlunparse(("https", parsed.netloc.lower(), path, "", urlencode(query), ""))
    return cleaned, source


def normalize_shared_text(text: str) -> tuple[str, str]:
    """Estrae il primo link utile da un testo condiviso.

    Il foglio di condivisione di iOS incolla spesso titolo + link insieme,
    per questo non si puo' assumere che il testo sia solo un URL.
    """
    if not text or not text.strip():
        raise UrlNotSupported("testo vuoto")
    candidates = _URL_RE.findall(text)
    if not candidates:
        # L'utente puo' aver incollato "instagram.com/reel/..." senza schema.
        candidates = ["https://" + text.strip().split()[0]]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return clean_url(candidate.rstrip(").,;'\""))
        except UrlNotSupported as exc:  # noqa: PERF203 - pochi candidati
            last_error = exc
    raise UrlNotSupported(str(last_error) if last_error else text)


def _dns_check_enabled() -> bool:
    """Con un proxy HTTP di uscita la risoluzione DNS la fa il proxy, non noi.

    In quel caso il controllo sugli indirizzi IP non puo' funzionare e va
    disattivato esplicitamente, altrimenti bloccherebbe tutto.
    """
    if os.environ.get("SKIP_DNS_GUARD", "").strip() == "1":
        return False
    return not any(os.environ.get(var) for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))


def assert_public_url(url: str) -> None:
    """Alza UnsafeUrl se l'URL non e' http(s) pubblico."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl(f"schema non ammesso: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrl("host mancante")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        _assert_public_ip(literal, host)
        return

    if not _dns_check_enabled():
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"non riesco a risolvere {host}: {exc}") from exc
    for info in infos:
        _assert_public_ip(ipaddress.ip_address(info[4][0]), host)


def _assert_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UnsafeUrl(f"{host} risolve a {ip}, che e' un indirizzo interno")
