"""Prova la consegna del file: un finto CDN locale, e il server che lo rigira."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from conftest import CHIAVE
from server import main
from server.auth import make_token
from server.extractor import Item

CORPO = bytes(range(256)) * 400  # 102400 byte riconoscibili


class FintoCdn(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - nome imposto da BaseHTTPRequestHandler
        if self.path == "/sparito.mp4":
            self.send_response(404)
            self.end_headers()
            return

        if self.path == "/enorme.mp4":
            # Dichiara 4 GB senza mandarli: e' il caso che il limite deve fermare
            # prima di iniziare a scaricare.
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(4 * 1024**3))
            self.end_headers()
            return

        intervallo = self.headers.get("Range")
        if intervallo and intervallo.startswith("bytes="):
            inizio, _, fine = intervallo[6:].partition("-")
            a = int(inizio or 0)
            b = int(fine) if fine else len(CORPO) - 1
            pezzo = CORPO[a : b + 1]
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(pezzo)))
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(CORPO)}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(pezzo)
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(CORPO)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(CORPO)

    def log_message(self, *_):  # silenzio nei test
        return


@pytest.fixture(scope="module")
def cdn():
    server = HTTPServer(("127.0.0.1", 0), FintoCdn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture()
def client(monkeypatch):
    # Il finto CDN sta su 127.0.0.1, che la difesa contro le richieste interne
    # blocca giustamente: qui la disattivo solo per la durata della prova.
    monkeypatch.setattr(main, "assert_public_url", lambda _url: None)
    main._tentativi.clear()
    with TestClient(main.app) as c:
        c.headers.update({"X-Chiave": CHIAVE})
        yield c


def _prepara(cdn_url: str, percorso: str = "/video.mp4", nome: str = "pinterest - Caffè in città - abcdef.mp4") -> str:
    item = Item(
        jid="prova1",
        source="pinterest",
        kind="video",
        title="Caffè in città",
        filename=nome,
        page_url="https://www.pinterest.it/pin/1/",
        direct_url=cdn_url + percorso,
        http_headers={"User-Agent": "prova"},
        thumbnail=cdn_url + "/thumb.jpg",
    )
    main.jobs.put(item.jid, item)
    return make_token(main.settings.secret_key, "file", 600, jid=item.jid)


def test_il_file_arriva_intero(client, cdn):
    biglietto = _prepara(cdn)
    risposta = client.get(f"/api/file/prova1?t={biglietto}")
    assert risposta.status_code == 200
    assert risposta.content == CORPO
    assert risposta.headers["content-type"].startswith("video/mp4")


def test_con_apri_il_file_non_e_da_salvare(client, cdn):
    """E' il modo in cui la pagina legge il video per passarlo alla galleria."""
    biglietto = _prepara(cdn)
    risposta = client.get(f"/api/file/prova1?t={biglietto}&apri=1")
    assert risposta.status_code == 200
    assert risposta.content == CORPO
    assert "content-disposition" not in risposta.headers


def test_safari_riceve_il_nome_giusto(client, cdn):
    biglietto = _prepara(cdn)
    disposizione = client.get(f"/api/file/prova1?t={biglietto}").headers["content-disposition"]
    assert disposizione.startswith("attachment;")
    # Versione ASCII per i client vecchi e versione UTF-8 per iOS.
    assert 'filename="pinterest - Caffe in citta - abcdef.mp4"' in disposizione
    assert "filename*=UTF-8''" in disposizione
    assert "Caff%C3%A8" in disposizione


def test_le_richieste_parziali_passano(client, cdn):
    biglietto = _prepara(cdn)
    risposta = client.get(f"/api/file/prova1?t={biglietto}", headers={"Range": "bytes=10-19"})
    assert risposta.status_code == 206
    assert risposta.content == CORPO[10:20]
    assert risposta.headers["content-range"] == f"bytes 10-19/{len(CORPO)}"


def test_se_il_cdn_dice_404_lo_spiega(client, cdn):
    biglietto = _prepara(cdn, "/sparito.mp4")
    risposta = client.get(f"/api/file/prova1?t={biglietto}")
    assert risposta.status_code == 502
    assert "non e' piu' disponibile" in risposta.json()["detail"]


def test_il_file_troppo_grande_viene_fermato(client, cdn):
    # Il limite preimpostato e' 2048 MB: il finto CDN ne dichiara 4096.
    biglietto = _prepara(cdn, "/enorme.mp4")
    risposta = client.get(f"/api/file/prova1?t={biglietto}")
    assert risposta.status_code == 413
    assert "supera il limite" in risposta.json()["detail"]


def test_la_miniatura_passa_dal_server(client, cdn):
    _prepara(cdn)
    biglietto = make_token(main.settings.secret_key, "thumb", 600, jid="prova1")
    risposta = client.get(f"/api/thumb/prova1?t={biglietto}")
    assert risposta.status_code == 200
    # La miniatura si guarda, non si salva: nessun Content-Disposition.
    assert "content-disposition" not in risposta.headers


def test_il_biglietto_della_miniatura_non_apre_il_file(client, cdn):
    _prepara(cdn)
    biglietto = make_token(main.settings.secret_key, "thumb", 600, jid="prova1")
    assert client.get(f"/api/file/prova1?t={biglietto}").status_code == 403
