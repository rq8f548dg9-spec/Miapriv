#!/usr/bin/env python3
"""Scarico Pin e Insta - tutto in un file solo, per Hugging Face Spaces.

ATTENZIONE: questo file e' GENERATO da tools/build_hf.py a partire dal
progetto vero. Non modificarlo a mano: le modifiche si perdono alla prossima
generazione. Si cambia il codice in server/ e in web/, poi si rigenera.
"""

from __future__ import annotations

import tempfile

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# icone
# --------------------------------------------------------------------------
"""Disegna le icone dell'app in PNG, senza dipendenze esterne.

Il disegno e' originale: una freccia che scende dentro un vassoio aperto, su
sfondo con sfumatura. Nessun logo o marchio di terzi.

Sta in un modulo a parte perche' lo usano due cose: lo script che genera i file
in web/icons/, e la versione a file unico per Hugging Face, che le disegna
all'avvio invece di portarsi dietro dei PNG.
"""


import struct
import zlib
from pathlib import Path

COLORE_A = (177, 76, 255)
COLORE_B = (74, 31, 150)
GLIFO = (255, 255, 255)

CAMPIONI = 3  # 3x3 campioni per pixel: basta per bordi puliti


def _dentro_rettangolo(x: float, y: float, x0: float, y0: float, x1: float, y1: float) -> bool:
    return x0 <= x <= x1 and y0 <= y <= y1


def _dentro_triangolo(x: float, y: float, punti: tuple) -> bool:
    (ax, ay), (bx, by), (cx, cy) = punti

    def lato(px: float, py: float, qx: float, qy: float) -> float:
        return (x - px) * (qy - py) - (y - py) * (qx - px)

    d1, d2, d3 = lato(ax, ay, bx, by), lato(bx, by, cx, cy), lato(cx, cy, ax, ay)
    return (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0)


def copertura_glifo(x: float, y: float, scala: float) -> bool:
    """x e y in coordinate normalizzate 0..1. `scala` restringe il glifo (versione maskable)."""
    # Riporto le coordinate al centro, rimpicciolendo il disegno se richiesto.
    x = 0.5 + (x - 0.5) / scala
    y = 0.5 + (y - 0.5) / scala
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return False

    # Asta della freccia.
    if _dentro_rettangolo(x, y, 0.452, 0.200, 0.548, 0.520):
        return True
    # Punta della freccia.
    if _dentro_triangolo(x, y, ((0.340, 0.470), (0.660, 0.470), (0.500, 0.690))):
        return True
    # Vassoio: base piu' due spalle verticali.
    if _dentro_rettangolo(x, y, 0.270, 0.755, 0.730, 0.818):
        return True
    if _dentro_rettangolo(x, y, 0.270, 0.640, 0.333, 0.818):
        return True
    if _dentro_rettangolo(x, y, 0.667, 0.640, 0.730, 0.818):
        return True
    return False


def disegna(lato: int, scala_glifo: float = 1.0) -> bytearray:
    pixel = bytearray(lato * lato * 4)
    passo = 1.0 / (CAMPIONI + 1)
    for py in range(lato):
        for px in range(lato):
            # Sfondo: sfumatura lungo la diagonale.
            t = ((px / max(lato - 1, 1)) + (py / max(lato - 1, 1))) / 2.0
            fondo = tuple(round(COLORE_A[i] + (COLORE_B[i] - COLORE_A[i]) * t) for i in range(3))

            colpi = 0
            for sy in range(CAMPIONI):
                for sx in range(CAMPIONI):
                    nx = (px + (sx + 1) * passo) / lato
                    ny = (py + (sy + 1) * passo) / lato
                    if copertura_glifo(nx, ny, scala_glifo):
                        colpi += 1
            alfa = colpi / (CAMPIONI * CAMPIONI)

            base = (py * lato + px) * 4
            for i in range(3):
                pixel[base + i] = round(fondo[i] * (1 - alfa) + GLIFO[i] * alfa)
            pixel[base + 3] = 255
    return pixel


def scrivi_png(percorso: Path, larghezza: int, altezza: int, pixel: bytearray) -> None:
    righe = b"".join(
        b"\x00" + bytes(pixel[y * larghezza * 4 : (y + 1) * larghezza * 4]) for y in range(altezza)
    )

    def pezzo(tipo: bytes, dati: bytes) -> bytes:
        testa = struct.pack(">I", len(dati)) + tipo + dati
        return testa + struct.pack(">I", zlib.crc32(tipo + dati) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += pezzo(b"IHDR", struct.pack(">IIBBBBB", larghezza, altezza, 8, 6, 0, 0, 0))
    png += pezzo(b"IDAT", zlib.compress(righe, 9))
    png += pezzo(b"IEND", b"")
    percorso.write_bytes(png)


MISURE = [
    ("icon-180.png", 180, 1.0),
    ("icon-192.png", 192, 1.0),
    ("icon-512.png", 512, 1.0),
    # Nelle icone maskable Android ritaglia fino al 20% per lato: il glifo
    # va tenuto dentro il cerchio di sicurezza.
    ("icon-maskable-512.png", 512, 0.72),
]


def disegna_icone(cartella: Path, parlante: bool = False) -> Path:
    """Scrive le quattro icone nella cartella indicata e la restituisce."""
    cartella.mkdir(parents=True, exist_ok=True)
    for nome, lato, scala in MISURE:
        destinazione = cartella / nome
        if destinazione.exists() and destinazione.stat().st_size > 0:
            continue
        scrivi_png(destinazione, lato, lato, disegna(lato, scala))
        if parlante:
            print(f"  {nome}  {lato}x{lato}")
    return cartella

def _icone_in_tmp() -> Path:
    """Le icone non viaggiano come file: le disegna il programma all'avvio."""
    return disegna_icone(Path(tempfile.gettempdir()) / "icone-scarico")


# --------------------------------------------------------------------------
# il sito
# --------------------------------------------------------------------------
_RISORSE: dict[str, str] = {
    'index.html': r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<title>Scarico</title>

<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Scarico">
<meta name="theme-color" content="#0c0a12" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#f7f4fb" media="(prefers-color-scheme: light)">
<link id="manifesto" rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icons/icon-180.png">
<link rel="icon" href="/icons/icon-192.png" sizes="192x192" type="image/png">
<link rel="stylesheet" href="/style.css">
</head>
<body>

<div class="aurora" aria-hidden="true"><span class="blob blob-a"></span><span class="blob blob-b"></span></div>

<header class="testata">
  <div class="logo">
    <img src="/icons/icon-192.png" alt="" width="28" height="28">
    <span>Scarico</span>
  </div>
  <span id="spia" class="spia" hidden></span>
</header>

<main class="foglio">

  <!-- ------------------------------------------------ serve il link completo -->
  <section id="vista-chiave" class="scheda avviso" hidden>
    <h1 class="titolo-avviso">Serve il tuo link personale</h1>
    <p>
      Questo indirizzo non ha la chiave. Apri il link completo, quello che finisce
      con <code>?k=…</code>, e da lì premi <strong>Condividi → Aggiungi a Home</strong>:
      l'icona se la ricorda per sempre.
    </p>
  </section>

  <!-- ----------------------------------------------------------- schermata -->
  <div id="vista-app" hidden>

    <section class="eroe">
      <h1>Incolla il link.<br><em>Finisce in galleria.</em></h1>
      <p class="occhiello">Pinterest e Instagram · video, reel, storie e foto</p>
    </section>

    <section class="scheda incolla">
      <form id="form-link">
        <textarea id="campo-link" rows="2" inputmode="url" enterkeyhint="search"
                  autocapitalize="off" autocorrect="off" spellcheck="false"
                  placeholder="Incolla qui il link del post"></textarea>
        <button id="btn-principale" class="bottone primario grande" type="submit">
          <span class="etichetta-bottone">Incolla e trova</span>
        </button>
      </form>
      <p id="stato" class="stato" role="status" aria-live="polite" hidden></p>
    </section>

    <section id="risultati" class="risultati" aria-label="Trovati"></section>

    <section id="guida" class="scheda guida" hidden>
      <h2>Come funziona</h2>
      <ol class="passi">
        <li>
          <span class="numero">1</span>
          <span>Copia il link del post da Pinterest o Instagram.</span>
        </li>
        <li>
          <span class="numero">2</span>
          <span>Torna qui e tocca <strong>Incolla e trova</strong>.</span>
        </li>
        <li>
          <span class="numero">3</span>
          <span>Tocca <strong>Salva in galleria</strong>, poi <strong>Salva video</strong>
                nel foglio che si apre.</span>
        </li>
      </ol>
      <button id="btn-chiudi-guida" class="bottone fantasma piccolo" type="button">Ho capito</button>
    </section>

    <section id="aggiungi-home" class="scheda promemoria" hidden>
      <p><strong>Mettilo sulla schermata Home.</strong> Premi
         <svg class="glifo-condividi" viewBox="0 0 24 24" aria-hidden="true" width="17" height="17">
           <path d="M12 3v12M12 3l-3.2 3.2M12 3l3.2 3.2" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
           <path d="M6 11H4.6v8.4h14.8V11H18" fill="none" stroke="currentColor"
                 stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
         </svg>
         <strong>Condividi</strong> in Safari, poi <strong>Aggiungi a Home</strong>. Si apre come un'app.</p>
      <button id="btn-chiudi-promemoria" class="bottone fantasma piccolo" type="button">Chiudi</button>
    </section>

    <footer class="piede"><span id="info-server"></span></footer>
  </div>
</main>

<div id="toast" class="toast" role="status" aria-live="polite" hidden></div>

<script src="/app.js"></script>
</body>
</html>
""",
    'style.css': r"""/* Scarico - foglio di stile unico, mobile-first.

   Colori: un primario viola (gli accenti, la minoranza dei pixel), una scala di
   grigi tinta di viola (quasi tutta la superficie) e tre colori semantici usati
   solo per dire com'e' andata. Il rosa compare solo dentro il gradiente del
   bottone principale, per dare un punto caldo in una pagina fredda. */

:root {
  color-scheme: dark;

  /* primario */
  --p-300: #d8b4fe;
  --p-400: #c084fc;
  --p-500: #a855f7;
  --p-600: #9333ea;
  --rosa-600: #db2777;

  /* neutri tinti di viola */
  --n-950: #0c0a12;
  --n-900: #141019;
  --n-850: #1a1522;
  --n-800: #221b2d;
  --n-700: #312841;
  --n-500: #6f6683;
  --n-400: #9b91ad;
  --n-200: #d9d3e3;
  --n-50:  #f7f4fb;

  --fondo: var(--n-950);
  --superficie: rgba(255, 255, 255, 0.045);
  --superficie-alta: rgba(255, 255, 255, 0.075);
  --bordo: rgba(255, 255, 255, 0.10);
  --bordo-forte: rgba(255, 255, 255, 0.18);
  --testo: var(--n-50);
  --testo-tenue: var(--n-400);

  /* semantici: verde fatto, rosso guaio, ambra attenzione */
  --ok-fondo: rgba(34, 197, 94, 0.16);
  --ok-testo: #6ee7a0;
  --no-fondo: rgba(244, 63, 94, 0.16);
  --no-testo: #fb8da0;
  --forse-fondo: rgba(245, 158, 11, 0.16);
  --forse-testo: #fbbf4a;

  --r-grande: 24px;
  --r: 18px;
  --r-piccolo: 12px;
  --gutter: 18px;
  --ombra: 0 18px 40px -12px rgba(0, 0, 0, 0.6);
}

@media (prefers-color-scheme: light) {
  :root {
    color-scheme: light;
    --fondo: var(--n-50);
    --superficie: #ffffff;
    --superficie-alta: #ffffff;
    --bordo: #e6dff1;
    --bordo-forte: #d3c8e5;
    --testo: #17121f;
    --testo-tenue: #5d5570;
    --ok-fondo: rgba(22, 163, 74, 0.12);
    --ok-testo: #15803d;
    --no-fondo: rgba(225, 29, 72, 0.10);
    --no-testo: #be123c;
    --forse-fondo: rgba(217, 119, 6, 0.12);
    --forse-testo: #b45309;
    --ombra: 0 16px 36px -14px rgba(60, 30, 90, 0.22);
  }
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }

body {
  margin: 0;
  min-height: 100svh;
  background: var(--fondo);
  color: var(--testo);
  font: 17px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  overflow-wrap: break-word;
  -webkit-tap-highlight-color: transparent;
}

[hidden] { display: none !important; }

/* --------------------------------------------------------------- sfondo */

.aurora { position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 0; }
.blob { position: absolute; border-radius: 50%; filter: blur(90px); opacity: 0.55; }
.blob-a {
  width: 420px; height: 420px; top: -170px; right: -140px;
  background: radial-gradient(circle, var(--p-500), transparent 68%);
}
.blob-b {
  width: 380px; height: 380px; top: 180px; left: -180px;
  background: radial-gradient(circle, var(--rosa-600), transparent 68%);
  opacity: 0.38;
}
@media (prefers-color-scheme: light) {
  .blob-a { opacity: 0.30; }
  .blob-b { opacity: 0.22; }
}

/* -------------------------------------------------------------- testata */

.testata {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: calc(14px + env(safe-area-inset-top)) var(--gutter) 6px;
  max-width: 560px;
  margin: 0 auto;
}
.logo { display: flex; align-items: center; gap: 9px; font-weight: 650; letter-spacing: -0.01em; }
.logo img { border-radius: 8px; }
.spia { font-size: 0.74rem; color: var(--testo-tenue); }

.foglio {
  position: relative;
  z-index: 1;
  max-width: 560px;
  margin: 0 auto;
  padding: 10px var(--gutter) calc(48px + env(safe-area-inset-bottom));
  display: grid;
  gap: 16px;
}

/* ----------------------------------------------------------------- eroe */

.eroe { padding: 18px 2px 2px; }
.eroe h1 {
  margin: 0;
  font-size: clamp(2rem, 9vw, 2.6rem);
  line-height: 1.08;
  letter-spacing: -0.035em;
  font-weight: 700;
}
.eroe em {
  font-style: normal;
  background: linear-gradient(100deg, var(--p-400), var(--rosa-600));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
/* L'occhiello sta sotto e piu' piccolo del titolo: non deve competerci. */
.occhiello { margin: 12px 0 0; color: var(--testo-tenue); font-size: 0.95rem; }

/* --------------------------------------------------------------- schede */

.scheda {
  background: var(--superficie);
  border: 1px solid var(--bordo);
  border-radius: var(--r-grande);
  padding: 16px;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}

.incolla form { display: grid; gap: 12px; }

textarea {
  width: 100%;
  font: inherit;
  font-size: 1rem;              /* sotto i 16px iOS ingrandisce la pagina da solo */
  color: var(--testo);
  background: var(--superficie-alta);
  border: 1px solid var(--bordo);
  border-radius: var(--r);
  padding: 15px 16px;
  resize: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
textarea::placeholder { color: var(--n-500); }
textarea:focus {
  outline: none;
  border-color: var(--p-500);
  box-shadow: 0 0 0 4px rgba(168, 85, 247, 0.18);
}

/* -------------------------------------------------------------- bottoni */

.bottone {
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font: inherit;
  font-weight: 650;
  text-align: center;
  text-decoration: none;
  border: 1px solid transparent;
  border-radius: var(--r);
  padding: 15px 18px;
  min-height: 52px;
  cursor: pointer;
  transition: transform 0.08s ease, opacity 0.15s ease, background-color 0.15s ease;
}
.bottone:active { transform: scale(0.985); }
.bottone[disabled] { opacity: 0.6; cursor: default; }
.grande { min-height: 56px; font-size: 1.05rem; }

.primario {
  /* Bianco su questi due viola/rosa supera 4.5:1: leggibile anche al sole. */
  background: linear-gradient(100deg, var(--p-600), var(--rosa-600));
  color: #ffffff;
  box-shadow: 0 10px 26px -10px rgba(147, 51, 234, 0.75);
}
.secondario { background: var(--superficie-alta); color: var(--testo); border-color: var(--bordo-forte); }
.fantasma { background: transparent; color: var(--testo-tenue); border-color: var(--bordo); min-height: 46px; }
.piccolo { min-height: 40px; padding: 9px 14px; font-size: 0.88rem; border-radius: var(--r-piccolo); }

.etichetta-bottone { position: relative; z-index: 1; }

/* Barra di avanzamento dentro al bottone: il progresso si legge dove si tocca. */
.riempimento {
  position: absolute;
  inset: 0 auto 0 0;
  width: 0;
  background: rgba(255, 255, 255, 0.26);
  transition: width 0.18s ease;
  z-index: 0;
}
.bottone.pronto {
  background: linear-gradient(100deg, #16a34a, #0f9d6b);
  box-shadow: 0 10px 26px -10px rgba(22, 163, 74, 0.7);
}

/* ------------------------------------------------------------- messaggi */

.stato {
  margin: 12px 0 0;
  font-size: 0.92rem;
  padding: 12px 14px;
  border-radius: var(--r-piccolo);
  background: var(--superficie-alta);
  border: 1px solid var(--bordo);
  color: var(--testo-tenue);
}
.stato.lavorando { border-color: rgba(168, 85, 247, 0.5); color: var(--testo); }
.stato.fatto { background: var(--ok-fondo); border-color: transparent; color: var(--ok-testo); }
.stato.guaio { background: var(--no-fondo); border-color: transparent; color: var(--no-testo); }
.stato.attenzione { background: var(--forse-fondo); border-color: transparent; color: var(--forse-testo); }

.filatoio {
  display: inline-block;
  width: 13px; height: 13px;
  margin-right: 8px;
  vertical-align: -1px;
  border: 2px solid rgba(168, 85, 247, 0.3);
  border-top-color: var(--p-400);
  border-radius: 50%;
  animation: gira 0.7s linear infinite;
}
@keyframes gira { to { transform: rotate(360deg); } }

/* ----------------------------------------------------------- risultati */

.risultati { display: grid; gap: 16px; }

.risultato {
  background: var(--superficie);
  border: 1px solid var(--bordo);
  border-radius: var(--r-grande);
  overflow: hidden;
  box-shadow: var(--ombra);
  animation: entra 0.28s ease both;
}
@keyframes entra { from { opacity: 0; transform: translateY(10px); } }

.cornice {
  position: relative;
  display: block;
  width: 100%;
  aspect-ratio: 4 / 5;
  background: var(--superficie-alta);
}
.cornice img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cornice .sfumatura {
  position: absolute; inset: 0;
  background: linear-gradient(to top, rgba(8, 6, 12, 0.88) 0%, rgba(8, 6, 12, 0) 52%);
}
.bollo {
  position: absolute;
  top: 12px; left: 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 11px;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 650;
  color: #fff;
  background: rgba(10, 8, 16, 0.6);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.16);
}
.didascalia { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px; color: #fff; }
.didascalia h3 {
  margin: 0 0 6px;
  font-size: 1.02rem;
  line-height: 1.28;
  font-weight: 650;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-shadow: 0 1px 10px rgba(0, 0, 0, 0.5);
}
.didascalia .meta { margin: 0; font-size: 0.82rem; color: rgba(255, 255, 255, 0.82); }
.meta .punto { opacity: 0.55; margin: 0 6px; }

.azioni { display: grid; gap: 9px; padding: 14px; }

/* ----------------------------------------------------- guida e avvisi */

.guida h2 { margin: 0 0 14px; font-size: 1.05rem; }
.passi { list-style: none; margin: 0 0 14px; padding: 0; display: grid; gap: 12px; font-size: 0.95rem; }
/* Due celle e basta: il numero e il testo. Se il testo del passo finisse
   spezzato in piu' figli, ogni pezzo diventerebbe una cella a se' e andrebbe
   a capo in colonna. */
.passi li { display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 11px; align-items: start; }
.numero {
  display: grid;
  place-items: center;
  flex: none;
  width: 26px; height: 26px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--p-600), var(--rosa-600));
  color: #fff;
  font-size: 0.8rem;
  font-weight: 700;
}
.avviso { border-color: rgba(245, 158, 11, 0.45); }
.titolo-avviso { font-size: 1.2rem; margin: 0 0 10px; }
.avviso code {
  background: var(--superficie-alta);
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 0.88em;
}
.promemoria { display: grid; gap: 10px; }
.promemoria p { margin: 0; font-size: 0.93rem; color: var(--testo-tenue); }
.promemoria .bottone { justify-self: start; }
.glifo-condividi { vertical-align: -3px; color: var(--p-400); }
.guida .bottone { justify-self: start; }

.piede { text-align: center; color: var(--n-500); font-size: 0.74rem; }

/* ---------------------------------------------------------------- toast */

.toast {
  position: fixed;
  left: 50%;
  bottom: calc(22px + env(safe-area-inset-bottom));
  transform: translate(-50%, 16px);
  z-index: 50;
  max-width: min(440px, calc(100vw - 32px));
  padding: 13px 18px;
  border-radius: 999px;
  font-size: 0.92rem;
  font-weight: 550;
  text-align: center;
  color: #fff;
  background: rgba(18, 14, 26, 0.94);
  border: 1px solid var(--bordo-forte);
  box-shadow: var(--ombra);
  backdrop-filter: blur(14px);
  opacity: 0;
  transition: opacity 0.22s ease, transform 0.22s ease;
}
.toast.visibile { opacity: 1; transform: translate(-50%, 0); }
.toast.ok { background: #15803d; border-color: transparent; }
.toast.no { background: #be123c; border-color: transparent; }

@media (prefers-reduced-motion: reduce) {
  .filatoio { animation-duration: 2.4s; }
  .risultato { animation: none; }
  .bottone:active { transform: none; }
  .toast, .riempimento { transition: none; }
}
""",
    'app.js': r"""/* Scarico - logica del sito. Nessuna libreria.

   Il punto delicato di tutta l'app e' il salvataggio in galleria.
   Su iOS nessuna pagina web puo' scrivere in Foto da sola: l'unica via e'
   il foglio di condivisione (navigator.share con un file), dove compare
   «Salva video» o «Salva immagine». Quindi la pagina scarica il file in
   memoria e poi lo passa al foglio. */
'use strict';

const CHIAVE_SALVATA = 'scarico.k';
const GUIDA_VISTA = 'scarico.guida';
const PROMEMORIA_VISTO = 'scarico.home';
const LIMITE_MEMORIA = 400 * 1024 * 1024; // oltre, il telefono rischia di non farcela

const $ = (sel) => document.querySelector(sel);

const el = {
  vistaChiave: $('#vista-chiave'),
  vistaApp: $('#vista-app'),
  form: $('#form-link'),
  campo: $('#campo-link'),
  btnPrincipale: $('#btn-principale'),
  stato: $('#stato'),
  risultati: $('#risultati'),
  guida: $('#guida'),
  btnChiudiGuida: $('#btn-chiudi-guida'),
  promemoria: $('#aggiungi-home'),
  btnChiudiPromemoria: $('#btn-chiudi-promemoria'),
  infoServer: $('#info-server'),
  spia: $('#spia'),
  toast: $('#toast'),
  manifesto: $('#manifesto'),
};

/* ----------------------------------------------------------- memoria locale */

function leggi(chiave, difetto = null) {
  try {
    const grezzo = localStorage.getItem(chiave);
    return grezzo === null ? difetto : grezzo;
  } catch (_) {
    return difetto;
  }
}
function scrivi(chiave, valore) {
  try { localStorage.setItem(chiave, valore); } catch (_) {}
}

/* -------------------------------------------------------- chiave d'accesso */

function trovaChiave() {
  const daUrl = new URLSearchParams(location.search).get('k');
  if (daUrl) {
    scrivi(CHIAVE_SALVATA, daUrl);
    return daUrl;
  }
  /* In Safari e nell'app aggiunta alla Home lo spazio di memoria e' separato,
     per questo la chiave deve restare dentro l'indirizzo: qui il salvataggio
     locale e' solo una rete di sicurezza. */
  return leggi(CHIAVE_SALVATA, '');
}

const chiave = trovaChiave();

/* Il manifest deve portarsi dietro la chiave, altrimenti l'icona sulla Home
   rischia di aprire l'indirizzo nudo e di non entrare piu'. */
if (chiave && el.manifesto) {
  el.manifesto.href = '/manifest.webmanifest?k=' + encodeURIComponent(chiave);
}

async function chiama(percorso, opzioni = {}) {
  const intestazioni = Object.assign({ 'Content-Type': 'application/json' }, opzioni.headers || {});
  if (chiave) intestazioni['X-Chiave'] = chiave;
  const risposta = await fetch(percorso, Object.assign({}, opzioni, { headers: intestazioni }));
  let dati = null;
  try { dati = await risposta.json(); } catch (_) {}
  if (!risposta.ok) {
    const errore = new Error((dati && dati.detail) || 'Errore ' + risposta.status + '.');
    errore.stato = risposta.status;
    throw errore;
  }
  return dati;
}

/* ------------------------------------------------------------------ aiuti */

function toast(testo, tipo = '') {
  el.toast.textContent = testo;
  el.toast.className = 'toast visibile' + (tipo ? ' ' + tipo : '');
  el.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.toast.classList.remove('visibile');
    setTimeout(() => { el.toast.hidden = true; }, 250);
  }, 3600);
}

function mostraStato(testo, classe = '', conFilatoio = false) {
  el.stato.className = 'stato' + (classe ? ' ' + classe : '');
  el.stato.innerHTML = '';
  if (conFilatoio) {
    const s = document.createElement('span');
    s.className = 'filatoio';
    el.stato.appendChild(s);
  }
  el.stato.appendChild(document.createTextNode(testo));
  el.stato.hidden = false;
}
function nascondiStato() { el.stato.hidden = true; }

function peso(byte) {
  if (!byte) return null;
  if (byte < 1048576) return Math.max(1, Math.round(byte / 1024)) + ' kB';
  const mb = byte / 1048576;
  if (mb >= 1000) return (mb / 1024).toFixed(1) + ' GB';
  return mb.toFixed(mb < 10 ? 1 : 0) + ' MB';
}
function durata(secondi) {
  if (!secondi) return null;
  return Math.floor(secondi / 60) + ':' + String(Math.round(secondi % 60)).padStart(2, '0');
}

/* ------------------------------------------------------------ avvio vista */

async function avvia() {
  if (!chiave) {
    el.vistaChiave.hidden = false;
    return;
  }
  try {
    const info = await chiama('/api/me');
    el.vistaApp.hidden = false;
    const pezzi = ['v' + info.versione];
    if (!info.ffmpeg) pezzi.push('senza ffmpeg');
    if (!info.cookie_instagram) pezzi.push('senza cookie Instagram');
    el.infoServer.textContent = pezzi.join(' · ');
    if (!leggi(GUIDA_VISTA)) el.guida.hidden = false;
    mostraPromemoriaHome();
    prendiLinkDallaUrl();
  } catch (errore) {
    if (errore.stato === 401) {
      el.vistaChiave.hidden = false;
      return;
    }
    el.vistaApp.hidden = false;
    toast(errore.message, 'no');
  }
}

function inApp() {
  return window.navigator.standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches;
}

function mostraPromemoriaHome() {
  if (inApp() || leggi(PROMEMORIA_VISTO)) return;
  const iOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  if (!iOS) return;
  el.promemoria.hidden = false;
}

el.btnChiudiGuida.addEventListener('click', () => {
  el.guida.hidden = true;
  scrivi(GUIDA_VISTA, '1');
});
el.btnChiudiPromemoria.addEventListener('click', () => {
  el.promemoria.hidden = true;
  scrivi(PROMEMORIA_VISTO, '1');
});

/* --------------------------------------------------- incolla, cerca, mostra */

el.campo.addEventListener('input', () => {
  const pieno = el.campo.value.trim().length > 0;
  el.btnPrincipale.querySelector('.etichetta-bottone').textContent = pieno ? 'Trova' : 'Incolla e trova';
});

/* Un bottone solo: se il campo e' vuoto legge gli appunti, se c'e' gia' un
   link cerca quello. Due bottoni che fanno la stessa cosa confondono. */
el.form.addEventListener('submit', async (evento) => {
  evento.preventDefault();
  if (el.campo.value.trim()) {
    cerca(el.campo.value.trim());
    return;
  }
  try {
    const testo = await navigator.clipboard.readText();
    if (!testo || !testo.trim()) {
      toast('Negli appunti non c’è niente.', 'no');
      el.campo.focus();
      return;
    }
    el.campo.value = testo.trim();
    cerca(testo.trim());
  } catch (_) {
    /* iOS chiede il permesso e si puo' dire di no: resta l'incolla a mano. */
    el.campo.focus();
    toast('Tieni premuto nel campo e scegli «Incolla».');
  }
});

async function cerca(testo) {
  el.btnPrincipale.disabled = true;
  el.risultati.innerHTML = '';
  memoria.clear();
  mostraStato('Sto cercando…', 'lavorando', true);
  try {
    const dati = await chiama('/api/resolve', { method: 'POST', body: JSON.stringify({ url: testo }) });
    const elementi = dati.elementi || [];
    if (!elementi.length) {
      mostraStato('Non ho trovato niente da scaricare.', 'guaio');
      return;
    }
    nascondiStato();
    elementi.forEach((elemento) => el.risultati.appendChild(creaScheda(elemento)));
    if (elementi.length > 1) {
      mostraStato('Questo post ha ' + elementi.length + ' elementi.', 'fatto');
    }
    el.risultati.firstElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (errore) {
    mostraStato(errore.message, errore.stato === 403 ? 'attenzione' : 'guaio');
  } finally {
    el.btnPrincipale.disabled = false;
  }
}

function creaScheda(elemento) {
  const scheda = document.createElement('article');
  scheda.className = 'risultato';

  const cornice = document.createElement('div');
  cornice.className = 'cornice';

  const anteprima = document.createElement('img');
  anteprima.alt = '';
  anteprima.decoding = 'async';
  if (elemento.url_thumb) anteprima.src = elemento.url_thumb;
  cornice.appendChild(anteprima);

  const sfumatura = document.createElement('div');
  sfumatura.className = 'sfumatura';
  cornice.appendChild(sfumatura);

  const bollo = document.createElement('span');
  bollo.className = 'bollo';
  bollo.textContent = (elemento.source === 'pinterest' ? 'Pinterest' : 'Instagram') +
    ' · ' + (elemento.kind === 'video' ? 'video' : 'foto');
  cornice.appendChild(bollo);

  const didascalia = document.createElement('div');
  didascalia.className = 'didascalia';
  const titolo = document.createElement('h3');
  titolo.textContent = elemento.title || 'Senza titolo';
  didascalia.appendChild(titolo);

  const pezzi = [];
  const d = durata(elemento.duration);
  if (d) pezzi.push(d);
  const p = peso(elemento.filesize);
  if (p) pezzi.push(p);
  if (elemento.height) pezzi.push(elemento.height + 'p');
  if (elemento.merge) pezzi.push('da comporre');
  if (pezzi.length) {
    const meta = document.createElement('p');
    meta.className = 'meta';
    pezzi.forEach((valore, indice) => {
      if (indice) {
        const punto = document.createElement('span');
        punto.className = 'punto';
        punto.textContent = '\u00b7';
        meta.appendChild(punto);
      }
      const pezzo = document.createElement('span');
      pezzo.textContent = valore;
      meta.appendChild(pezzo);
    });
    didascalia.appendChild(meta);
  }
  cornice.appendChild(didascalia);
  scheda.appendChild(cornice);

  const azioni = document.createElement('div');
  azioni.className = 'azioni';

  const salva = document.createElement('button');
  salva.className = 'bottone primario grande';
  salva.type = 'button';
  const riempimento = document.createElement('span');
  riempimento.className = 'riempimento';
  const etichetta = document.createElement('span');
  etichetta.className = 'etichetta-bottone';
  etichetta.textContent = 'Salva in galleria';
  salva.appendChild(riempimento);
  salva.appendChild(etichetta);
  salva.addEventListener('click', () => salvaInGalleria(elemento, salva));
  azioni.appendChild(salva);

  const neiFile = document.createElement('a');
  neiFile.className = 'bottone secondario';
  neiFile.href = elemento.url_file;
  neiFile.setAttribute('download', elemento.filename || '');
  neiFile.textContent = 'Salva nei File';
  neiFile.addEventListener('click', () => toast('Lo trovi nei Download di Safari.'));
  azioni.appendChild(neiFile);

  scheda.appendChild(azioni);
  return scheda;
}

/* ------------------------------------------------- salvataggio in galleria */

const memoria = new Map(); // id -> File gia' scaricato

function testo(bottone, valore) {
  bottone.querySelector('.etichetta-bottone').textContent = valore;
}
function avanzamento(bottone, frazione) {
  bottone.querySelector('.riempimento').style.width = Math.round(frazione * 100) + '%';
}

async function scaricaInMemoria(elemento, bottone) {
  const risposta = await fetch(elemento.url_file + '&apri=1', {
    headers: chiave ? { 'X-Chiave': chiave } : {},
  });
  if (!risposta.ok) {
    let dettaglio = 'Errore ' + risposta.status + '.';
    try { dettaglio = (await risposta.json()).detail || dettaglio; } catch (_) {}
    throw new Error(dettaglio);
  }

  const totale = Number(risposta.headers.get('content-length')) || 0;
  if (totale > LIMITE_MEMORIA) throw new Error('Questo file e’ troppo grande per la galleria: usa «Salva nei File».');

  let blob;
  if (risposta.body && risposta.body.getReader) {
    const lettore = risposta.body.getReader();
    const pezzi = [];
    let presi = 0;
    for (;;) {
      const { done, value } = await lettore.read();
      if (done) break;
      pezzi.push(value);
      presi += value.length;
      if (totale) avanzamento(bottone, presi / totale);
      testo(bottone, totale ? 'Scarico ' + Math.round((presi / totale) * 100) + '%' : 'Scarico…');
    }
    blob = new Blob(pezzi, { type: elemento.mime });
  } else {
    blob = await risposta.blob();
  }
  avanzamento(bottone, 1);
  return new File([blob], elemento.filename, { type: elemento.mime });
}

function scaricaNeiFile(elemento) {
  const ancora = document.createElement('a');
  ancora.href = elemento.url_file;
  ancora.download = elemento.filename || '';
  document.body.appendChild(ancora);
  ancora.click();
  ancora.remove();
}

async function salvaInGalleria(elemento, bottone) {
  if (bottone.dataset.occupato === '1') return;
  bottone.dataset.occupato = '1';
  const etichettaIniziale = bottone.querySelector('.etichetta-bottone').textContent;
  try {
    let file = memoria.get(elemento.id);
    if (!file) {
      testo(bottone, 'Preparo…');
      file = await scaricaInMemoria(elemento, bottone);
      memoria.set(elemento.id, file);
    }

    if (!(navigator.canShare && navigator.canShare({ files: [file] }))) {
      /* Browser senza condivisione di file (o desktop): meglio il download
         normale che un messaggio di errore. */
      scaricaNeiFile(elemento);
      testo(bottone, 'Scaricato');
      toast('Questo browser non sa passare i file alla galleria: l’ho scaricato.', '');
      return;
    }

    testo(bottone, 'Apro…');
    await navigator.share({ files: [file] });
    bottone.classList.remove('pronto');
    testo(bottone, 'Salvato');
    avanzamento(bottone, 0);
    toast(elemento.kind === 'video' ? 'Scegli «Salva video»' : 'Scegli «Salva immagine»', 'ok');
  } catch (errore) {
    avanzamento(bottone, 0);
    if (errore.name === 'AbortError') {
      testo(bottone, etichettaIniziale);
      return;
    }
    if (errore.name === 'NotAllowedError') {
      /* iOS pretende che il foglio parta da un tocco: dopo un download lungo
         l'autorizzazione scade. Il file pero' e' gia' in memoria, quindi il
         prossimo tocco lo apre all'istante. */
      bottone.classList.add('pronto');
      testo(bottone, 'Tocca per salvare');
      return;
    }
    testo(bottone, 'Riprova');
    toast(errore.message, 'no');
  } finally {
    bottone.dataset.occupato = '0';
  }
}

/* ------------------------------------------ link passato nell'indirizzo */

function prendiLinkDallaUrl() {
  const link = new URLSearchParams(location.search).get('u');
  if (!link) return;
  el.campo.value = link;
  /* Tolgo solo "u" e tengo "k": senza la chiave il prossimo avvio non entra. */
  const parametri = new URLSearchParams(location.search);
  parametri.delete('u');
  const coda = parametri.toString();
  history.replaceState(null, '', location.pathname + (coda ? '?' + coda : ''));
  cerca(link);
}

/* ------------------------------------------------------------------ avvio */

avvia();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
""",
    'sw.js': r"""/* Service worker: tiene in cache solo il guscio del sito.
   Le API e i file video non vengono MAI messi in cache: sono privati,
   pesanti e i biglietti di download scadono. */
'use strict';

const CACHE = 'scarico-v2';
const GUSCIO = [
  '/',
  '/style.css',
  '/app.js',
  '/icons/icon-180.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(GUSCIO)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((nomi) => Promise.all(nomi.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evento) => {
  const richiesta = evento.request;
  const url = new URL(richiesta.url);

  // Fuori dal nostro sito, non GET, o rotte dinamiche: lascio passare.
  if (richiesta.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  // Il manifest cambia con la chiave: in cache diventerebbe quello sbagliato.
  if (url.pathname === '/manifest.webmanifest') return;

  // Navigazione: prima la rete, la cache solo se la rete non c'e'.
  if (richiesta.mode === 'navigate') {
    evento.respondWith(fetch(richiesta).catch(() => caches.match('/')));
    return;
  }

  // Guscio: prima la cache, e intanto si aggiorna in silenzio.
  evento.respondWith(
    caches.match(richiesta).then((salvata) => {
      const dallaRete = fetch(richiesta)
        .then((risposta) => {
          if (risposta && risposta.ok && risposta.type === 'basic') {
            const copia = risposta.clone();
            caches.open(CACHE).then((cache) => cache.put(richiesta, copia));
          }
          return risposta;
        })
        .catch(() => salvata);
      return salvata || dallaRete;
    })
  );
});
""",
}

# --------------------------------------------------------------------------
# auth.py
# --------------------------------------------------------------------------
"""Token firmati con HMAC-SHA256.

Due tipi di token, distinti dal campo "aud" perche' un token di sessione non
deve poter essere usato come biglietto di download e viceversa:

- "file"  : biglietto per un singolo download, viaggia nella query string
            (serve perche' su iOS il download puo' partire da una navigazione,
            e una navigazione non puo' portare intestazioni su misura).
- "thumb" : biglietto per la miniatura.
- "short" : token per le Scorciatoie iOS, senza scadenza.
"""


import base64
import hashlib
import hmac
import json
import time

_SEP = "."


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return _b64e(digest)


def make_token(secret: str, aud: str, ttl_seconds: int | None, **claims: object) -> str:
    """Crea un token firmato. ttl_seconds None = nessuna scadenza."""
    payload: dict[str, object] = {"aud": aud, **claims}
    if ttl_seconds is not None:
        payload["exp"] = int(time.time()) + int(ttl_seconds)
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + _SEP + _sign(secret, body)


def read_token(secret: str, token: str, aud: str) -> dict[str, object] | None:
    """Verifica firma, pubblico e scadenza. Ritorna i claim o None."""
    if not token or token.count(_SEP) != 1:
        return None
    body, signature = token.split(_SEP, 1)
    expected = _sign(secret, body)
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("aud") != aud:
        return None
    exp = payload.get("exp")
    if exp is not None:
        if not isinstance(exp, int) or exp < time.time():
            return None
    return payload


def check_key(expected: str, given: str | None) -> bool:
    """Confronto a tempo costante: la chiave non si indovina a tentativi misurati."""
    return hmac.compare_digest(expected.encode("utf-8"), (given or "").encode("utf-8"))


def shortcut_token(secret: str) -> str:
    """Token stabile per le Scorciatoie iOS: cambia solo se cambia SECRET_KEY."""
    return make_token(secret, "short", None, v=1)
# --------------------------------------------------------------------------
# config.py
# --------------------------------------------------------------------------
"""Configurazione letta dalle variabili d'ambiente.

Un solo segreto da impostare: ACCESS_KEY, la chiave che sta dentro il link.
Tutto il resto ha un valore sensato di suo, perche' questo sito deve poter
essere acceso da un telefono in tre tocchi.
"""


import hashlib
import hmac
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("scarico.config")


class ConfigError(RuntimeError):
    """Configurazione mancante o non valida: il server non deve partire."""


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} deve essere un numero intero, trovato {raw!r}") from exc


def _bool_env(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "si", "sì", "yes", "on")


@dataclass(frozen=True)
class Settings:
    access_key: str
    secret_key: str
    cookies_file: Path | None
    max_filesize_mb: int
    max_concurrent: int
    tmp_dir: Path
    open_mode: bool

    @property
    def max_filesize_bytes(self) -> int:
        """0 significa nessun limite."""
        return max(self.max_filesize_mb, 0) * 1024 * 1024


def _derive(access_key: str, scopo: str) -> str:
    """Chiave di firma ricavata da quella d'accesso.

    Cosi' c'e' un segreto solo da incollare, ma la chiave che firma i biglietti
    non e' la stessa che gira dentro il link.
    """
    return hmac.new(access_key.encode("utf-8"), scopo.encode("utf-8"), hashlib.sha256).hexdigest()


def _cookies_da_variabile(contenuto: str) -> Path | None:
    """Su Hugging Face i cookie arrivano come testo in un secret, non come file."""
    testo = contenuto.strip()
    if not testo:
        return None
    if "\\n" in testo and "\n" not in testo:
        # Alcuni pannelli salvano gli a capo come due caratteri: li rimetto a posto.
        testo = testo.replace("\\n", "\n")
    percorso = Path(tempfile.gettempdir()) / "ig-cookies.txt"
    percorso.write_text(testo if testo.endswith("\n") else testo + "\n", encoding="utf-8")
    percorso.chmod(0o600)
    log.info("cookie Instagram presi dalla variabile IG_COOKIES")
    return percorso


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)

    access_key = env.get("ACCESS_KEY", "").strip()
    open_mode = _bool_env(env, "OPEN_MODE", False)

    if not access_key and not open_mode:
        raise ConfigError(
            "Manca ACCESS_KEY.\n"
            "E' la chiave segreta che sta dentro il tuo link: senza, il sito sarebbe\n"
            "aperto a chiunque ne indovini l'indirizzo, quindi mi fermo qui.\n"
            "Generane una con:  python3 -c \"import secrets; print(secrets.token_urlsafe(24))\"\n"
            "e mettila tra i secret dello Space (Settings > Variables and secrets).\n"
            "Se vuoi davvero un sito aperto a tutti, imposta OPEN_MODE=1."
        )
    if access_key and len(access_key) < 16:
        raise ConfigError("ACCESS_KEY deve essere lunga almeno 16 caratteri: e' l'unica cosa che protegge il sito.")

    secret_key = env.get("SECRET_KEY", "").strip() or _derive(access_key or "aperto", "firma-biglietti")

    cookies_file = _cookies_da_variabile(env.get("IG_COOKIES", ""))
    if cookies_file is None:
        grezzo = env.get("COOKIES_FILE", "").strip()
        if grezzo:
            candidato = Path(grezzo).expanduser()
            if candidato.is_file():
                cookies_file = candidato
            else:
                log.warning("COOKIES_FILE punta a %s che non esiste: lo ignoro.", candidato)

    tmp_raw = env.get("TMP_DIR", "").strip()
    tmp_dir = Path(tmp_raw).expanduser() if tmp_raw else Path(tempfile.gettempdir()) / "scarico-pin-insta"

    return Settings(
        access_key=access_key,
        secret_key=secret_key,
        cookies_file=cookies_file,
        max_filesize_mb=_int_env(env, "MAX_FILESIZE_MB", 2048),
        max_concurrent=max(1, _int_env(env, "MAX_CONCURRENT", 2)),
        tmp_dir=tmp_dir,
        open_mode=open_mode,
    )
# --------------------------------------------------------------------------
# store.py
# --------------------------------------------------------------------------
"""Memoria temporanea dei link risolti.

Dopo /api/resolve il server deve ricordarsi l'URL diretto del video per
servirlo a /api/file. Tenerlo in RAM e' sufficiente: il sito ha un solo
utente e un solo processo. Al riavvio si perde, e va bene cosi'.
"""


import threading
import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLStore(Generic[T]):
    def __init__(self, ttl_seconds: int = 7200, max_items: int = 200) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: dict[str, tuple[float, T]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: T) -> None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            if len(self._items) >= self._max:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                self._items.pop(oldest, None)
            self._items[key] = (now, value)

    def get(self, key: str) -> T | None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            found = self._items.get(key)
            return found[1] if found else None

    def __len__(self) -> int:
        with self._lock:
            self._purge(time.monotonic())
            return len(self._items)

    def _purge(self, now: float) -> None:
        dead = [k for k, (stamp, _) in self._items.items() if now - stamp > self._ttl]
        for key in dead:
            self._items.pop(key, None)
# --------------------------------------------------------------------------
# urls.py
# --------------------------------------------------------------------------
"""Riconoscimento e pulizia dei link, piu' la difesa contro le richieste interne.

Due controlli separati, con scopi diversi:

1. `normalize_shared_text` guarda il link che arriva dal telefono. Accetta solo
   Pinterest e Instagram: senza questo filtro il server diventerebbe un proxy
   aperto con cui chiunque conosca la password potrebbe scaricare qualsiasi cosa.
2. `assert_public_url` guarda il link del file che il server sta per aprire.
   Serve a impedire che un URL restituito da terzi faccia bussare il server a
   indirizzi della rete locale (127.0.0.1, 192.168.x.x, metadata dei cloud).
"""


import ipaddress
import os
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

PINTEREST = "pinterest"
INSTAGRAM = "instagram"

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Parametri che vale la pena tenere: tutto il resto e' tracciamento.
_KEEP_QUERY = {"img_index"}

# Host interi, per gli accorciatori che non hanno il nome del sito nel dominio.
_HOST_ESATTI = {"pin.it": PINTEREST, "ig.me": INSTAGRAM, "instagr.am": INSTAGRAM}

# Nome registrabile del dominio, cioe' la parte prima del suffisso pubblico.
_NOMI = {"pinterest": PINTEREST, "instagram": INSTAGRAM}

# Suffissi a due livelli in cui il nome sta una etichetta piu' indietro
# (pinterest.co.uk, instagram.com.br).
_SUFFISSI_DOPPI = {"co", "com", "net", "org", "ne", "or"}


class UrlNotSupported(ValueError):
    """Il link non e' di Pinterest ne' di Instagram."""


class UnsafeUrl(ValueError):
    """Il link punta dentro la rete locale, o non e' http(s)."""


def _nome_registrabile(etichette: list[str]) -> str | None:
    """La parte di dominio che conta: "pinterest" in www.pinterest.co.uk.

    Guardare solo l'inizio o un pezzo qualsiasi dell'host non basta:
    pinterest.evil.com e instagram.com.attacco.net contengono il nome giusto
    ma appartengono a qualcun altro.
    """
    if len(etichette) < 2:
        return None
    if len(etichette) >= 3 and etichette[-2] in _SUFFISSI_DOPPI and len(etichette[-1]) == 2:
        return etichette[-3]
    return etichette[-2]


def classify(host: str | None) -> str | None:
    """Ritorna "pinterest", "instagram" oppure None."""
    if not host:
        return None
    h = host.lower().strip().strip(".")
    if h.startswith("www."):
        h = h[4:]
    if h in _HOST_ESATTI:
        return _HOST_ESATTI[h]
    nome = _nome_registrabile(h.split("."))
    return _NOMI.get(nome) if nome else None


def clean_url(raw: str) -> tuple[str, str]:
    """Pulisce un singolo URL. Ritorna (url_pulito, sorgente)."""
    parsed = urlparse(raw.strip())
    source = classify(parsed.hostname)
    if parsed.scheme not in ("http", "https") or source is None:
        raise UrlNotSupported(raw)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False) if k in _KEEP_QUERY]
    # Le URL dei pin e dei reel finiscono spesso con una / di troppo quando
    # arrivano dal foglio di condivisione: non cambia nulla, ma tenerle
    # uniformi evita di risolvere due volte lo stesso link.
    path = re.sub(r"/{2,}", "/", parsed.path)
    cleaned = urlunparse(("https", parsed.netloc.lower(), path, "", urlencode(query), ""))
    return cleaned, source


def normalize_shared_text(text: str) -> tuple[str, str]:
    """Estrae il primo link utile da un testo condiviso.

    Il foglio di condivisione di iOS incolla spesso titolo + link insieme,
    per questo non si puo' assumere che il testo sia solo un URL.
    """
    if not text or not text.strip():
        raise UrlNotSupported("testo vuoto")
    candidates = _URL_RE.findall(text)
    if not candidates:
        # L'utente puo' aver incollato "instagram.com/reel/..." senza schema.
        candidates = ["https://" + text.strip().split()[0]]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return clean_url(candidate.rstrip(").,;'\""))
        except UrlNotSupported as exc:  # noqa: PERF203 - pochi candidati
            last_error = exc
    raise UrlNotSupported(str(last_error) if last_error else text)


def _dns_check_enabled() -> bool:
    """Con un proxy HTTP di uscita la risoluzione DNS la fa il proxy, non noi.

    In quel caso il controllo sugli indirizzi IP non puo' funzionare e va
    disattivato esplicitamente, altrimenti bloccherebbe tutto.
    """
    if os.environ.get("SKIP_DNS_GUARD", "").strip() == "1":
        return False
    return not any(os.environ.get(var) for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))


def assert_public_url(url: str) -> None:
    """Alza UnsafeUrl se l'URL non e' http(s) pubblico."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrl(f"schema non ammesso: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeUrl("host mancante")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        _assert_public_ip(literal, host)
        return

    if not _dns_check_enabled():
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"non riesco a risolvere {host}: {exc}") from exc
    for info in infos:
        _assert_public_ip(ipaddress.ip_address(info[4][0]), host)


def _assert_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, host: str) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UnsafeUrl(f"{host} risolve a {ip}, che e' un indirizzo interno")
# --------------------------------------------------------------------------
# extractor.py
# --------------------------------------------------------------------------
"""Risoluzione dei link in file scaricabili, via yt-dlp."""


import logging
import re
import secrets
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError


log = logging.getLogger("scarico.extractor")

BROWSER_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


class NothingToDownload(RuntimeError):
    """Il link e' valido ma non contiene nulla di scaricabile."""


class NeedsLogin(RuntimeError):
    """Il contenuto e' privato o Instagram chiede di autenticarsi."""


class TooBig(RuntimeError):
    """Il file supera il limite configurato."""


@dataclass
class Item:
    """Un singolo file scaricabile."""

    jid: str
    source: str
    kind: str  # "video" oppure "immagine"
    title: str
    filename: str
    page_url: str
    direct_url: str | None = None
    http_headers: dict[str, str] = field(default_factory=dict)
    needs_merge: bool = False
    format_id: str | None = None
    ext: str = "mp4"
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    filesize: int | None = None
    thumbnail: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Quello che il telefono ha bisogno di sapere: mai l'URL del CDN."""
        return {
            "id": self.jid,
            "source": self.source,
            "kind": self.kind,
            "title": self.title,
            "filename": self.filename,
            "ext": self.ext,
            "duration": round(self.duration) if self.duration else None,
            "width": self.width,
            "height": self.height,
            "filesize": self.filesize,
            "mime": mime_di(self.ext, self.kind),
            "has_thumb": bool(self.thumbnail),
            "merge": self.needs_merge,
        }


# --------------------------------------------------------------------------- #
# Nomi dei file
# --------------------------------------------------------------------------- #

# iOS decide "Salva video" o "Salva immagine" dal tipo MIME del file condiviso:
# se il tipo e' sbagliato o generico, la voce per la galleria non compare.
MIME_PER_ESTENSIONE = {
    "mp4": "video/mp4",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
    "webm": "video/webm",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}


def mime_di(ext: str, kind: str) -> str:
    pulita = (ext or "").lower().lstrip(".")
    difetto = "video/mp4" if kind == "video" else "image/jpeg"
    return MIME_PER_ESTENSIONE.get(pulita, difetto)


_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_SPACES = re.compile(r"\s+")


def slugify(title: str, fallback: str = "video") -> str:
    """Nome file leggibile: tiene accenti e spazi, toglie cio' che rompe i filesystem."""
    text = unicodedata.normalize("NFC", title or "").strip()
    text = _BAD_CHARS.sub(" ", text)
    text = _SPACES.sub(" ", text).strip(" .-_")
    if len(text) > 70:
        text = text[:70].rstrip(" .-_")
    return text or fallback


def build_filename(source: str, title: str, ext: str, jid: str) -> str:
    prefix = "pinterest" if source == PINTEREST else "instagram"
    safe_ext = re.sub(r"[^a-z0-9]", "", (ext or "mp4").lower()) or "mp4"
    return f"{prefix} - {slugify(title)} - {jid[:6]}.{safe_ext}"


# --------------------------------------------------------------------------- #
# yt-dlp
# --------------------------------------------------------------------------- #


def _ydl_options(cookies_file: Path | None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "skip_download": True,
        "noprogress": True,
        "socket_timeout": 30,
        "extractor_retries": 2,
        "http_headers": {"User-Agent": BROWSER_UA},
    }
    if cookies_file:
        opts["cookiefile"] = str(cookies_file)
    return opts


def _is_progressive(fmt: dict[str, Any]) -> bool:
    """Un formato progressive ha audio e video nello stesso file: si puo' servire al volo."""
    if not fmt.get("url"):
        return False
    protocol = (fmt.get("protocol") or "https").lower()
    if not protocol.startswith("http") or protocol.startswith("http_dash"):
        return False
    return fmt.get("vcodec") not in (None, "none") and fmt.get("acodec") not in (None, "none")


def _format_rank(fmt: dict[str, Any]) -> tuple:
    return (
        fmt.get("height") or 0,
        fmt.get("tbr") or 0,
        fmt.get("filesize") or fmt.get("filesize_approx") or 0,
    )


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if not thumbs:
        return None
    thumbs.sort(key=lambda t: (t.get("preference") or 0, t.get("width") or 0))
    return thumbs[-1]["url"]


def _entries_of(info: dict[str, Any]) -> list[dict[str, Any]]:
    """Appiattisce i post con piu' elementi (caroselli Instagram, bacheche)."""
    if info.get("_type") not in ("playlist", "multi_video"):
        return [info]
    flat: list[dict[str, Any]] = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        flat.extend(_entries_of(entry))
    return flat


def _item_from_entry(entry: dict[str, Any], source: str, page_url: str, max_bytes: int) -> Item | None:
    formats = [f for f in (entry.get("formats") or []) if f.get("url")]
    progressive = [f for f in formats if _is_progressive(f)]
    jid = secrets.token_urlsafe(9)
    title = entry.get("title") or entry.get("description") or entry.get("id") or "senza titolo"
    title = str(title).splitlines()[0]

    if progressive:
        chosen = max(progressive, key=_format_rank)
        size = chosen.get("filesize") or chosen.get("filesize_approx")
        if max_bytes and size and size > max_bytes:
            raise TooBig(f"{size / 1048576:.0f} MB")
        ext = chosen.get("ext") or "mp4"
        return Item(
            jid=jid,
            source=source,
            kind="video",
            title=title,
            filename=build_filename(source, title, ext, jid),
            page_url=page_url,
            direct_url=chosen["url"],
            http_headers={**{"User-Agent": BROWSER_UA}, **(chosen.get("http_headers") or {})},
            format_id=chosen.get("format_id"),
            ext=ext,
            duration=entry.get("duration"),
            width=chosen.get("width") or entry.get("width"),
            height=chosen.get("height") or entry.get("height"),
            filesize=size,
            thumbnail=_best_thumbnail(entry),
        )

    if formats:
        # Solo flussi separati o HLS: va ricomposto con ffmpeg lato server.
        best = max(formats, key=_format_rank)
        return Item(
            jid=jid,
            source=source,
            kind="video",
            title=title,
            filename=build_filename(source, title, "mp4", jid),
            page_url=entry.get("webpage_url") or page_url,
            needs_merge=True,
            ext="mp4",
            duration=entry.get("duration"),
            width=best.get("width") or entry.get("width"),
            height=best.get("height") or entry.get("height"),
            thumbnail=_best_thumbnail(entry),
        )

    thumb = _best_thumbnail(entry)
    if thumb:
        # Pin fotografico: non c'e' video, ma l'immagine a piena risoluzione si'.
        ext = "jpg" if ".png" not in thumb.lower() else "png"
        return Item(
            jid=jid,
            source=source,
            kind="immagine",
            title=title,
            filename=build_filename(source, title, ext, jid),
            page_url=page_url,
            direct_url=thumb,
            http_headers={"User-Agent": BROWSER_UA},
            ext=ext,
            width=entry.get("width"),
            height=entry.get("height"),
            thumbnail=thumb,
        )
    return None


_LOGIN_HINTS = (
    "login required",
    "requested content is not available",
    "rate-limit",
    "rate limit",
    "empty media response",
    "main webpage is locked behind the login page",
    "you need to log in",
    "private",
)


def extract(url: str, source: str, cookies_file: Path | None, max_bytes: int) -> list[Item]:
    """Risolve un link in una lista di elementi scaricabili."""
    try:
        with YoutubeDL(_ydl_options(cookies_file)) as ydl:
            info = ydl.extract_info(url, download=False)
    except (DownloadError, ExtractorError) as exc:
        message = str(exc)
        lowered = message.lower()
        if any(hint in lowered for hint in _LOGIN_HINTS):
            raise NeedsLogin(message) from exc
        if source == PINTEREST:
            fallback = pinterest_fallback(url, max_bytes)
            if fallback:
                return fallback
        log.info("estrazione fallita per %s: %s", url, message)
        raise NothingToDownload(message) from exc

    if not info:
        raise NothingToDownload("yt-dlp non ha restituito nulla")

    items: list[Item] = []
    too_big: TooBig | None = None
    for entry in _entries_of(info):
        try:
            item = _item_from_entry(entry, source, info.get("webpage_url") or url, max_bytes)
        except TooBig as exc:
            too_big = exc
            continue
        if item:
            items.append(item)

    if not items:
        if too_big:
            raise too_big
        if source == PINTEREST:
            fallback = pinterest_fallback(url, max_bytes)
            if fallback:
                return fallback
        raise NothingToDownload("nessun video o immagine in questo link")
    return items


# --------------------------------------------------------------------------- #
# Ripiego per Pinterest: leggere i meta tag della pagina
# --------------------------------------------------------------------------- #

_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](og:video(?::secure_url|:url)?|og:image|og:title)["\'][^>]*content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_RE_REVERSED = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](og:video(?::secure_url|:url)?|og:image|og:title)["\']',
    re.IGNORECASE,
)


def pinterest_fallback(url: str, max_bytes: int) -> list[Item]:
    """Quando yt-dlp non riconosce un pin, i meta tag Open Graph bastano spesso."""
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": BROWSER_UA, "Accept-Language": "it-IT,it;q=0.9"},
            follow_redirects=True,
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("ripiego Pinterest fallito su %s: %s", url, exc)
        return []

    found: dict[str, str] = {}
    for pattern in (_META_RE, _META_RE_REVERSED):
        for first, second in pattern.findall(response.text):
            key, value = (first, second) if first.startswith("og:") else (second, first)
            found.setdefault(key.lower(), value)

    video = found.get("og:video:secure_url") or found.get("og:video:url") or found.get("og:video")
    image = found.get("og:image")
    title = found.get("og:title") or "pin"
    media = video or image
    if not media:
        return []
    try:
        assert_public_url(media)
    except UnsafeUrl:
        return []

    jid = secrets.token_urlsafe(9)
    ext = media.rsplit(".", 1)[-1].split("?")[0].lower()
    if ext not in ("mp4", "m4v", "mov", "jpg", "jpeg", "png", "webp", "gif"):
        ext = "mp4" if video else "jpg"
    return [
        Item(
            jid=jid,
            source=PINTEREST,
            kind="video" if video else "immagine",
            title=title,
            filename=build_filename(PINTEREST, title, ext, jid),
            page_url=url,
            direct_url=media,
            http_headers={"User-Agent": BROWSER_UA, "Referer": "https://www.pinterest.com/"},
            ext=ext,
            thumbnail=image or media,
        )
    ]


# --------------------------------------------------------------------------- #
# Download con ricomposizione (serve ffmpeg)
# --------------------------------------------------------------------------- #


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def download_merged(item: Item, dest_dir: Path, cookies_file: Path | None) -> Path:
    """Scarica e ricompone audio+video in un mp4 nella cartella indicata."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = _ydl_options(cookies_file)
    opts.update(
        {
            "skip_download": False,
            "outtmpl": str(dest_dir / "media.%(ext)s"),
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "concurrent_fragment_downloads": 4,
            "retries": 3,
        }
    )
    with YoutubeDL(opts) as ydl:
        ydl.download([item.page_url])

    produced = sorted(dest_dir.glob("media.*"), key=lambda p: p.stat().st_size, reverse=True)
    produced = [p for p in produced if p.suffix.lower() not in (".part", ".ytdl")]
    if not produced:
        raise NothingToDownload("ffmpeg non ha prodotto alcun file")
    return produced[0]


def ffmpeg_version() -> str | None:
    if not have_ffmpeg():
        return None
    try:
        out = subprocess.run(  # noqa: S603 - percorso da shutil.which, nessun input utente
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (out.stdout or "").splitlines()
    return first[0] if first else None
# --------------------------------------------------------------------------
# main.py
# --------------------------------------------------------------------------
"""Applicazione FastAPI: serve il sito e risolve i link.

Non c'e' nessuna password da digitare. Il permesso sta dentro il link che ti
salvi sulla schermata Home (`?k=...`): chi non ha quel link non entra, tu non
devi ricordare niente.
"""


import asyncio
import logging
import shutil
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scarico")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# Il sito puo' girare in due modi: dal progetto, leggendo la cartella web/,
# oppure come file unico (vedi tools/build_hf.py), dove le pagine sono stringhe
# dentro al programma e le icone vengono disegnate all'avvio.
RISORSE_WEB: dict[str, str] = _RISORSE
GENERATORE_ICONE = _icone_in_tmp


def _pagina(nome: str, media_type: str) -> Response:
    contenuto = RISORSE_WEB.get(nome)
    if contenuto is not None:
        return Response(contenuto, media_type=media_type, headers=_NO_STORE)
    return FileResponse(WEB_DIR / nome, media_type=media_type, headers=_NO_STORE)

TICKET_TTL = 6 * 3600  # un biglietto vale sei ore
MAX_THUMB_BYTES = 8 * 1024 * 1024

settings: Settings = load_settings()
jobs: TTLStore[Item] = TTLStore(ttl_seconds=TICKET_TTL)
extract_semaphore = asyncio.Semaphore(settings.max_concurrent)
merge_semaphore = asyncio.Semaphore(1)

app = FastAPI(title="Scarico Pin e Insta", version=__version__, docs_url=None, redoc_url=None)


@app.on_event("startup")
async def _startup() -> None:
    shutil.rmtree(settings.tmp_dir, ignore_errors=True)
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    log.info("Scarico Pin e Insta %s pronto", __version__)
    log.info("ffmpeg: %s", ffmpeg_version() or "NON trovato (i video da ricomporre falliranno)")
    log.info("cookie Instagram: %s", "si" if settings.cookies_file else "no (molti post falliranno)")
    if settings.open_mode:
        log.warning("OPEN_MODE attivo: il sito e' aperto a chiunque conosca l'indirizzo")


# --------------------------------------------------------------------------- #
# Chiave d'accesso
# --------------------------------------------------------------------------- #

_tentativi: dict[str, list[float]] = {}
_FINESTRA = 900.0
_MAX_TENTATIVI = 20


def _chi(request: Request) -> str:
    inoltrato = request.headers.get("x-forwarded-for", "")
    if inoltrato:
        return inoltrato.split(",")[0].strip()
    return request.client.host if request.client else "sconosciuto"


def _troppi_tentativi(chiave: str) -> bool:
    ora = time.monotonic()
    recenti = [t for t in _tentativi.get(chiave, []) if ora - t < _FINESTRA]
    _tentativi[chiave] = recenti
    return len(recenti) >= _MAX_TENTATIVI


async def require_key(
    request: Request,
    k: str | None = Query(default=None, description="chiave d'accesso"),
    x_chiave: str | None = Header(default=None),
) -> None:
    """Il permesso arriva dal link (`?k=`) o dall'intestazione X-Chiave."""
    if settings.open_mode:
        return
    fornita = x_chiave or k
    if check_key(settings.access_key, fornita):
        _tentativi.pop(_chi(request), None)
        return
    chi = _chi(request)
    if _troppi_tentativi(chi):
        raise HTTPException(status_code=429, detail="Troppi tentativi. Riprova tra un quarto d'ora.")
    _tentativi.setdefault(chi, []).append(time.monotonic())
    log.warning("chiave mancante o sbagliata da %s", chi)
    raise HTTPException(
        status_code=401,
        detail="Questo indirizzo non e' completo. Apri il tuo link personale, quello che finisce con ?k=...",
    )


def _base_url(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}"


# --------------------------------------------------------------------------- #
# Pagine e file statici
# --------------------------------------------------------------------------- #

_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


@app.get("/", include_in_schema=False)
async def index() -> Response:
    # La pagina si apre sempre: e' il JavaScript a dire "serve il link completo"
    # se la chiave non c'e'. Una pagina di errore secca spaventerebbe e basta.
    return _pagina("index.html", "text/html; charset=utf-8")


@app.get("/style.css", include_in_schema=False)
async def foglio_di_stile() -> Response:
    return _pagina("style.css", "text/css; charset=utf-8")


@app.get("/app.js", include_in_schema=False)
async def programma() -> Response:
    return _pagina("app.js", "application/javascript; charset=utf-8")


@app.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    return _pagina("sw.js", "application/javascript; charset=utf-8")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest(k: str | None = Query(default=None)) -> JSONResponse:
    """Il manifest si porta dietro la chiave.

    Quando iOS aggiunge il sito alla schermata Home puo' usare `start_url`:
    se li' dentro la chiave non ci fosse, l'icona aprirebbe un indirizzo nudo
    e il sito direbbe "serve il link completo" ogni volta.
    """
    start = "/"
    if k and (settings.open_mode or check_key(settings.access_key, k)):
        start = f"/?k={quote(k, safe='')}"
    return JSONResponse(
        {
            "name": "Scarico Pin e Insta",
            "short_name": "Scarico",
            "description": "Salva in galleria i video e le foto di Pinterest e Instagram.",
            "lang": "it",
            "start_url": start,
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#0c0a12",
            "theme_color": "#0c0a12",
            "icons": [
                {"src": "/icons/icon-180.png", "sizes": "180x180", "type": "image/png"},
                {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        },
        media_type="application/manifest+json",
        headers=_NO_STORE,
    )


@app.get("/api/health", include_in_schema=False)
async def health() -> dict[str, object]:
    return {"ok": True, "version": __version__, "ffmpeg": have_ffmpeg(), "in_memoria": len(jobs)}


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@app.get("/api/me")
async def me(_: None = Depends(require_key)) -> JSONResponse:
    return JSONResponse(
        {
            "versione": __version__,
            "ffmpeg": have_ffmpeg(),
            "cookie_instagram": bool(settings.cookies_file),
            "limite_mb": settings.max_filesize_mb,
        },
        headers=_NO_STORE,
    )


async def _resolve(raw_url: str) -> list[Item]:
    try:
        url, source = normalize_shared_text(raw_url)
    except UrlNotSupported as exc:
        raise HTTPException(
            status_code=400,
            detail="Questo link non e' di Pinterest o Instagram. Copia l'indirizzo di un pin, "
            "di un reel, di una storia o di un post.",
        ) from exc

    async with extract_semaphore:
        try:
            items = await asyncio.to_thread(
                extract, url, source, settings.cookies_file, settings.max_filesize_bytes
            )
        except NeedsLogin as exc:
            log.info("serve login per %s: %s", url, exc)
            raise HTTPException(
                status_code=403,
                detail="Instagram chiede di essere loggati per questo contenuto. Aggiungi i cookie "
                "nelle impostazioni del server, oppure il post e' privato.",
            ) from exc
        except TooBig as exc:
            raise HTTPException(
                status_code=413,
                detail=f"Il file pesa {exc} e supera il limite di {settings.max_filesize_mb} MB.",
            ) from exc
        except NothingToDownload as exc:
            log.info("niente da scaricare in %s: %s", url, exc)
            raise HTTPException(
                status_code=404, detail="Non ho trovato nessun video o foto in questo link."
            ) from exc

    for item in items:
        jobs.put(item.jid, item)
    return items


def _with_tickets(items: list[Item]) -> list[dict[str, object]]:
    out = []
    for item in items:
        data = item.public_dict()
        biglietto = make_token(settings.secret_key, "file", TICKET_TTL, jid=item.jid)
        data["url_file"] = f"/api/file/{item.jid}?t={biglietto}"
        if item.thumbnail:
            data["url_thumb"] = f"/api/thumb/{item.jid}?t=" + make_token(
                settings.secret_key, "thumb", TICKET_TTL, jid=item.jid
            )
        out.append(data)
    return out


@app.post("/api/resolve")
async def resolve(request: Request, _: None = Depends(require_key)) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        body = {}
    raw = str((body or {}).get("url") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Manca il link.")
    items = await _resolve(raw)
    log.info("risolti %d elementi da %s", len(items), items[0].page_url)
    return JSONResponse({"elementi": _with_tickets(items)}, headers=_NO_STORE)


@app.get("/api/shortcut")
async def shortcut(
    request: Request,
    u: str = Query(..., description="link di Pinterest o Instagram"),
    _: None = Depends(require_key),
) -> JSONResponse:
    """Per l'app Comandi rapidi di iOS: un link in ingresso, un file in uscita."""
    items = await _resolve(u)
    base = _base_url(request)
    arricchiti = _with_tickets(items)
    primo = arricchiti[0]
    return JSONResponse(
        {
            "ok": True,
            "download": base + str(primo["url_file"]),
            "nome_file": primo["filename"],
            "tipo": primo["mime"],
            "quanti": len(arricchiti),
            "tutti": [
                {"download": base + str(e["url_file"]), "nome_file": e["filename"], "tipo": e["mime"]}
                for e in arricchiti
            ],
        },
        headers=_NO_STORE,
    )


# --------------------------------------------------------------------------- #
# Consegna dei file
# --------------------------------------------------------------------------- #


def _content_disposition(filename: str) -> str:
    """Nome del file per il browser, in versione ASCII e in versione UTF-8."""
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii").replace('"', "")
    ) or "file"
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'


def _ticket_item(jid: str, token: str, aud: str) -> Item:
    claims = read_token(settings.secret_key, token, aud)
    if claims is None or claims.get("jid") != jid:
        raise HTTPException(status_code=403, detail="Questo link di download e' scaduto. Cerca di nuovo il post.")
    item = jobs.get(jid)
    if item is None:
        raise HTTPException(
            status_code=410,
            detail="Il server si e' riavviato e ha perso questo download. Incolla di nuovo il link.",
        )
    return item


async def _proxy(
    item: Item, request: Request, *, attachment: bool, byte_cap: int | None = None
) -> StreamingResponse:
    direct = item.direct_url
    if not direct:
        raise HTTPException(status_code=500, detail="Elemento senza URL diretto.")
    try:
        assert_public_url(direct)
    except UnsafeUrl as exc:
        log.warning("bloccato URL non pubblico: %s", exc)
        raise HTTPException(status_code=502, detail="L'indirizzo del file non e' pubblico.") from exc

    headers = dict(item.http_headers)
    incoming_range = request.headers.get("range")
    if incoming_range:
        headers["Range"] = incoming_range

    client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=900.0))
    try:
        upstream = await client.send(client.build_request("GET", direct, headers=headers), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        log.warning("il CDN non risponde: %s", exc)
        raise HTTPException(status_code=504, detail="Il server di Pinterest/Instagram non risponde.") from exc

    if upstream.status_code >= 400:
        code = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        log.warning("il CDN ha risposto %s per %s", code, item.jid)
        raise HTTPException(
            status_code=502,
            detail=f"Il file non e' piu' disponibile (il CDN ha risposto {code}). Cerca di nuovo il post.",
        )

    declared = upstream.headers.get("content-length")
    misura = int(declared) if declared and declared.isdigit() else None
    if misura is not None:
        if byte_cap and misura > byte_cap:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(status_code=413, detail="File troppo grande.")
        if attachment and settings.max_filesize_bytes and misura > settings.max_filesize_bytes:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(
                status_code=413,
                detail=f"Il file pesa {misura / 1048576:.0f} MB e supera il limite di {settings.max_filesize_mb} MB.",
            )

    out_headers = {"Cache-Control": "no-store, max-age=0"}
    for name in ("content-length", "content-range", "accept-ranges"):
        if name in upstream.headers:
            out_headers[name.title()] = upstream.headers[name]
    if attachment:
        out_headers["Content-Disposition"] = _content_disposition(item.filename)

    media_type = upstream.headers.get("content-type") or (
        "video/mp4" if item.kind == "video" else "image/jpeg"
    )

    async def closer() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_bytes(131072),
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=media_type,
        background=BackgroundTask(closer),
    )


@app.get("/api/thumb/{jid}")
async def thumb(jid: str, request: Request, t: str = Query(...)) -> StreamingResponse:
    """Le miniature passano dal server: il CDN non vede mai il telefono."""
    item = _ticket_item(jid, t, "thumb")
    stand_in = Item(
        jid=item.jid,
        source=item.source,
        kind="immagine",
        title=item.title,
        filename=item.filename,
        page_url=item.page_url,
        direct_url=item.thumbnail,
        http_headers=item.http_headers,
    )
    risposta = await _proxy(stand_in, request, attachment=False, byte_cap=MAX_THUMB_BYTES)
    risposta.headers["Cache-Control"] = "private, max-age=3600"
    return risposta


@app.get("/api/file/{jid}")
async def file(jid: str, request: Request, t: str = Query(...), apri: int = Query(0)):
    """Consegna il file.

    `apri=1` lo manda senza l'intestazione "da salvare": serve quando la pagina
    lo legge in memoria per passarlo al foglio di condivisione di iOS, o quando
    lo apre per mostrarlo.
    """
    item = _ticket_item(jid, t, "file")

    if not item.needs_merge:
        return await _proxy(item, request, attachment=not apri)

    if not have_ffmpeg():
        raise HTTPException(
            status_code=501,
            detail="Questo video ha audio e video separati e serve ffmpeg sul server per unirli.",
        )

    async with merge_semaphore:
        work_dir = settings.tmp_dir / item.jid
        try:
            path = await asyncio.to_thread(download_merged, item, work_dir, settings.cookies_file)
        except NothingToDownload as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(status_code=502, detail=f"Non sono riuscito a comporre il video: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - qualunque errore di yt-dlp va riportato pulito
            shutil.rmtree(work_dir, ignore_errors=True)
            log.exception("errore durante la composizione di %s", item.jid)
            raise HTTPException(status_code=502, detail="Non sono riuscito a comporre il video.") from exc

        size = path.stat().st_size
        if settings.max_filesize_bytes and size > settings.max_filesize_bytes:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise HTTPException(
                status_code=413,
                detail=f"Il video pesa {size / 1048576:.0f} MB e supera il limite di {settings.max_filesize_mb} MB.",
            )

        filename = item.filename
        if path.suffix and not filename.lower().endswith(path.suffix.lower()):
            filename = filename.rsplit(".", 1)[0] + path.suffix
        return FileResponse(
            path,
            filename=None if apri else filename,
            media_type="video/mp4",
            headers={"Cache-Control": "no-store, max-age=0"},
            background=BackgroundTask(shutil.rmtree, work_dir, True),
        )


def monta_statici() -> None:
    """Le icone e gli altri file: da disco se ci sono, altrimenti disegnate."""
    icone = WEB_DIR / "icons"
    if not icone.is_dir() and GENERATORE_ICONE is not None:
        icone = GENERATORE_ICONE()
    if icone.is_dir():
        app.mount("/icons", StaticFiles(directory=icone), name="icone")
    # Per ultimo, cosi' nessun file statico puo' coprire le API.
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=False), name="web")


monta_statici()
