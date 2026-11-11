import pytest
from fastapi.testclient import TestClient

from conftest import CHIAVE
from server import main
from server.auth import make_token


@pytest.fixture()
def client():
    main._tentativi.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def con_chiave(client):
    client.headers.update({"X-Chiave": CHIAVE})
    return client


def test_salute(client):
    assert client.get("/api/health").json()["ok"] is True


def test_la_pagina_si_apre_anche_senza_chiave(client):
    # Senza chiave la pagina deve comunque caricarsi: e' il sito a spiegare
    # che serve il link completo, invece di sbattere in faccia un errore.
    risposta = client.get("/")
    assert risposta.status_code == 200
    assert "Serve il tuo link personale" in risposta.text


def test_gli_statici_ci_sono(client):
    for percorso in ("/style.css", "/app.js", "/icons/icon-180.png"):
        assert client.get(percorso).status_code == 200, percorso


def test_il_prefisso_sparisce_quando_il_sito_sta_alla_radice(client):
    pagina = client.get("/").text
    assert "__BASE__" not in pagina
    assert 'href="/style.css"' in pagina
    assert 'data-base=""' in pagina


def test_senza_chiave_le_api_sono_chiuse(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/resolve", json={"url": "https://pin.it/x"}).status_code == 401
    assert client.get("/api/shortcut", params={"u": "https://pin.it/x"}).status_code == 401


def test_chiave_sbagliata_rifiutata(client):
    assert client.get("/api/me", headers={"X-Chiave": "quasi-giusta"}).status_code == 401


def test_la_chiave_vale_anche_nell_indirizzo(client):
    # Serve per le Scorciatoie e per i link aperti da Safari, che non possono
    # aggiungere intestazioni su misura.
    assert client.get("/api/me", params={"k": CHIAVE}).status_code == 200


def test_me_racconta_lo_stato(con_chiave):
    corpo = con_chiave.get("/api/me").json()
    assert "versione" in corpo and "ffmpeg" in corpo
    # La chiave non deve mai tornare indietro in una risposta.
    assert CHIAVE not in str(corpo)


def test_link_di_un_altro_sito_rifiutato(con_chiave):
    risposta = con_chiave.post("/api/resolve", json={"url": "https://www.youtube.com/watch?v=x"})
    assert risposta.status_code == 400
    assert "Pinterest" in risposta.json()["detail"]


def test_link_mancante(con_chiave):
    assert con_chiave.post("/api/resolve", json={"url": "  "}).status_code == 400


def test_biglietto_per_un_altro_file_non_vale(con_chiave):
    biglietto = make_token(main.settings.secret_key, "file", 60, jid="uno")
    assert con_chiave.get(f"/api/file/due?t={biglietto}").status_code == 403


def test_la_chiave_daccesso_non_e_un_biglietto(con_chiave):
    assert con_chiave.get(f"/api/file/abc?t={CHIAVE}").status_code == 403


def test_biglietto_giusto_ma_lavoro_sparito(con_chiave):
    biglietto = make_token(main.settings.secret_key, "file", 60, jid="sparito")
    assert con_chiave.get(f"/api/file/sparito?t={biglietto}").status_code == 410


def test_troppi_tentativi_bloccano(client):
    for _ in range(main._MAX_TENTATIVI):
        client.get("/api/me", headers={"X-Chiave": "sbagliata"})
    assert client.get("/api/me", headers={"X-Chiave": "sbagliata"}).status_code == 429
    main._tentativi.clear()
    assert client.get("/api/me", headers={"X-Chiave": CHIAVE}).status_code == 200


class TestManifest:
    """Il manifest decide dove punta l'icona sulla schermata Home."""

    def test_senza_chiave_parte_dalla_radice(self, client):
        assert client.get("/manifest.webmanifest").json()["start_url"] == "/"

    def test_con_la_chiave_se_la_porta_dietro(self, client):
        corpo = client.get("/manifest.webmanifest", params={"k": CHIAVE}).json()
        assert corpo["start_url"] == f"/?k={CHIAVE}"

    def test_una_chiave_falsa_non_entra_nel_manifest(self, client):
        assert client.get("/manifest.webmanifest", params={"k": "falsa"}).json()["start_url"] == "/"

    def test_le_icone_sono_dichiarate(self, client):
        icone = client.get("/manifest.webmanifest").json()["icons"]
        assert any(i.get("purpose") == "maskable" for i in icone)
        for icona in icone:
            assert client.get(icona["src"]).status_code == 200
