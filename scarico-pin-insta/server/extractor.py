"""Risoluzione dei link in file scaricabili, via yt-dlp."""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError

from .urls import INSTAGRAM, PINTEREST, UnsafeUrl, assert_public_url

log = logging.getLogger("scarico.extractor")

BROWSER_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


class NothingToDownload(RuntimeError):
    """Il link e' valido ma non contiene nulla di scaricabile."""


class NeedsLogin(RuntimeError):
    """Il contenuto e' privato o Instagram chiede di autenticarsi."""


class TooBig(RuntimeError):
    """Il file supera il limite configurato."""


@dataclass
class Item:
    """Un singolo file scaricabile."""

    jid: str
    source: str
    kind: str  # "video" oppure "immagine"
    title: str
    filename: str
    page_url: str
    direct_url: str | None = None
    http_headers: dict[str, str] = field(default_factory=dict)
    needs_merge: bool = False
    format_id: str | None = None
    ext: str = "mp4"
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    filesize: int | None = None
    thumbnail: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Quello che il telefono ha bisogno di sapere: mai l'URL del CDN."""
        return {
            "id": self.jid,
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "filename": self.filename,
            "ext": self.ext,
            "duration": round(self.duration) if self.duration else None,
            "width": self.width,
            "height": self.height,
            "filesize": self.filesize,
            "mime": mime_di(self.ext, self.kind),
            "has_thumb": bool(self.thumbnail),
            "merge": self.needs_merge,
        }


# --------------------------------------------------------------------------- #
# Nomi dei file
# --------------------------------------------------------------------------- #

# iOS decide "Salva video" o "Salva immagine" dal tipo MIME del file condiviso:
# se il tipo e' sbagliato o generico, la voce per la galleria non compare.
MIME_PER_ESTENSIONE = {
    "mp4": "video/mp4",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def mime_di(ext: str, kind: str) -> str:
    pulita = (ext or "").lower().lstrip(".")
    difetto = "video/mp4" if kind == "video" else "image/jpeg"
    return MIME_PER_ESTENSIONE.get(pulita, difetto)


_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_SPACES = re.compile(r"\s+")


def slugify(title: str, fallback: str = "video") -> str:
    """Nome file leggibile: tiene accenti e spazi, toglie cio' che rompe i filesystem."""
    text = unicodedata.normalize("NFC", title or "").strip()
    text = _BAD_CHARS.sub(" ", text)
    text = _SPACES.sub(" ", text).strip(" .-_")
    if len(text) > 70:
        text = text[:70].rstrip(" .-_")
    return text or fallback


def build_filename(source: str, title: str, ext: str, jid: str) -> str:
    prefix = "pinterest" if source == PINTEREST else "instagram"
    safe_ext = re.sub(r"[^a-z0-9]", "", (ext or "mp4").lower()) or "mp4"
    return f"{prefix} - {slugify(title)} - {jid[:6]}.{safe_ext}"


# --------------------------------------------------------------------------- #
# yt-dlp
# --------------------------------------------------------------------------- #


def _ydl_options(cookies_file: Path | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "skip_download": True,
        "noprogress": True,
        "socket_timeout": 30,
        "extractor_retries": 2,
        "http_headers": {"User-Agent": BROWSER_UA},
    }
    if cookies_file:
        opts["cookiefile"] = str(cookies_file)
    return opts


def _is_progressive(fmt: dict[str, Any]) -> bool:
    """Un formato progressive ha audio e video nello stesso file: si puo' servire al volo."""
    if not fmt.get("url"):
        return False
    protocol = (fmt.get("protocol") or "https").lower()
    if not protocol.startswith("http") or protocol.startswith("http_dash"):
        return False
    return fmt.get("vcodec") not in (None, "none") and fmt.get("acodec") not in (None, "none")


def _format_rank(fmt: dict[str, Any]) -> tuple:
    return (
        fmt.get("height") or 0,
        fmt.get("tbr") or 0,
        fmt.get("filesize") or fmt.get("filesize_approx") or 0,
    )


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if not thumbs:
        return None
    thumbs.sort(key=lambda t: (t.get("preference") or 0, t.get("width") or 0))
    return thumbs[-1]["url"]


def _entries_of(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Appiattisce i post con piu' elementi (caroselli Instagram, bacheche)."""
    if info.get("_type") not in ("playlist", "multi_video"):
        return [info]
    flat: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        flat.extend(_entries_of(entry))
    return flat


def _item_from_entry(entry: dict[str, Any], source: str, page_url: str, max_bytes: int) -> Item | None:
    formats = [f for f in (entry.get("formats") or []) if f.get("url")]
    progressive = [f for f in formats if _is_progressive(f)]
    jid = secrets.token_urlsafe(9)
    title = entry.get("title") or entry.get("description") or entry.get("id") or "senza titolo"
    title = str(title).splitlines()[0]

    if progressive:
        chosen = max(progressive, key=_format_rank)
        size = chosen.get("filesize") or chosen.get("filesize_approx")
        if max_bytes and size and size > max_bytes:
            raise TooBig(f"{size / 1048576:.0f} MB")
        ext = chosen.get("ext") or "mp4"
        return Item(
            jid=jid,
            source=source,
            kind="video",
            title=title,
            filename=build_filename(source, title, ext, jid),
            page_url=page_url,
            direct_url=chosen["url"],
            http_headers={**{"User-Agent": BROWSER_UA}, **(chosen.get("http_headers") or {})},
            format_id=chosen.get("format_id"),
            ext=ext,
            duration=entry.get("duration"),
            width=chosen.get("width") or entry.get("width"),
            height=chosen.get("height") or entry.get("height"),
            filesize=size,
            thumbnail=_best_thumbnail(entry),
        )

    if formats:
        # Solo flussi separati o HLS: va ricomposto con ffmpeg lato server.
        best = max(formats, key=_format_rank)
        return Item(
            jid=jid,
            source=source,
            kind="video",
            title=title,
            filename=build_filename(source, title, "mp4", jid),
            page_url=entry.get("webpage_url") or page_url,
            needs_merge=True,
            ext="mp4",
            duration=entry.get("duration"),
            width=best.get("width") or entry.get("width"),
            height=best.get("height") or entry.get("height"),
            thumbnail=_best_thumbnail(entry),
        )

    thumb = _best_thumbnail(entry)
    if thumb:
        # Pin fotografico: non c'e' video, ma l'immagine a piena risoluzione si'.
        ext = "jpg" if ".png" not in thumb.lower() else "png"
        return Item(
            jid=jid,
            source=source,
            kind="immagine",
            title=title,
            filename=build_filename(source, title, ext, jid),
            page_url=page_url,
            direct_url=thumb,
            http_headers={"User-Agent": BROWSER_UA},
            ext=ext,
            width=entry.get("width"),
            height=entry.get("height"),
            thumbnail=thumb,
        )
    return None


_LOGIN_HINTS = (
    "login required",
    "requested content is not available",
    "rate-limit",
    "rate limit",
    "empty media response",
    "main webpage is locked behind the login page",
    "you need to log in",
    "private",
)


def extract(url: str, source: str, cookies_file: Path | None, max_bytes: int) -> list[Item]:
    """Risolve un link in una lista di elementi scaricabili."""
    try:
        with YoutubeDL(_ydl_options(cookies_file)) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as exc:
        message = str(exc)
        lowered = message.lower()
        if any(hint in lowered for hint in _LOGIN_HINTS):
            raise NeedsLogin(message) from exc
        if source == PINTEREST:
            fallback = pinterest_fallback(url, max_bytes)
            if fallback:
                return fallback
        log.info("estrazione fallita per %s: %s", url, message)
        raise NothingToDownload(message) from exc

    if not info:
        raise NothingToDownload("yt-dlp non ha restituito nulla")

    items: list[Item] = []
    too_big: TooBig | None = None
    for entry in _entries_of(info):
        try:
            item = _item_from_entry(entry, source, info.get("webpage_url") or url, max_bytes)
        except TooBig as exc:
            too_big = exc
            continue
        if item:
            items.append(item)

    if not items:
        if too_big:
            raise too_big
        if source == PINTEREST:
            fallback = pinterest_fallback(url, max_bytes)
            if fallback:
                return fallback
        raise NothingToDownload("nessun video o immagine in questo link")
    return items


# --------------------------------------------------------------------------- #
# Ripiego per Pinterest: leggere i meta tag della pagina
# --------------------------------------------------------------------------- #

_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](og:video(?::secure_url|:url)?|og:image|og:title)["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_RE_REVERSED = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](og:video(?::secure_url|:url)?|og:image|og:title)["\']',
    re.IGNORECASE,
)


def pinterest_fallback(url: str, max_bytes: int) -> list[Item]:
    """Quando yt-dlp non riconosce un pin, i meta tag Open Graph bastano spesso."""
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "it-IT,it;q=0.9"},
            follow_redirects=True,
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("ripiego Pinterest fallito su %s: %s", url, exc)
        return []

    found: dict[str, str] = {}
    for pattern in (_META_RE, _META_RE_REVERSED):
        for first, second in pattern.findall(response.text):
            key, value = (first, second) if first.startswith("og:") else (second, first)
            found.setdefault(key.lower(), value)

    video = found.get("og:video:secure_url") or found.get("og:video:url") or found.get("og:video")
    image = found.get("og:image")
    title = found.get("og:title") or "pin"
    media = video or image
    if not media:
        return []
    try:
        assert_public_url(media)
    except UnsafeUrl:
        return []

    jid = secrets.token_urlsafe(9)
    ext = media.rsplit(".", 1)[-1].split("?")[0].lower()
    if ext not in ("mp4", "m4v", "mov", "jpg", "jpeg", "png", "webp", "gif"):
        ext = "mp4" if video else "jpg"
    return [
        Item(
            jid=jid,
            source=PINTEREST,
            kind="video" if video else "immagine",
            title=title,
            filename=build_filename(PINTEREST, title, ext, jid),
            page_url=url,
            direct_url=media,
            http_headers={"User-Agent": BROWSER_UA, "Referer": "https://www.pinterest.com/"},
            ext=ext,
            thumbnail=image or media,
        )
    ]


# --------------------------------------------------------------------------- #
# Download con ricomposizione (serve ffmpeg)
# --------------------------------------------------------------------------- #


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def download_merged(item: Item, dest_dir: Path, cookies_file: Path | None) -> Path:
    """Scarica e ricompone audio+video in un mp4 nella cartella indicata."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = _ydl_options(cookies_file)
    opts.update(
        {
            "skip_download": False,
            "outtmpl": str(dest_dir / "media.%(ext)s"),
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "concurrent_fragment_downloads": 4,
            "retries": 3,
        }
    )
    with YoutubeDL(opts) as ydl:
        ydl.download([item.page_url])

    produced = sorted(dest_dir.glob("media.*"), key=lambda p: p.stat().st_size, reverse=True)
    produced = [p for p in produced if p.suffix.lower() not in (".part", ".ytdl")]
    if not produced:
        raise NothingToDownload("ffmpeg non ha prodotto alcun file")
    return produced[0]


def ffmpeg_version() -> str | None:
    if not have_ffmpeg():
        return None
    try:
        out = subprocess.run(  # noqa: S603 - percorso da shutil.which, nessun input utente
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (out.stdout or "").splitlines()
    return first[0] if first else None
