import pytest

from server.urls import (
    INSTAGRAM,
    PINTEREST,
    UnsafeUrl,
    UrlNotSupported,
    assert_public_url,
    classify,
    normalize_shared_text,
)


@pytest.mark.parametrize(
    "host, atteso",
    [
        ("www.pinterest.com", PINTEREST),
        ("pinterest.it", PINTEREST),
        ("it.pinterest.com", PINTEREST),
        ("pin.it", PINTEREST),
        ("pinterest.co.uk", PINTEREST),
        ("www.pinterest.com.au", PINTEREST),
        ("instagr.am", INSTAGRAM),
        ("www.instagram.com", INSTAGRAM),
        ("youtube.com", None),
        ("pinterest.evil.com", None),
        ("instagram.com.attacco.net", None),
        ("mio-pinterest.com", None),
        ("pinterest.com.evil.net", None),
        ("instagram", None),
        (None, None),
    ],
)
def test_riconoscimento_host(host, atteso):
    assert classify(host) is atteso


def test_estrae_il_link_da_un_testo_condiviso():
    testo = "Guarda che roba\nhttps://www.pinterest.it/pin/12345/?utm_source=ig_share&b=1"
    url, sorgente = normalize_shared_text(testo)
    assert url == "https://www.pinterest.it/pin/12345/"
    assert sorgente == PINTEREST


def test_tiene_img_index_e_butta_il_resto():
    url, _ = normalize_shared_text("https://www.instagram.com/p/ABC/?img_index=3&igsh=xyz")
    assert url == "https://www.instagram.com/p/ABC/?img_index=3"


def test_accetta_link_senza_schema():
    url, sorgente = normalize_shared_text("instagram.com/reel/XYZ/")
    assert url == "https://instagram.com/reel/XYZ/"
    assert sorgente == INSTAGRAM


def test_scarta_i_siti_non_previsti():
    with pytest.raises(UrlNotSupported):
        normalize_shared_text("https://www.youtube.com/watch?v=abc")
    with pytest.raises(UrlNotSupported):
        normalize_shared_text("   ")


def test_sceglie_il_primo_link_valido_tra_tanti():
    url, _ = normalize_shared_text("vedi https://example.com/x e https://pin.it/abc")
    assert url == "https://pin.it/abc"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://10.0.0.5/x",
        "http://192.168.1.10:8080/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/x",
        "file:///etc/passwd",
    ],
)
def test_blocca_gli_indirizzi_interni(url):
    with pytest.raises(UnsafeUrl):
        assert_public_url(url)


def test_lascia_passare_un_ip_pubblico():
    assert_public_url("https://1.1.1.1/video.mp4")
