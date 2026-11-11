"""La funzione per Supabase e' generata: qui si controlla che sia allineata al
sorgente, e si eseguono le sue prove in JavaScript quando Node e' disponibile."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE / "tools"))

import build_supabase  # noqa: E402

GENERATO = RADICE / "supabase" / "functions" / "scarico" / "index.ts"


def test_il_file_generato_e_aggiornato():
    assert GENERATO.is_file(), "manca supabase/functions/scarico/index.ts"
    atteso = build_supabase.costruisci()
    assert GENERATO.read_text(encoding="utf-8") == atteso, (
        "la funzione Supabase e' vecchia: rigenerala con  python3 tools/build_supabase.py"
    )


def test_le_pagine_sono_quelle_del_progetto():
    generato = GENERATO.read_text(encoding="utf-8")
    import json

    for nome in build_supabase.RISORSE:
        contenuto = (RADICE / "web" / nome).read_text(encoding="utf-8")
        assert json.dumps(contenuto) in generato, f"{nome} nel file generato non corrisponde a web/{nome}"


def test_il_prefisso_resta_da_sostituire_a_runtime():
    # Nella versione Supabase il sito vive sotto /functions/v1/scarico:
    # i segnaposti devono arrivare interi fino alla funzione.
    assert "__BASE__" in GENERATO.read_text(encoding="utf-8")


def test_niente_segreti_nel_file_generato():
    testo = GENERATO.read_text(encoding="utf-8")
    for sospetto in ("ACCESS_KEY=", "sessionid=", "sbp_", "-----BEGIN"):
        assert sospetto not in testo


@pytest.mark.skipif(shutil.which("node") is None, reason="Node non e' installato")
def test_le_prove_javascript_passano():
    esito = subprocess.run(  # noqa: S603 - comando fisso, nessun input esterno
        ["node", "--experimental-strip-types", "deno/prova.mjs"],
        cwd=RADICE,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert esito.returncode == 0, esito.stdout + esito.stderr
    assert "prove superate" in esito.stdout
