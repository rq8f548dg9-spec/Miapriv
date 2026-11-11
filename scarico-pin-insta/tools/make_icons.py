#!/usr/bin/env python3
"""Genera le icone PNG del sito senza dipendenze esterne.

Il disegno e' originale: una freccia che scende dentro un vassoio aperto, su
sfondo con sfumatura. Nessun logo o marchio di terzi.

Uso:  python3 tools/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

USCITA = Path(__file__).resolve().parent.parent / "web" / "icons"

# Sfumatura diagonale: viola acceso -> indaco profondo.
COLORE_A = (177, 76, 255)
COLORE_B = (74, 31, 150)
GLIFO = (255, 255, 255)

CAMPIONI = 3  # 3x3 campioni per pixel: basta per bordi puliti


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


def main() -> None:
    USCITA.mkdir(parents=True, exist_ok=True)
    lavori = [
        ("icon-180.png", 180, 1.0),
        ("icon-192.png", 192, 1.0),
        ("icon-512.png", 512, 1.0),
        # Nelle icone maskable Android ritaglia fino al 20% per lato:
        # il glifo va tenuto dentro il cerchio di sicurezza.
        ("icon-maskable-512.png", 512, 0.72),
    ]
    for nome, lato, scala in lavori:
        scrivi_png(USCITA / nome, lato, lato, disegna(lato, scala))
        print(f"  {nome}  {lato}x{lato}")


if __name__ == "__main__":
    main()
