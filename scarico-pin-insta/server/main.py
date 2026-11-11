"""Applicazione FastAPI: serve il sito e risolve i link.

Non c'e' nessuna password da digitare. Il permesso sta dentro il link che ti
salvi sulla schermata Home (`?k=...`): chi non ha quel link non entra, tu non
devi ricordare niente.
"""

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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import __version__
from .auth import check_key, make_token, read_token
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

# Il sito puo' girare in due modi: dal progetto, leggendo la cartella web/,
# oppure come file unico (vedi tools/build_hf.py), dove le pagine sono stringhe
# dentro al programma e le icone vengono disegnate all'avvio.
RISORSE_WEB: dict[str, str] = {}
GENERATORE_ICONE = None


def _pagina(nome: str, media_type: str) -> Response:
    contenuto = RISORSE_WEB.get(nome)
    if contenuto is not None:
        return Response(contenuto, media_type=media_type, headers=_NO_STORE)
    return FileResponse(WEB_DIR / nome, media_type=media_type, headers=_NO_STORE)

TICKET_TTL = 6 * 3600  # un biglietto vale sei ore
MAX_THUMB_BYTES = 8 * 1024 * 1024

settings: Settings = load_settings()
jobs: TTLStore[Item] = TTLStore(ttl_seconds=TICKET_TTL)
extract_semaphore = asyncio.Semaphore(settings.max_concurrent)
merge_semaphore = asyncio.Semaphore(1)

app = FastAPI(title="Scarico Pin e Insta", version=__version__, docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _startup() -> None:
    shutil.rmtree(settings.tmp_dir, ignore_errors=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    log.info("Scarico Pin e Insta %s pronto", __version__)
    log.info("ffmpeg: %s", ffmpeg_version() or "NON trovato (i video da ricomporre falliranno)")
    log.info("cookie Instagram: %s", "si" if settings.cookies_file else "no (molti post falliranno)")
    if settings.open_mode:
        log.warning("OPEN_MODE attivo: il sito e' aperto a chiunque conosca l'indirizzo")


# --------------------------------------------------------------------------- #
# Chiave d'accesso
# --------------------------------------------------------------------------- #

_tentativi: dict[str, list[float]] = {}
_FINESTRA = 900.0
_MAX_TENTATIVI = 20


def _chi(request: Request) -> str:
    inoltrato = request.headers.get("x-forwarded-for", "")
    if inoltrato:
        return inoltrato.split(",")[0].strip()
    return request.client.host if request.client else "sconosciuto"


def _troppi_tentativi(chiave: str) -> bool:
    ora = time.monotonic()
    recenti = [t for t in _tentativi.get(chiave, []) if ora - t < _FINESTRA]
    _tentativi[chiave] = recenti
    return len(recenti) >= _MAX_TENTATIVI


async def require_key(
    request: Request,
    k: str | None = Query(default=None, description="chiave d'accesso"),
    x_chiave: str | None = Header(default=None),
) -> None:
    """Il permesso arriva dal link (`?k=`) o dall'intestazione X-Chiave."""
    if settings.open_mode:
        return
    fornita = x_chiave or k
    if check_key(settings.access_key, fornita):
        _tentativi.pop(_chi(request), None)
        return
    chi = _chi(request)
    if _troppi_tentativi(chi):
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova tra un quarto d'ora.")
    _tentativi.setdefault(chi, []).append(time.monotonic())
    log.warning("chiave mancante o sbagliata da %s", chi)
    raise HTTPException(
        status_code=401,
        detail="Questo indirizzo non e' completo. Apri il tuo link personale, quello che finisce con ?k=...",
    )


def _base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}"


# --------------------------------------------------------------------------- #
# Pagine e file statici
# --------------------------------------------------------------------------- #

_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/", include_in_schema=False)
async def index() -> Response:
    # La pagina si apre sempre: e' il JavaScript a dire "serve il link completo"
    # se la chiave non c'e'. Una pagina di errore secca spaventerebbe e basta.
    return _pagina("index.html", "text/html; charset=utf-8")


@app.get("/style.css", include_in_schema=False)
async def foglio_di_stile() -> Response:
    return _pagina("style.css", "text/css; charset=utf-8")


@app.get("/app.js", include_in_schema=False)
async def programma() -> Response:
    return _pagina("app.js", "application/javascript; charset=utf-8")


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    return _pagina("sw.js", "application/javascript; charset=utf-8")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest(k: str | None = Query(default=None)) -> JSONResponse:
    """Il manifest si porta dietro la chiave.

    Quando iOS aggiunge il sito alla schermata Home puo' usare `start_url`:
    se li' dentro la chiave non ci fosse, l'icona aprirebbe un indirizzo nudo
    e il sito direbbe "serve il link completo" ogni volta.
    """
    start = "/"
    if k and (settings.open_mode or check_key(settings.access_key, k)):
        start = f"/?k={quote(k, safe='')}"
    return JSONResponse(
        {
            "name": "Scarico Pin e Insta",
            "short_name": "Scarico",
            "description": "Salva in galleria i video e le foto di Pinterest e Instagram.",
            "lang": "it",
            "start_url": start,
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0c0a12",
            "theme_color": "#0c0a12",
            "icons": [
                {"src": "/icons/icon-180.png", "sizes": "180x180", "type": "image/png"},
                {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        },
        media_type="application/manifest+json",
        headers=_NO_STORE,
    )


@app.get("/api/health", include_in_schema=False)
async def health() -> dict[str, object]:
    return {"ok": True, "version": __version__, "ffmpeg": have_ffmpeg(), "in_memoria": len(jobs)}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@app.get("/api/me")
async def me(_: None = Depends(require_key)) -> JSONResponse:
    return JSONResponse(
        {
            "versione": __version__,
            "ffmpeg": have_ffmpeg(),
            "cookie_instagram": bool(settings.cookies_file),
            "limite_mb": settings.max_filesize_mb,
        },
        headers=_NO_STORE,
    )


async def _resolve(raw_url: str) -> list[Item]:
    try:
        url, source = normalize_shared_text(raw_url)
    except UrlNotSupported as exc:
        raise HTTPException(
            status_code=400,
            detail="Questo link non e' di Pinterest o Instagram. Copia l'indirizzo di un pin, "
            "di un reel, di una storia o di un post.",
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
                detail="Instagram chiede di essere loggati per questo contenuto. Aggiungi i cookie "
                "nelle impostazioni del server, oppure il post e' privato.",
            ) from exc
        except TooBig as exc:
            raise HTTPException(
                status_code=413,
                detail=f"Il file pesa {exc} e supera il limite di {settings.max_filesize_mb} MB.",
            ) from exc
        except NothingToDownload as exc:
            log.info("niente da scaricare in %s: %s", url, exc)
            raise HTTPException(
                status_code=404, detail="Non ho trovato nessun video o foto in questo link."
            ) from exc

    for item in items:
        jobs.put(item.jid, item)
    return items


def _with_tickets(items: list[Item]) -> list[dict[str, object]]:
    out = []
    for item in items:
        data = item.public_dict()
        biglietto = make_token(settings.secret_key, "file", TICKET_TTL, jid=item.jid)
        data["url_file"] = f"/api/file/{item.jid}?t={biglietto}"
        if item.thumbnail:
            data["url_thumb"] = f"/api/thumb/{item.jid}?t=" + make_token(
                settings.secret_key, "thumb", TICKET_TTL, jid=item.jid
            )
        out.append(data)
    return out


@app.post("/api/resolve")
async def resolve(request: Request, _: None = Depends(require_key)) -> JSONResponse:
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
    u: str = Query(..., description="link di Pinterest o Instagram"),
    _: None = Depends(require_key),
) -> JSONResponse:
    """Per l'app Comandi rapidi di iOS: un link in ingresso, un file in uscita."""
    items = await _resolve(u)
    base = _base_url(request)
    arricchiti = _with_tickets(items)
    primo = arricchiti[0]
    return JSONResponse(
        {
            "ok": True,
            "download": base + str(primo["url_file"]),
            "nome_file": primo["filename"],
            "tipo": primo["mime"],
            "quanti": len(arricchiti),
            "tutti": [
                {"download": base + str(e["url_file"]), "nome_file": e["filename"], "tipo": e["mime"]}
                for e in arricchiti
            ],
        },
        headers=_NO_STORE,
    )


# --------------------------------------------------------------------------- #
# Consegna dei file
# --------------------------------------------------------------------------- #


def _content_disposition(filename: str) -> str:
    """Nome del file per il browser, in versione ASCII e in versione UTF-8."""
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii").replace('"', "")
    ) or "file"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


def _ticket_item(jid: str, token: str, aud: str) -> Item:
    claims = read_token(settings.secret_key, token, aud)
    if claims is None or claims.get("jid") != jid:
        raise HTTPException(status_code=403, detail="Questo link di download e' scaduto. Cerca di nuovo il post.")
    item = jobs.get(jid)
    if item is None:
        raise HTTPException(
            status_code=410,
            detail="Il server si e' riavviato e ha perso questo download. Incolla di nuovo il link.",
        )
    return item


async def _proxy(
    item: Item, request: Request, *, attachment: bool, byte_cap: int | None = None
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
            detail=f"Il file non e' piu' disponibile (il CDN ha risposto {code}). Cerca di nuovo il post.",
        )

    declared = upstream.headers.get("content-length")
    misura = int(declared) if declared and declared.isdigit() else None
    if misura is not None:
        if byte_cap and misura > byte_cap:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(status_code=413, detail="File troppo grande.")
        if attachment and settings.max_filesize_bytes and misura > settings.max_filesize_bytes:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=413,
                detail=f"Il file pesa {misura / 1048576:.0f} MB e supera il limite di {settings.max_filesize_mb} MB.",
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
    """Le miniature passano dal server: il CDN non vede mai il telefono."""
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
    risposta = await _proxy(stand_in, request, attachment=False, byte_cap=MAX_THUMB_BYTES)
    risposta.headers["Cache-Control"] = "private, max-age=3600"
    return risposta


@app.get("/api/file/{jid}")
async def file(jid: str, request: Request, t: str = Query(...), apri: int = Query(0)):
    """Consegna il file.

    `apri=1` lo manda senza l'intestazione "da salvare": serve quando la pagina
    lo legge in memoria per passarlo al foglio di condivisione di iOS, o quando
    lo apre per mostrarlo.
    """
    item = _ticket_item(jid, t, "file")

    if not item.needs_merge:
        return await _proxy(item, request, attachment=not apri)

    if not have_ffmpeg():
        raise HTTPException(
            status_code=501,
            detail="Questo video ha audio e video separati e serve ffmpeg sul server per unirli.",
        )

    async with merge_semaphore:
        work_dir = settings.tmp_dir / item.jid
        try:
            path = await asyncio.to_thread(download_merged, item, work_dir, settings.cookies_file)
        except NothingToDownload as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(status_code=502, detail=f"Non sono riuscito a comporre il video: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - qualunque errore di yt-dlp va riportato pulito
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
            filename=None if apri else filename,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store, max-age=0"},
            background=BackgroundTask(shutil.rmtree, work_dir, True),
        )


def monta_statici() -> None:
    """Le icone e gli altri file: da disco se ci sono, altrimenti disegnate."""
    icone = WEB_DIR / "icons"
    if not icone.is_dir() and GENERATORE_ICONE is not None:
        icone = GENERATORE_ICONE()
    if icone.is_dir():
        app.mount("/icons", StaticFiles(directory=icone), name="icone")
    # Per ultimo, cosi' nessun file statico puo' coprire le API.
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=False), name="web")


monta_statici()
