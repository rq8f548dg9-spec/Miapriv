"""Prove su dizionari con la forma di quelli che restituisce yt-dlp."""

from server.extractor import _entries_of, _item_from_entry

REEL_PROGRESSIVO = {
    "id": "ABC123",
    "title": "Reel di prova",
    "duration": 31.4,
    "webpage_url": "https://www.instagram.com/reel/ABC123/",
    "thumbnail": "https://cdn.example/thumb.jpg",
    "formats": [
        {"format_id": "basso", "url": "https://cdn.example/360.mp4", "ext": "mp4",
         "vcodec": "h264", "acodec": "aac", "height": 360, "tbr": 400, "filesize": 1_000_000},
        {"format_id": "alto", "url": "https://cdn.example/1080.mp4", "ext": "mp4",
         "vcodec": "h264", "acodec": "aac", "height": 1080, "tbr": 1800, "filesize": 5_000_000},
    ],
}

SOLO_SEPARATI = {
    "id": "XYZ",
    "title": "Storia",
    "webpage_url": "https://www.instagram.com/stories/x/1/",
    "formats": [
        {"format_id": "v", "url": "https://cdn.example/v.m4s", "vcodec": "h264", "acodec": "none", "height": 720},
        {"format_id": "a", "url": "https://cdn.example/a.m4a", "vcodec": "none", "acodec": "aac"},
    ],
}

PIN_FOTOGRAFICO = {
    "id": "999",
    "title": "Cucina in legno",
    "webpage_url": "https://www.pinterest.it/pin/999/",
    "formats": [],
    "thumbnails": [
        {"url": "https://i.pinimg.com/236x/a.jpg", "width": 236},
        {"url": "https://i.pinimg.com/originals/a.jpg", "width": 1200},
    ],
}


def test_sceglie_la_qualita_migliore():
    item = _item_from_entry(REEL_PROGRESSIVO, "instagram", "https://instagram.com/reel/ABC123/", 0)
    assert item.direct_url == "https://cdn.example/1080.mp4"
    assert item.height == 1080
    assert item.needs_merge is False
    assert item.kind == "video"
    assert item.filename.startswith("instagram - Reel di prova - ")
    assert item.duration == 31.4


def test_il_limite_di_dimensione_scarta_il_file():
    import pytest

    from server.extractor import TooBig

    with pytest.raises(TooBig):
        _item_from_entry(REEL_PROGRESSIVO, "instagram", "https://x/", 1_000_000)


def test_flussi_separati_chiedono_la_ricomposizione():
    item = _item_from_entry(SOLO_SEPARATI, "instagram", "https://x/", 0)
    assert item.needs_merge is True
    assert item.direct_url is None
    assert item.ext == "mp4"
    # Per ricomporre serve la pagina, non il flusso.
    assert item.page_url == "https://www.instagram.com/stories/x/1/"


def test_pin_fotografico_scarica_l_immagine_piu_grande():
    item = _item_from_entry(PIN_FOTOGRAFICO, "pinterest", "https://www.pinterest.it/pin/999/", 0)
    assert item.kind == "immagine"
    assert item.direct_url == "https://i.pinimg.com/originals/a.jpg"
    assert item.filename.endswith(".jpg")


def test_niente_formati_e_niente_miniature_da_niente():
    assert _item_from_entry({"id": "x", "formats": []}, "pinterest", "https://x/", 0) is None


def test_i_caroselli_vengono_appiattiti():
    post = {
        "_type": "playlist",
        "entries": [
            REEL_PROGRESSIVO,
            {"_type": "playlist", "entries": [PIN_FOTOGRAFICO]},
            None,
        ],
    }
    pezzi = _entries_of(post)
    assert len(pezzi) == 2
    assert pezzi[0]["id"] == "ABC123"
    assert pezzi[1]["id"] == "999"


def test_un_post_singolo_resta_singolo():
    assert _entries_of(REEL_PROGRESSIVO) == [REEL_PROGRESSIVO]
