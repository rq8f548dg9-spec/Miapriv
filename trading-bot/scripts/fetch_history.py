"""Scarica le candele storiche da Binance e le salva nello store Parquet.

    python scripts/fetch_history.py --days 30
    python scripts/fetch_history.py --symbols BTCUSDT --days 90

Senza dati storici il backtest non ha niente da leggere. Lo script usa
l'endpoint pubblico ``/api/v3/klines``, che non richiede autenticazione, e
rispetta il throttle del client.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import SecretStr

from config.credentials import Credentials, load_dotenv
from config.schema import Mode, load_config
from core.clock import LiveClock
from core.types import Candle, datetime_to_ms
from data.store import ParquetStore
from execution.binance import AiohttpClient, BinanceBroker
from monitoring.logger import setup_logging

logger = logging.getLogger("trading_bot.fetch")

#: Durata in millisecondi degli intervalli supportati.
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


def to_candle(symbol: str, row: list[object]) -> Candle:
    """Converte una riga di ``/api/v3/klines`` in una candela chiusa."""
    return Candle(
        symbol=symbol,
        open_time_ms=int(str(row[0])),
        close_time_ms=int(str(row[6])),
        open=Decimal(str(row[1])),
        high=Decimal(str(row[2])),
        low=Decimal(str(row[3])),
        close=Decimal(str(row[4])),
        volume=Decimal(str(row[5])),
        quote_volume=Decimal(str(row[7])),
        trades=int(str(row[8])),
        is_closed=True,
    )


async def fetch(symbols: list[str], days: int, config_path: Path) -> int:
    config = load_config(config_path)
    interval = config.data.candle_interval
    step_ms = INTERVAL_MS[interval]

    clock = LiveClock()
    # L'endpoint klines e' pubblico: le credenziali servono solo alla firma,
    # che qui non viene usata. Si passano segnaposto per non leggere chiavi vere.
    broker = BinanceBroker(
        config=config,
        credentials=Credentials(
            api_key=SecretStr("public"), api_secret=SecretStr("public"), mode=Mode.TESTNET
        ),
        clock=clock,
        http=AiohttpClient(),
    )
    store = ParquetStore(config.data.parquet_root, batch_size=config.data.write_batch_size)

    end = datetime.now(tz=UTC)
    start_ms = datetime_to_ms(end - timedelta(days=days))
    end_ms = datetime_to_ms(end)
    total = 0

    try:
        for symbol in symbols:
            cursor = start_ms
            while cursor < end_ms:
                rows = await broker.klines(symbol, interval, start_ms=cursor, limit=1000)
                if not rows:
                    break
                candles = [to_candle(symbol, row) for row in rows]
                # L'ultima candela potrebbe essere ancora in formazione: si scarta.
                candles = [c for c in candles if c.close_time_ms < end_ms]
                store.add_candles(candles)
                total += len(candles)
                cursor = int(str(rows[-1][0])) + step_ms
                logger.info("%s: %d candele fino a %s", symbol, total, cursor)
    finally:
        store.flush()
        await broker.close()

    logger.info("scaricate %d candele in %s", total, config.data.parquet_root)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Scarica candele storiche da Binance")
    parser.add_argument("--days", type=int, default=30, help="giorni di storia da scaricare")
    parser.add_argument("--symbols", nargs="*", help="simboli (default: quelli abilitati)")
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    args = parser.parse_args()

    load_dotenv()
    setup_logging(level="INFO", json_logs=False)
    config = load_config(args.config)
    symbols = args.symbols or [s.symbol for s in config.enabled_symbols]

    total = asyncio.run(fetch(symbols, args.days, args.config))
    print(f"scaricate {total} candele in {config.data.parquet_root}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
