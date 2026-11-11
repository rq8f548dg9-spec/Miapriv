"""Applicazione FastAPI: serve il sito e risolve i link."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__
from .auth import check_password, make_token, read_token, shortcut_token
from .config import Settings, load_settings
from .extractor import (
    Item,
    NeedsLogin,
    NothingToDownload,
    TooBig,
    download_merged,
    extract,
    ffmpeg_version,
    have_ffmpeg,
)
from .store import TTLStore
from .urls import UnsafeUrl, UrlNotSupported, assert_public_url, normalize_shared_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scarico")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

FILE_TICKET_TTL = 6 * 3600  # un biglietto di download vale sei ore
THUMB_TICKET_TTL = 6 * 3600
MAX_THUMB_BYTES = 8 * 1024 * 1024

settings: Settings = load_settings()
jobs: TTLStore[Item] = TTLStore(ttl_seconds=FILE_TICKET_TTL)
extract_semaphore = asyncio.Semaphore(settings.max_concurrent)
merge_semaphore = asyncio.Semaphore(1)

app = FastAPI(title="Scarico Pin e Insta", version=__version__, docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- #
# Avvio
# --------------------------------------------------------------------------- #


@app.on_event("startup")
async def _startup() -> None:
    if settings.tmp_dir.exists():
        shutil.rmtree(settings.tmp_dir, ignore_errors=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    log.info("Scarico Pin e Insta %s pronto", __version__)
    log.info("ffmpeg: %s", ffmpeg_version() or "NON trovato (i video da ricomporre falliranno)")
    log.info("cookie Instagram: %s", settings.cookies_file or "nessuno (i post privati non funzioneranno)")
    if settings.secret_is_ephemeral:
        log.warning("SECRET_KEY temporanea: imposta SECRET_KEY nel file .env per non rifare il login a ogni riavvio")


# --------------------------------------------------------------------------- #
# Autenticazione
# --------------------------------------------------------------------------- #

_failed_logins: dict[str, list[float]] = {}
_LOGIN_WINDOW = 900.0
_LOGIN_MAX_TRIES = 8


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "sconosciuto"


def _login_blocked(key: str) -> bool:
    now = time.monotonic()
    tries = [t for t in _failed_logins.get(key, []) if now - t < _LOGIN_WINDOW]
    _failed_logins[key] = tries
    return len(tries) >= _LOGIN_MAX_TRIES


def _record_failure(key: str) -> None:
    _failed_logins.setdefault(key, []).append(time.monotonic())


async def require_session(
    authorization: str | None = Header(default=None),
    x_token: str | None = Header(default=None),
) -> dict[str, object]:
    token = x_token or ""
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    claims = read_token(settings.secret_key, token, "sess")
    if claims is None:
        raise HTTPException(status_code=401, detail="Sessione scaduta: rifai il login.")
    return claims


def _base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}"


# --------------------------------------------------------------------------- #
# Pagine e file statici
# --------------------------------------------------------------------------- #

_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers=_NO_STORE)


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    # Il service worker deve stare alla radice per avere come scope tutto il sito,
    # e non va mai messo in cache, altrimenti gli aggiornamenti non arrivano.
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript", headers=_NO_STORE)


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> FileResponse:
    return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/api/health", include_in_schema=False)
async def health() -> dict[str, object]:
    return {"ok": True, "version": __version__, "ffmpeg": have_ffmpeg(), "in_memoria": len(jobs)}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@app.post("/api/login")
async def login(request: Request) -> JSONResponse:
    key = _client_key(request)
    if _login_blocked(key):
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova tra un quarto d'ora.")
    try:
        body = await request.json()
    except ValueError:
        body = {}
    given = str((body or {}).get("password") or "")
    if not check_password(settings.password, given):
        _record_failure(key)
        log.warning("login fallito da %s", key)
        raise HTTPException(status_code=401, detail="Password sbagliata.")
    _failed_logins.pop(key, None)
    token = make_token(settings.secret_key, "sess", settings.session_days * 86400)
    return JSONResponse({"token": token, "giorni": settings.session_days}, headers=_NO_STORE)


@app.get("/api/me")
async def me(request: Request, _: dict = Depends(require_session)) -> JSONResponse:
    return JSONResponse(
        {
            "versione": __version__,
            "ffmpeg": have_ffmpeg(),
            "cookie_instagram": bool(settings.cookies_file),
            "limite_mb": settings.max_filesize_mb,
            "token_scorciatoia": shortcut_token(settings.secret_key),
            "base": _base_url(request),
        },
        headers=_NO_STORE,
    )


async def _resolve(raw_url: str) -> list[Item]:
    try:
        url, source = normalize_shared_text(raw_url)
    except UrlNotSupported as exc:
        raise HTTPException(
            status_code=400,
            detail="Questo link non e' di Pinterest o Instagram. Incolla l'indirizzo di un pin, "
            "di un reel o di un post.",
        ) from exc

    async with extract_semaphore:
        try:
            items = await asyncio.to_thread(
                extract, url, source, settings.cookies_file, settings.max_filesize_bytes
            )
        except NeedsLogin as exc:
            log.info("serve login per %s: %s", url, exc)
            raise HTTPException(
                status_code=403,
                detail="Instagram chiede di essere loggati per questo contenuto. Serve il file "
                "cookies.txt sul server (vedi il README), oppure il post e' privato.",
            ) from exc
        except TooBig as exc:
            raise HTTPException(
                status_code=413,
                detail=f"Il file pesa {exc} e supera il limite di {settings.max_filesize_mb} MB.",
            ) from exc
        except NothingToDownload as exc:
            log.info("niente da scaricare in %s: %s", url, exc)
            raise HTTPException(
                status_code=404,
                detail="Non ho trovato nessun video o immagine in questo link.",
            ) from exc

    for item in items:
        jobs.put(item.jid, item)
    return items


def _with_tickets(items: list[Item]) -> list[dict[str, object]]:
    out = []
    for item in items:
        data = item.public_dict()
        data["url_file"] = f"/api/file/{item.jid}?t=" + make_token(
            settings.secret_key, "file", FILE_TICKET_TTL, jid=item.jid
        )
        if item.thumbnail:
            data["url_thumb"] = f"/api/thumb/{item.jid}?t=" + make_token(
                settings.secret_key, "thumb", THUMB_TICKET_TTL, jid=item.jid
            )
        out.append(data)
    return out


@app.post("/api/resolve")
async def resolve(request: Request, _: dict = Depends(require_session)) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    raw = str((body or {}).get("url") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Manca il link.")
    items = await _resolve(raw)
    log.info("risolti %d elementi da %s", len(items), items[0].page_url)
    return JSONResponse({"elementi": _with_tickets(items)}, headers=_NO_STORE)


@app.get("/api/shortcut")
async def shortcut(
    request: Request,
    k: str = Query(..., description="token delle Scorciatoie"),
    u: str = Query(..., description="link di Pinterest o Instagram"),
) -> JSONResponse:
    """Endpoint per l'app Scorciatoie di iOS: un link in ingresso, un file in uscita."""
    if read_token(settings.secret_key, k, "short") is None:
        raise HTTPException(status_code=401, detail="Token della scorciatoia non valido.")
    items = await _resolve(u)
    base = _base_url(request)
    enriched = _with_tickets(items)
    primo = enriched[0]
    return JSONResponse(
        {
            "ok": True,
            "download": base + str(primo["url_file"]),
            "nome_file": primo["filename"],
            "quanti": len(enriched),
            "tutti": [
                {"download": base + str(e["url_file"]), "nome_file": e["filename"]} for e in enriched
            ],
        },
        headers=_NO_STORE,
    )


# --------------------------------------------------------------------------- #
# Consegna dei file
# --------------------------------------------------------------------------- #


def _content_disposition(filename: str) -> str:
    """Nome del file per il browser, in versione ASCII e in versione UTF-8.

    Senza la variante ASCII i nomi con accenti arrivano storti ai client vecchi;
    senza filename* iOS taglia gli accenti.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii").replace('"', "")
    ) or "file"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


def _ticket_item(jid: str, token: str, aud: str) -> Item:
    claims = read_token(settings.secret_key, token, aud)
    if claims is None or claims.get("jid") != jid:
        raise HTTPException(status_code=403, detail="Link di download scaduto. Risolvi di nuovo il pin.")
    item = jobs.get(jid)
    if item is None:
        raise HTTPException(
            status_code=410,
            detail="Questo download non e' piu' in memoria (il server e' stato riavviato). Incolla di nuovo il link.",
        )
    return item


async def _proxy(
    item: Item,
    request: Request,
    *,
    attachment: bool,
    byte_cap: int | None = None,
) -> StreamingResponse:
    direct = item.direct_url
    if not direct:
        raise HTTPException(status_code=500, detail="Elemento senza URL diretto.")
    try:
        assert_public_url(direct)
    except UnsafeUrl as exc:
        log.warning("bloccato URL non pubblico: %s", exc)
        raise HTTPException(status_code=502, detail="L'indirizzo del file non e' pubblico.") from exc

    headers = dict(item.http_headers)
    incoming_range = request.headers.get("range")
    if incoming_range:
        headers["Range"] = incoming_range

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=900.0))
    try:
        upstream = await client.send(client.build_request("GET", direct, headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        log.warning("il CDN non risponde: %s", exc)
        raise HTTPException(status_code=504, detail="Il server di Pinterest/Instagram non risponde.") from exc

    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        log.warning("il CDN ha risposto %s per %s", code, item.jid)
        raise HTTPException(
            status_code=502,
            detail=f"Il file non e' piu' disponibile (il CDN ha risposto {code}). Riprova a risolvere il link.",
        )

    declared = upstream.headers.get("content-length")
    if byte_cap and declared and declared.isdigit() and int(declared) > byte_cap:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=413, detail="File troppo grande.")
    if settings.max_filesize_bytes and attachment and declared and declared.isdigit():
        if int(declared) > settings.max_filesize_bytes:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=413,
                detail=f"Il file pesa {int(declared) / 1048576:.0f} MB e supera il limite di {settings.max_filesize_mb} MB.",
            )

    out_headers = {"Cache-Control": "no-store, max-age=0"}
    for name in ("content-length", "content-range", "accept-ranges"):
        if name in upstream.headers:
            out_headers[name.title()] = upstream.headers[name]
    if attachment:
        out_headers["Content-Disposition"] = _content_disposition(item.filename)

    media_type = upstream.headers.get("content-type") or (
        "video/mp4" if item.kind == "video" else "image/jpeg"
    )

    async def closer() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_bytes(131072),
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=media_type,
        background=BackgroundTask(closer),
    )


@app.get("/api/thumb/{jid}")
async def thumb(jid: str, request: Request, t: str = Query(...)) -> StreamingResponse:
    """Le miniature passano dal server: il CDN non vede il telefono."""
    item = _ticket_item(jid, t, "thumb")
    stand_in = Item(
        jid=item.jid,
        source=item.source,
        kind="immagine",
        title=item.title,
        filename=item.filename,
        page_url=item.page_url,
        direct_url=item.thumbnail,
        http_headers=item.http_headers,
    )
    response = await _proxy(stand_in, request, attachment=False, byte_cap=MAX_THUMB_BYTES)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.get("/api/file/{jid}")
async def file(jid: str, request: Request, t: str = Query(...)):
    item = _ticket_item(jid, t, "file")

    if not item.needs_merge:
        return await _proxy(item, request, attachment=True)

    if not have_ffmpeg():
        raise HTTPException(
            status_code=501,
            detail="Questo video ha audio e video separati e serve ffmpeg sul server per unirli. "
            "Installa ffmpeg e riprova.",
        )

    async with merge_semaphore:
        work_dir = settings.tmp_dir / item.jid
        try:
            path = await asyncio.to_thread(download_merged, item, work_dir, settings.cookies_file)
        except NothingToDownload as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(status_code=502, detail=f"Non sono riuscito a comporre il video: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - qualunque errore di yt-dlp qui va riportato pulito
            shutil.rmtree(work_dir, ignore_errors=True)
            log.exception("errore durante la composizione di %s", item.jid)
            raise HTTPException(status_code=502, detail="Non sono riuscito a comporre il video.") from exc

        size = path.stat().st_size
        if settings.max_filesize_bytes and size > settings.max_filesize_bytes:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(
                status_code=413,
                detail=f"Il video pesa {size / 1048576:.0f} MB e supera il limite di {settings.max_filesize_mb} MB.",
            )

        filename = item.filename
        if path.suffix and not filename.lower().endswith(path.suffix.lower()):
            filename = filename.rsplit(".", 1)[0] + path.suffix
        return FileResponse(
            path,
            filename=filename,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store, max-age=0"},
            background=BackgroundTask(shutil.rmtree, work_dir, True),
        )


# Gli statici restano per ultimi: cosi' nessun file in web/ puo' coprire le API.
app.mount("/", StaticFiles(directory=WEB_DIR, html=False), name="web")
