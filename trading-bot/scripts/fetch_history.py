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
from collections.abc import Callable
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

#: Tetto agli scambi scaricati per coppia. Duecentomila bastano a
#: misurare l'order flow su qualche ora; il giorno intero di BTCUSDT ne
#: ha milioni, e scaricarli tutti vuol dire aspettare mezz'ora guardando
#: una finestra che sembra ferma.
MAX_TRADE_PER_SIMBOLO = 200_000



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
    symbols: list[str],
    days: int,
    config_path: Path,
    *,
    trade_hours: int = 0,
    symbols_flusso: list[str] | None = None,
    progresso: Callable[[str], None] | None = None,
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
                if progresso is not None:
                    progresso(f"{symbol}: {total} candele")
                cursor = int(str(rows[-1][0])) + step_ms
                logger.info("%s: %d candele fino a %s", symbol, total, cursor)
        if trade_hours > 0:
            # Il flusso ordini solo per le coppie che il bot usa davvero:
            # per le altre bastano le candele a fare il confronto, e
            # scaricare milioni di scambi che non verranno usati e' il
            # modo piu' rapido di far sembrare il programma bloccato.
            totale_trade = await _scarica_flusso(
                broker,
                store,
                symbols_flusso or symbols,
                ore=trade_hours,
                fine_ms=end_ms,
                progresso=progresso,
            )
            logger.info("scaricati %d scambi (flusso ordini)", totale_trade)
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
    ore: int,
    fine_ms: int,
    massimo_per_simbolo: int = MAX_TRADE_PER_SIMBOLO,
    progresso: Callable[[str], None] | None = None,
) -> int:
    """Scarica il flusso ordini aggregato delle ultime ore.

    Serve un tetto, e non e' una precauzione teorica: BTCUSDT fa qualche
    milione di scambi al giorno, che a mille per richiesta sono migliaia
    di chiamate. Con il throttle lato client diventano decine di minuti,
    e dalla finestra il programma sembra bloccato. Meglio poche ore
    complete che un'attesa senza fine, e il tetto viene dichiarato invece
    che subito.
    """
    inizio_ms = fine_ms - ore * 3_600_000
    totale = 0
    for symbol in symbols:
        presi = 0
        trade = await broker.agg_trades(symbol, start_ms=inizio_ms, limit=1000)
        while trade:
            dentro = [t for t in trade if t.ts_ms < fine_ms]
            if presi + len(dentro) > massimo_per_simbolo:
                dentro = dentro[: massimo_per_simbolo - presi]
            store.add_trades(dentro)
            presi += len(dentro)
            totale += len(dentro)
            if progresso is not None:
                progresso(f"{symbol}: {presi} scambi")
            logger.info("%s: %d scambi scaricati", symbol, presi)
            if presi >= massimo_per_simbolo:
                logger.info(
                    "%s: raggiunto il tetto di %d scambi, il resto non si scarica "
                    "(bastano per misurare l'order flow)",
                    symbol,
                    massimo_per_simbolo,
                )
                break
            if len(dentro) < len(trade) or len(trade) < 1000:
                break
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
        "--ore-flusso",
        type=int,
        default=0,
        dest="trade_hours",
        help="ore di flusso ordini (aggTrades) da scaricare: molto piu' "
        "voluminoso delle candele, ma senza di esso il backtest e' cieco "
        f"su meta' degli ingressi. Tetto di {MAX_TRADE_PER_SIMBOLO} scambi "
        "per coppia.",
    )
    args = parser.parse_args()

    load_dotenv()
    setup_logging(level="INFO", json_logs=False)
    config = load_config(args.config)
    symbols = args.symbols or [s.symbol for s in config.enabled_symbols]

    total = asyncio.run(
        fetch(symbols, args.days, args.config, trade_hours=args.trade_hours)
    )
    print(f"scaricate {total} candele in {config.data.parquet_root}")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
