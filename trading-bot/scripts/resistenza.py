"""Prova di resistenza a tempo accelerato.

    python -m scripts.resistenza --ore 72

Simula ore di mercato iniettando guasti (disconnessioni, riavvii, salti
dell'orologio, esecuzioni ripetute) e verifica gli invarianti della
contabilita' dopo ognuno.

Il tempo e' simulato: questa prova NON sostituisce le 72 ore reali, e il
rapporto lo dice. Serve a trovare i difetti di logica prima di spendere
tre giorni per scoprirli.
"""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from config.schema import load_config
from monitoring.logger import setup_logging
from resistenza.prova import ProvaDiResistenza


def main() -> int:
    parser = argparse.ArgumentParser(description="Prova di resistenza a tempo simulato")
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--ore", type=float, default=24.0)
    parser.add_argument("--seed", type=int, default=20_261_018)
    parser.add_argument(
        "--cartella",
        type=Path,
        default=None,
        help="dove tenere stato e registro della prova (default: cartella temporanea)",
    )
    args = parser.parse_args()

    setup_logging(level="INFO", json_logs=False)
    config = load_config(args.config)

    with tempfile.TemporaryDirectory() as temporanea:
        cartella = args.cartella or Path(temporanea)
        cartella.mkdir(parents=True, exist_ok=True)
        prova = ProvaDiResistenza(config=config, cartella=cartella, seed=args.seed)
        esito = asyncio.run(prova.esegui(minuti=int(args.ore * 60)))

    print(esito.rapporto())
    return 0 if esito.superata else 1


if __name__ == "__main__":
    raise SystemExit(main())
