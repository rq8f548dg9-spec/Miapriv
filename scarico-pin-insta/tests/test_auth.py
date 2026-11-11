from server.auth import check_key, make_token, read_token

SEGRETO = "segreto-di-prova"


def test_token_valido_torna_i_claim():
    token = make_token(SEGRETO, "file", 60, jid="abc")
    claims = read_token(SEGRETO, token, "file")
    assert claims and claims["jid"] == "abc"


def test_token_di_un_altro_pubblico_non_vale():
    token = make_token(SEGRETO, "thumb", 60, jid="abc")
    assert read_token(SEGRETO, token, "file") is None


def test_token_con_chiave_diversa_non_vale():
    token = make_token(SEGRETO, "file", 60)
    assert read_token("altro-segreto", token, "file") is None


def test_token_manomesso_non_vale():
    token = make_token(SEGRETO, "file", 60, jid="aaa")
    firma = token.split(".")[1]
    altro_corpo = make_token(SEGRETO, "file", 60, jid="bbb").split(".")[0]
    assert read_token(SEGRETO, altro_corpo + "." + firma, "file") is None


def test_token_scaduto_non_vale():
    assert read_token(SEGRETO, make_token(SEGRETO, "file", -1), "file") is None


def test_token_senza_scadenza_resta_valido():
    token = make_token(SEGRETO, "short", None, v=1)
    claims = read_token(SEGRETO, token, "short")
    assert claims is not None and "exp" not in claims


def test_token_spazzatura_non_rompe():
    for spazzatura in ["", "abc", "a.b.c", "....", "x." + "y" * 50]:
        assert read_token(SEGRETO, spazzatura, "file") is None


def test_confronto_chiave():
    assert check_key("abc", "abc")
    assert not check_key("abc", "abd")
    assert not check_key("abc", "")
    assert not check_key("abc", None)
