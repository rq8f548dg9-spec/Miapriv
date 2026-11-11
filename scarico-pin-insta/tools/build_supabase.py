#!/usr/bin/env python3
"""Costruisce la funzione da pubblicare su Supabase.

Supabase esegue un file solo, e il sito deve viaggiare dentro di esso: le
pagine come stringhe, le icone in base64. Il sorgente leggibile sta in
deno/scarico.ts e in web/: questo script li mette insieme.

Uso:  python3 tools/build_supabase.py
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
USCITA = RADICE / "supabase" / "functions" / "scarico"

RISORSE = ["index.html", "style.css", "app.js"]
ICONE = ["icon-180.png", "icon-192.png", "icon-512.png"]

INTESTAZIONE = """// Scarico - funzione per Supabase Edge Functions.
//
// ATTENZIONE: file GENERATO da tools/build_supabase.py. Non modificarlo a
// mano: si cambia deno/scarico.ts e web/, poi si rigenera.

"""


def costruisci() -> str:
    pezzi = [INTESTAZIONE]

    pezzi.append("const RISORSE: Record<string, string> = {\n")
    for nome in RISORSE:
        contenuto = (RADICE / "web" / nome).read_text(encoding="utf-8")
        pezzi.append(f"  {json.dumps(nome)}: {json.dumps(contenuto)},\n")
    pezzi.append("};\n\n")

    pezzi.append("const ICONE: Record<string, string> = {\n")
    for nome in ICONE:
        dati = (RADICE / "web" / "icons" / nome).read_bytes()
        pezzi.append(f"  {json.dumps(nome)}: {json.dumps(base64.b64encode(dati).decode('ascii'))},\n")
    pezzi.append("};\n\n")

    corpo = (RADICE / "deno" / "scarico.ts").read_text(encoding="utf-8")
    corpo = "\n".join(
        riga for riga in corpo.splitlines()
        if not riga.startswith("declare const RISORSE")
        and not riga.startswith("declare const ICONE")
    )
    pezzi.append(corpo)
    return "".join(pezzi).rstrip() + "\n"


def main() -> int:
    USCITA.mkdir(parents=True, exist_ok=True)
    destinazione = USCITA / "index.ts"
    destinazione.write_text(costruisci(), encoding="utf-8")
    print(f"  {destinazione.relative_to(RADICE)}  {destinazione.stat().st_size / 1024:.1f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
