"""Il file unico per Hugging Face e' generato: qui si controlla che sia davvero
uguale al progetto. Senza questi test basta ritoccare il sito e dimenticarsi di
rigenerare, e lo Space continua a servire la versione vecchia."""

import ast
import re
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "tools"))

import build_hf  # noqa: E402


@pytest.fixture(scope="module")
def sorgente() -> str:
    return build_hf.costruisci()


def test_il_bundle_e_python_valido(sorgente):
    ast.parse(sorgente)


def test_non_restano_import_fra_moduli(sorgente):
    colpevoli = [r for r in sorgente.splitlines() if re.match(r"\s*from \.", r)]
    assert colpevoli == []


def test_il_future_sta_solo_in_cima(sorgente):
    righe = sorgente.splitlines()
    posizioni = [i for i, r in enumerate(righe) if r.startswith("from __future__")]
    assert len(posizioni) == 1
    # Prima ci possono stare solo commenti, righe vuote e il docstring.
    assert posizioni[0] < 20


def _risorse_del_bundle(sorgente: str) -> dict[str, str]:
    """Legge il dizionario delle pagine senza eseguire il programma.

    Cercarne la fine col testo non funziona: il CSS contiene righe con una
    graffa chiusa da sola, e il taglio cadrebbe a meta'.
    """
    for nodo in ast.parse(sorgente).body:
        bersaglio = getattr(nodo, "target", None)
        if isinstance(nodo, ast.AnnAssign) and getattr(bersaglio, "id", "") == "_RISORSE":
            return ast.literal_eval(nodo.value)
    raise AssertionError("nel bundle non c'e' il dizionario _RISORSE")


def test_le_pagine_finiscono_dentro_identiche(sorgente):
    risorse = _risorse_del_bundle(sorgente)
    assert set(risorse) == set(build_hf.RISORSE)
    for nome, contenuto in risorse.items():
        atteso = (RADICE / "web" / nome).read_text(encoding="utf-8")
        assert contenuto == atteso, f"{nome} nel bundle non corrisponde a web/{nome}"


def test_il_file_su_disco_e_aggiornato(sorgente):
    disco = (RADICE / "hf-space" / "app.py").read_text(encoding="utf-8")
    assert disco == sorgente, (
        "hf-space/app.py e' vecchio: rigeneralo con  python3 tools/build_hf.py"
    )


def test_lo_space_ha_i_file_che_servono():
    cartella = RADICE / "hf-space"
    for nome in ("app.py", "Dockerfile", "README.md", "requirements.txt"):
        assert (cartella / nome).is_file(), nome
    leggimi = (cartella / "README.md").read_text(encoding="utf-8")
    # Senza il blocco iniziale, Hugging Face non sa che Space e'.
    assert leggimi.startswith("---")
    assert "sdk: docker" in leggimi
    assert "app_port: 7860" in leggimi
    # La porta del Dockerfile deve essere la stessa dichiarata nel README.
    assert "7860" in (cartella / "Dockerfile").read_text(encoding="utf-8")


def test_nel_bundle_non_finiscono_segreti():
    testo = (RADICE / "hf-space" / "app.py").read_text(encoding="utf-8")
    for sospetto in ("ACCESS_KEY=", "sessionid", "cookies.txt\n"):
        assert sospetto not in testo
