from server.extractor import Item, _is_progressive, build_filename, slugify


def test_nome_file_pulito():
    assert slugify('Pasta: al / forno "2026"') == "Pasta al forno 2026"
    assert slugify("") == "video"
    assert slugify("   ...   ") == "video"
    assert len(slugify("x" * 200)) <= 70


def test_nome_file_tiene_gli_accenti():
    assert slugify("Idée déco salón") == "Idée déco salón"


def test_costruzione_del_nome():
    nome = build_filename("pinterest", "Cucina", "mp4", "abcdefghij")
    assert nome == "pinterest - Cucina - abcdef.mp4"
    assert build_filename("instagram", "x", "MP4?", "abcdefghij").endswith(".mp4")


def test_formato_progressive():
    assert _is_progressive({"url": "https://x/v.mp4", "vcodec": "h264", "acodec": "aac"})
    assert not _is_progressive({"url": "https://x/v.mp4", "vcodec": "h264", "acodec": "none"})
    assert not _is_progressive({"url": "https://x/v.m3u8", "vcodec": "h264", "acodec": "aac", "protocol": "m3u8_native"})
    assert not _is_progressive({"vcodec": "h264", "acodec": "aac"})


def test_il_dizionario_pubblico_non_espone_il_cdn():
    item = Item(
        jid="abc",
        source="pinterest",
        kind="video",
        title="t",
        filename="f.mp4",
        page_url="https://pin.it/x",
        direct_url="https://cdn.segreto/video.mp4",
        http_headers={"Cookie": "roba"},
    )
    pubblico = item.public_dict()
    testo = repr(pubblico)
    assert "cdn.segreto" not in testo
    assert "Cookie" not in testo
    assert pubblico["id"] == "abc"
