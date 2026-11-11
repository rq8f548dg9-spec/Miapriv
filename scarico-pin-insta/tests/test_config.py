import pytest

from server.config import ConfigError, load_settings

CHIAVE = "chiave-abbastanza-lunga-123"


def test_senza_chiave_non_parte():
    with pytest.raises(ConfigError) as errore:
        load_settings({})
    assert "ACCESS_KEY" in str(errore.value)


def test_chiave_corta_rifiutata():
    with pytest.raises(ConfigError):
        load_settings({"ACCESS_KEY": "corta"})


def test_modalita_aperta_permette_di_partire_senza_chiave():
    impostazioni = load_settings({"OPEN_MODE": "1"})
    assert impostazioni.open_mode is True
    assert impostazioni.access_key == ""


def test_la_chiave_di_firma_e_derivata_e_stabile():
    una = load_settings({"ACCESS_KEY": CHIAVE})
    due = load_settings({"ACCESS_KEY": CHIAVE})
    assert una.secret_key == due.secret_key
    # Non deve essere la chiave d'accesso: quella gira dentro il link.
    assert una.secret_key != CHIAVE
    assert load_settings({"ACCESS_KEY": CHIAVE + "x"}).secret_key != una.secret_key


def test_la_chiave_di_firma_si_puo_imporre():
    impostazioni = load_settings({"ACCESS_KEY": CHIAVE, "SECRET_KEY": "s" * 40})
    assert impostazioni.secret_key == "s" * 40


def test_numeri_non_validi_fermano_l_avvio():
    with pytest.raises(ConfigError):
        load_settings({"ACCESS_KEY": CHIAVE, "MAX_FILESIZE_MB": "tanti"})


def test_limite_in_byte():
    assert load_settings({"ACCESS_KEY": CHIAVE, "MAX_FILESIZE_MB": "10"}).max_filesize_bytes == 10 * 1024 * 1024
    assert load_settings({"ACCESS_KEY": CHIAVE, "MAX_FILESIZE_MB": "0"}).max_filesize_bytes == 0


def test_cookie_dal_testo_di_una_variabile(tmp_path):
    contenuto = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tXYZ"
    impostazioni = load_settings({"ACCESS_KEY": CHIAVE, "IG_COOKIES": contenuto})
    assert impostazioni.cookies_file is not None
    assert impostazioni.cookies_file.read_text(encoding="utf-8").startswith("# Netscape")


def test_cookie_con_a_capo_scritti_a_due_caratteri():
    impostazioni = load_settings({"ACCESS_KEY": CHIAVE, "IG_COOKIES": "riga1\\nriga2"})
    assert impostazioni.cookies_file.read_text(encoding="utf-8").splitlines() == ["riga1", "riga2"]


def test_cookie_inesistente_ignorato():
    assert load_settings({"ACCESS_KEY": CHIAVE, "COOKIES_FILE": "/non/esiste.txt"}).cookies_file is None
