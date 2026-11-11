import pytest
from fastapi.testclient import TestClient

from server import main
from server.auth import make_token, shortcut_token

PASSWORD = "password-di-prova-lunga"


@pytest.fixture()
def client():
    main._failed_logins.clear()
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def loggato(client):
    risposta = client.post("/api/login", json={"password": PASSWORD})
    assert risposta.status_code == 200
    client.headers.update({"X-Token": risposta.json()["token"]})
    return client


def test_salute(client):
    corpo = client.get("/api/health").json()
    assert corpo["ok"] is True


def test_la_pagina_si_apre(client):
    risposta = client.get("/")
    assert risposta.status_code == 200
    assert "Scarico Pin e Insta" in risposta.text


def test_gli_statici_ci_sono(client):
    for percorso in ("/style.css", "/app.js", "/sw.js", "/manifest.webmanifest", "/icons/icon-180.png"):
        assert client.get(percorso).status_code == 200, percorso


def test_senza_token_le_api_sono_chiuse(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/resolve", json={"url": "https://pin.it/x"}).status_code == 401


def test_password_sbagliata(client):
    assert client.post("/api/login", json={"password": "no"}).status_code == 401
    assert client.post("/api/login", json={}).status_code == 401


def test_login_e_poi_me(loggato):
    corpo = loggato.get("/api/me").json()
    assert corpo["token_scorciatoia"]
    assert "versione" in corpo


def test_la_sessione_non_apre_i_file(loggato):
    sessione = loggato.headers["X-Token"]
    assert loggato.get(f"/api/file/abc?t={sessione}").status_code == 403


def test_biglietto_per_un_altro_file_non_vale(loggato):
    biglietto = make_token(main.settings.secret_key, "file", 60, jid="uno")
    assert loggato.get(f"/api/file/due?t={biglietto}").status_code == 403


def test_biglietto_giusto_ma_lavoro_sparito(loggato):
    biglietto = make_token(main.settings.secret_key, "file", 60, jid="sparito")
    risposta = loggato.get(f"/api/file/sparito?t={biglietto}")
    assert risposta.status_code == 410


def test_link_di_un_altro_sito_rifiutato(loggato):
    risposta = loggato.post("/api/resolve", json={"url": "https://www.youtube.com/watch?v=x"})
    assert risposta.status_code == 400
    assert "Pinterest" in risposta.json()["detail"]


def test_link_mancante(loggato):
    assert loggato.post("/api/resolve", json={"url": "  "}).status_code == 400


def test_scorciatoia_con_token_sbagliato(client):
    risposta = client.get("/api/shortcut", params={"k": "falso", "u": "https://pin.it/x"})
    assert risposta.status_code == 401


def test_scorciatoia_con_token_giusto_ma_link_non_valido(client):
    buono = shortcut_token(main.settings.secret_key)
    risposta = client.get("/api/shortcut", params={"k": buono, "u": "https://youtube.com/x"})
    assert risposta.status_code == 400


def test_troppi_tentativi_bloccano(client):
    for _ in range(main._LOGIN_MAX_TRIES):
        client.post("/api/login", json={"password": "sbagliata"})
    risposta = client.post("/api/login", json={"password": PASSWORD})
    assert risposta.status_code == 429
    main._failed_logins.clear()
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
