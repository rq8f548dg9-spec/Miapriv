#!/usr/bin/env python3
"""Genera le icone PNG in web/icons/.

Uso:  python3 tools/make_icons.py [--rifai]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from icone import disegna_icone  # noqa: E402

USCITA = Path(__file__).resolve().parent.parent / "web" / "icons"

if __name__ == "__main__":
    if "--rifai" in sys.argv:
        for vecchia in USCITA.glob("icon-*.png"):
            vecchia.unlink()
    print("Disegno le icone in", USCITA)
    disegna_icone(USCITA, parlante=True)
