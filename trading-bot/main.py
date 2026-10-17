"""Punto di ingresso del bot.

    python -m main                      # usa config/config.yaml
    python -m main --config altro.yaml
    python -m main --check              # valida la configurazione ed esce
    python -m main --backtest           # esegue il backtest sui dati salvati

La modalita' (``backtest`` / ``testnet`` / ``live``) si legge SOLO dal file
di configurazione, mai dalla riga di comando: un flag di shell e' troppo
facile da digitare per decidere se usare denaro vero.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from backtest.engine import BacktestEngine
from config.credentials import load_dotenv
from config.schema import AppConfig, Mode, load_config
from core.types import datetime_to_ms
from data.store import ParquetStore
from monitoring.logger import setup_logging
from runner import build_bot, build_generator

logger = logging.getLogger("trading_bot.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bot di trading modulare, testnet-first")
    parser.add_argument(
        "--config", type=Path, default=Path("config/config.yaml"), help="file di configurazione"
    )
    parser.add_argument(
        "--check", action="store_true", help="valida la configurazione ed esci"
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="esegui il backtest sui dati salvati invece di avviare il bot",
    )
    parser.add_argument(
        "--env", type=Path, default=Path(".env"), help="file con le variabili d'ambiente"
    )
    return parser.parse_args(argv)


def describe(config: AppConfig) -> str:
    symbols = ", ".join(s.symbol for s in config.enabled_symbols)
    limits = config.active_limits_summary()
    return (
        f"modalita'={config.mode.value} capitale={config.capital_quote} "
        f"{config.quote_asset} simboli=[{symbols}] generatore={config.signals.generator} "
        f"limiti={limits}"
    )


def run_backtest(config: AppConfig) -> int:
    """Esegue il backtest sulle candele salvate nello store Parquet."""
    if config.backtest is None:
        logger.error("sezione 'backtest' assente in configurazione")
        return 2

    store = ParquetStore(config.data.parquet_root)
    candles = store.read_candles(
        symbols=[s.symbol for s in config.enabled_symbols],
        start_ms=datetime_to_ms(config.backtest.start),
        end_ms=datetime_to_ms(config.backtest.end),
    )
    if not candles:
        logger.error(
            "nessuna candela in %s nella finestra richiesta: scaricare prima i dati "
            "(scripts/fetch_history.py)",
            config.data.parquet_root,
        )
        return 2

    result = BacktestEngine(config, build_generator(config)).run(candles)
    logger.info("backtest concluso: %s", result.summary())
    print(result.summary())

    if not result.metrics.is_viable:
        print(
            "\nLa strategia NON regge i costi di transazione su questi dati.\n"
            "Non ha senso passare al modello predittivo finche' la baseline\n"
            "a regole non supera questa soglia."
        )
        return 1
    return 0


async def run_bot(config: AppConfig) -> int:
    bot = build_bot(config)
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info("interruzione richiesta dall'operatore")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(args.env)

    try:
        # Qui dentro scatta la guardia sul denaro reale, se mode e' live.
        config = load_config(args.config)
    except Exception as exc:
        print(f"configurazione non valida: {exc}", file=sys.stderr)
        return 2

    setup_logging(
        level=config.monitoring.log_level,
        json_logs=config.monitoring.json_logs,
        log_file=config.monitoring.log_file,
    )
    logger.info("avvio: %s", describe(config))

    if args.check:
        print(f"configurazione valida: {describe(config)}")
        return 0

    if args.backtest or config.mode is Mode.BACKTEST:
        return run_backtest(config)

    return asyncio.run(run_bot(config))


if __name__ == "__main__":
    raise SystemExit(main())
