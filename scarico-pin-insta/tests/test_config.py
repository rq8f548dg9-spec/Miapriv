import pytest

from server.config import ConfigError, load_settings

BASE = {"APP_PASSWORD": "password-abbastanza-lunga", "SECRET_KEY": "k" * 40}


def test_senza_password_non_parte():
    with pytest.raises(ConfigError):
        load_settings({})


def test_password_corta_rifiutata():
    with pytest.raises(ConfigError):
        load_settings({"APP_PASSWORD": "corta"})


def test_senza_secret_ne_genera_una_temporanea():
    impostazioni = load_settings({"APP_PASSWORD": "password-abbastanza-lunga"})
    assert impostazioni.secret_is_ephemeral
    assert len(impostazioni.secret_key) > 20


def test_numeri_non_validi_fermano_l_avvio():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "SESSION_DAYS": "tanti"})


def test_limite_in_byte():
    impostazioni = load_settings({**BASE, "MAX_FILESIZE_MB": "10"})
    assert impostazioni.max_filesize_bytes == 10 * 1024 * 1024
    assert load_settings({**BASE, "MAX_FILESIZE_MB": "0"}).max_filesize_bytes == 0


def test_cookie_inesistente_ignorato():
    impostazioni = load_settings({**BASE, "COOKIES_FILE": "/non/esiste/cookies.txt"})
    assert impostazioni.cookies_file is None
