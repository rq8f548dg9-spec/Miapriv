#!/usr/bin/env python3
"""Costruisce la versione a file unico da caricare su Hugging Face Spaces.

Perche' esiste: lo Space va riempito dal telefono, e caricare venti file da
Safari e' un supplizio. Questo script impacchetta tutto il server e tutto il
sito in un solo `app.py`, cosi' lo Space ha quattro file in croce.

Il file prodotto e' generato: si modifica il progetto, non il bundle.

Uso:  python3 tools/build_hf.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
USCITA = RADICE / "hf-space"

# L'ordine conta: ogni modulo puo' usare solo quelli che vengono prima.
MODULI = ["auth.py", "config.py", "store.py", "urls.py", "extractor.py", "main.py"]
RISORSE = ["index.html", "style.css", "app.js"]

INTESTAZIONE = '''#!/usr/bin/env python3
"""Scarico Pin e Insta - tutto in un file solo, per Hugging Face Spaces.

ATTENZIONE: questo file e' GENERATO da tools/build_hf.py a partire dal
progetto vero. Non modificarlo a mano: le modifiche si perdono alla prossima
generazione. Si cambia il codice in server/ e in web/, poi si rigenera.
"""

from __future__ import annotations

import tempfile

__version__ = "{versione}"
'''

PONTE_ICONE = '''

def _icone_in_tmp() -> Path:
    """Le icone non viaggiano come file: le disegna il programma all'avvio."""
    return disegna_icone(Path(tempfile.gettempdir()) / "icone-scarico")

'''


def _senza_future(testo: str) -> str:
    return "\n".join(r for r in testo.splitlines() if not r.startswith("from __future__ import"))


def togli_import_relativi(testo: str) -> str:
    """Via gli import fra moduli: nel file unico sono tutti gia' qui dentro."""
    fuori: list[str] = []
    dentro_parentesi = False
    for riga in testo.splitlines():
        if dentro_parentesi:
            if ")" in riga:
                dentro_parentesi = False
            continue
        if riga.startswith("from .") or riga.startswith("from . import"):
            if "(" in riga and ")" not in riga:
                dentro_parentesi = True
            continue
        fuori.append(riga)
    return "\n".join(fuori)


def come_stringa_python(contenuto: str) -> str:
    """Il contenuto di un file come stringa sorgente valida.

    Preferisce la stringa grezza su piu' righe, che resta leggibile; se il testo
    contiene i tre apici o finisce con una barra rovesciata ripiega su repr(),
    che e' brutto ma non si rompe mai.
    """
    if '"""' not in contenuto and not contenuto.endswith("\\"):
        return 'r"""' + contenuto + '"""'
    return repr(contenuto)


def costruisci() -> str:
    versione = re.search(
        r'__version__ = "([^"]+)"', (RADICE / "server" / "__init__.py").read_text(encoding="utf-8")
    ).group(1)

    pezzi = [INTESTAZIONE.format(versione=versione)]

    # 1. il disegnatore di icone
    pezzi.append("\n# " + "-" * 74 + "\n# icone\n# " + "-" * 74 + "\n")
    pezzi.append(_senza_future((RADICE / "tools" / "icone.py").read_text(encoding="utf-8")))
    pezzi.append(PONTE_ICONE)

    # 2. le pagine del sito
    pezzi.append("\n# " + "-" * 74 + "\n# il sito\n# " + "-" * 74 + "\n")
    pezzi.append("_RISORSE: dict[str, str] = {\n")
    for nome in RISORSE:
        contenuto = (RADICE / "web" / nome).read_text(encoding="utf-8")
        pezzi.append(f"    {nome!r}: {come_stringa_python(contenuto)},\n")
    pezzi.append("}\n")

    # 3. il server
    for nome in MODULI:
        testo = (RADICE / "server" / nome).read_text(encoding="utf-8")
        testo = togli_import_relativi(_senza_future(testo))
        if nome == "main.py":
            testo = testo.replace(
                "RISORSE_WEB: dict[str, str] = {}", "RISORSE_WEB: dict[str, str] = _RISORSE"
            )
            testo = testo.replace("GENERATORE_ICONE = None", "GENERATORE_ICONE = _icone_in_tmp")
        pezzi.append(f"\n# {'-' * 74}\n# {nome}\n# {'-' * 74}\n")
        pezzi.append(testo)

    return "".join(pezzi).rstrip() + "\n"


DOCKERFILE = """# Hugging Face Spaces, SDK docker. La porta e' quella dichiarata nel README.
FROM python:3.12-slim

# ffmpeg serve per i video che arrivano con audio e video separati
# (storie, alcuni reel, i pin "idea").
RUN apt-get update \\
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \\
 && rm -rf /var/lib/apt/lists/*

# Su Spaces il processo gira come utente 1000: scrivere altrove non e' permesso.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1

WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=user app.py .

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", \\
     "--proxy-headers", "--forwarded-allow-ips=*"]
"""

LEGGIMI = """---
title: Scarico
emoji: "\N{HEAVY BLACK HEART}"
colorFrom: purple
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
short_description: Salva in galleria i video e le foto di Pinterest e Instagram
---

# Scarico

Sito privato per salvare sull'iPhone i video e le foto di Pinterest e Instagram.

**Prima di usarlo** metti in Settings > Variables and secrets un secret chiamato
`ACCESS_KEY` con dentro una chiave lunga a tua scelta. Senza, lo Space non parte:
e' voluto, altrimenti chiunque trovi l'indirizzo potrebbe usarlo.

Poi apri `https://<il-tuo-space>.hf.space/?k=LA-TUA-CHIAVE` e, da Safari,
Condividi > Aggiungi a Home.

Secret facoltativo: `IG_COOKIES`, il contenuto di un `cookies.txt` esportato da
instagram.com mentre sei loggato. Serve per i contenuti che Instagram non mostra
agli sconosciuti.

Il codice di questo file e' generato: il progetto vero sta altrove, in
`scarico-pin-insta/`.
"""


def main() -> int:
    USCITA.mkdir(parents=True, exist_ok=True)
    (USCITA / "app.py").write_text(costruisci(), encoding="utf-8")
    (USCITA / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (USCITA / "README.md").write_text(LEGGIMI, encoding="utf-8")
    (USCITA / "requirements.txt").write_text(
        (RADICE / "requirements.txt").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for nome in ("app.py", "Dockerfile", "README.md", "requirements.txt"):
        peso = (USCITA / nome).stat().st_size
        print(f"  hf-space/{nome:<18} {peso / 1024:6.1f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
