"""Scarica le candele storiche da Binance e le salva nello store Parquet.

    python -m scripts.fetch_history --days 30
    python -m scripts.fetch_history --symbols BTCUSDT --days 90

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
from config.schema import INTERVAL_MS, Mode, load_config
from core.clock import LiveClock
from core.types import Candle, datetime_to_ms
from data.store import ParquetStore
from execution.binance import AiohttpClient, BinanceBroker
from monitoring.logger import setup_logging

logger = logging.getLogger("trading_bot.fetch")



def step_ms_for(interval: str) -> int:
    """Durata di una candela dell'intervallo richiesto, in millisecondi.

    La tabella e' quella che la configurazione usa per validare: cosi' un
    intervallo ammesso e' per costruzione un intervallo scaricabile.
    """
    durata = INTERVAL_MS.get(interval)
    if durata is None:
        raise ValueError(
            f"intervallo {interval!r} non scaricabile: ammessi {sorted(INTERVAL_MS)}"
        )
    return durata


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


async def fetch(
    symbols: list[str], days: int, config_path: Path, *, trade_days: int = 0
) -> int:
    config = load_config(config_path)
    interval = config.data.candle_interval
    step_ms = step_ms_for(interval)

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
        if trade_days > 0:
            totale_trade = await _scarica_flusso(
                broker, store, symbols, giorni=trade_days, fine_ms=end_ms
            )
            logger.info("scaricati %d trade (flusso ordini)", totale_trade)
    finally:
        store.flush()
        await broker.close()

    logger.info("scaricate %d candele in %s", total, config.data.parquet_root)
    return total


async def _scarica_flusso(
    broker: BinanceBroker,
    store: ParquetStore,
    symbols: list[str],
    *,
    giorni: int,
    fine_ms: int,
) -> int:
    """Scarica il flusso ordini aggregato degli ultimi giorni.

    E' molto piu' voluminoso delle candele (BTCUSDT fa centinaia di
    migliaia di trade al giorno), e per questo si scarica una finestra
    corta e separata. Senza di esso pero' il backtest gira con l'order
    flow spento, che e' meta' di cio' che guarda la strategia dal vivo.
    """
    inizio_ms = fine_ms - giorni * 86_400_000
    totale = 0
    for symbol in symbols:
        trade = await broker.agg_trades(symbol, start_ms=inizio_ms, limit=1000)
        while trade:
            dentro = [t for t in trade if t.ts_ms < fine_ms]
            store.add_trades(dentro)
            totale += len(dentro)
            if len(dentro) < len(trade) or len(trade) < 1000:
                break
            logger.info("%s: %d trade fino a %s", symbol, totale, trade[-1].ts_ms)
            trade = await broker.agg_trades(
                symbol, from_id=trade[-1].trade_id + 1, limit=1000
            )
    return totale


def main() -> int:
    parser = argparse.ArgumentParser(description="Scarica candele storiche da Binance")
    parser.add_argument("--days", type=int, default=30, help="giorni di storia da scaricare")
    parser.add_argument("--symbols", nargs="*", help="simboli (default: quelli abilitati)")
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument(
        "--giorni-flusso",
        type=int,
        default=0,
        dest="trade_days",
        help="giorni di flusso ordini (aggTrades) da scaricare: molto piu' "
        "voluminoso delle candele, ma senza di esso il backtest e' cieco "
        "su meta' degli ingressi",
    )
    args = parser.parse_args()

    load_dotenv()
    setup_logging(level="INFO", json_logs=False)
    config = load_config(args.config)
    symbols = args.symbols or [s.symbol for s in config.enabled_symbols]

    total = asyncio.run(
        fetch(symbols, args.days, args.config, trade_days=args.trade_days)
    )
    print(f"scaricate {total} candele in {config.data.parquet_root}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
