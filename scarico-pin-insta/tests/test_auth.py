import time

from server.auth import check_password, make_token, read_token, shortcut_token

SEGRETO = "segreto-di-prova"


def test_token_valido_torna_i_claim():
    token = make_token(SEGRETO, "sess", 60, chi="io")
    claims = read_token(SEGRETO, token, "sess")
    assert claims and claims["chi"] == "io"


def test_token_di_un_altro_pubblico_non_vale():
    token = make_token(SEGRETO, "sess", 60)
    assert read_token(SEGRETO, token, "file") is None


def test_token_con_chiave_diversa_non_vale():
    token = make_token(SEGRETO, "sess", 60)
    assert read_token("altro-segreto", token, "sess") is None


def test_token_manomesso_non_vale():
    token = make_token(SEGRETO, "file", 60, jid="aaa")
    corpo, firma = token.split(".")
    falso = make_token(SEGRETO, "file", 60, jid="bbb").split(".")[0] + "." + firma
    assert read_token(SEGRETO, falso, "file") is None


def test_token_scaduto_non_vale():
    token = make_token(SEGRETO, "sess", -1)
    assert read_token(SEGRETO, token, "sess") is None


def test_token_senza_scadenza_resta_valido():
    token = shortcut_token(SEGRETO)
    claims = read_token(SEGRETO, token, "short")
    assert claims is not None and "exp" not in claims


def test_token_spazzatura_non_rompe():
    for spazzatura in ["", "abc", "a.b.c", "....", "x." + "y" * 50]:
        assert read_token(SEGRETO, spazzatura, "sess") is None


def test_confronto_password():
    assert check_password("abc", "abc")
    assert not check_password("abc", "abd")
    assert not check_password("abc", "")
    assert not check_password("abc", None)


def test_il_token_scorciatoia_e_stabile():
    assert shortcut_token(SEGRETO) == shortcut_token(SEGRETO)
    assert shortcut_token(SEGRETO) != shortcut_token("altro")
