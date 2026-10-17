"""Persistenza su Parquet partizionato per giorno e simbolo.

Partizionare per ``date=YYYY-MM-DD/symbol=XXX`` serve a due cose: una query di
backtest su una finestra legge solo i file di quella finestra, e la retention
si applica cancellando directory invece di riscrivere tabelle.

La scrittura e' a lotti: un file Parquet per ogni manciata di migliaia di
eventi. Scrivere una riga alla volta produrrebbe decine di migliaia di file
minuscoli, che poi sono piu' lenti da leggere dei dati stessi.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.types import Candle, TimestampMs, Trade, ms_to_datetime

__all__ = ["ParquetStore", "day_partition"]

logger = logging.getLogger("trading_bot.store")


def day_partition(ts_ms: TimestampMs) -> str:
    """Chiave di partizione giornaliera in UTC.

    Sempre UTC: partizionare sull'ora locale sposta il confine del giorno
    quando cambia l'ora legale, e un backtest sulle stesse date leggerebbe
    dati diversi a seconda di dove gira.
    """
    return ms_to_datetime(ts_ms).strftime("%Y-%m-%d")


def _trade_row(trade: Trade) -> dict[str, Any]:
    return {
        "ts_ms": trade.ts_ms,
        "symbol": trade.symbol,
        "trade_id": trade.trade_id,
        "price": str(trade.price),
        "quantity": str(trade.quantity),
        "is_buyer_maker": trade.is_buyer_maker,
    }


def _candle_row(candle: Candle) -> dict[str, Any]:
    return {
        "open_time_ms": candle.open_time_ms,
        "close_time_ms": candle.close_time_ms,
        "symbol": candle.symbol,
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
        "quote_volume": str(candle.quote_volume),
        "trades": candle.trades,
        "is_closed": candle.is_closed,
    }


@dataclass
class ParquetStore:
    """Scrittore a lotti e lettore delle partizioni Parquet.

    I prezzi sono salvati come stringhe, non come float: un Decimal che passa
    per float e torna indietro non e' piu' lo stesso numero, e su un backtest
    di mesi la differenza si accumula.
    """

    root: Path
    batch_size: int = 5_000
    _buffers: dict[str, list[dict[str, Any]]] = field(default_factory=dict, repr=False)
    files_written: int = 0
    rows_written: int = 0

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # -- scrittura --------------------------------------------------------

    def add_trade(self, trade: Trade) -> None:
        self._add("trades", trade.symbol, trade.ts_ms, _trade_row(trade))

    def add_candle(self, candle: Candle) -> None:
        """Solo le candele chiuse vanno su disco.

        Una candela in formazione verrebbe riletta dal backtest come se fosse
        definitiva: e' esattamente il look-ahead che il progetto vieta.
        """
        if not candle.is_closed:
            return
        self._add("candles", candle.symbol, candle.close_time_ms, _candle_row(candle))

    def add_trades(self, trades: Iterable[Trade]) -> None:
        for trade in trades:
            self.add_trade(trade)

    def add_candles(self, candles: Iterable[Candle]) -> None:
        for candle in candles:
            self.add_candle(candle)

    def flush(self) -> int:
        """Scrive su disco tutti i lotti in sospeso. Restituisce i file scritti."""
        written = 0
        for key in list(self._buffers):
            if self._flush_key(key):
                written += 1
        return written

    def _add(self, dataset: str, symbol: str, ts_ms: TimestampMs, row: dict[str, Any]) -> None:
        key = f"{dataset}|{day_partition(ts_ms)}|{symbol}"
        buffer = self._buffers.setdefault(key, [])
        buffer.append(row)
        if len(buffer) >= self.batch_size:
            self._flush_key(key)

    def _flush_key(self, key: str) -> bool:
        rows = self._buffers.pop(key, [])
        if not rows:
            return False
        import polars as pl

        dataset, day, symbol = key.split("|")
        directory = self.root / dataset / f"date={day}" / f"symbol={symbol}"
        directory.mkdir(parents=True, exist_ok=True)
        # Il nome include un timestamp di scrittura: due lotti dello stesso
        # giorno non si sovrascrivono a vicenda.
        stamp = datetime.now(tz=UTC).strftime("%H%M%S%f")
        path = directory / f"part-{stamp}-{len(rows)}.parquet"
        pl.DataFrame(rows).write_parquet(path)
        self.files_written += 1
        self.rows_written += len(rows)
        logger.debug("scritte %d righe in %s", len(rows), path)
        return True

    # -- lettura ----------------------------------------------------------

    def partitions(self, dataset: str) -> list[Path]:
        base = self.root / dataset
        if not base.is_dir():
            return []
        return sorted(base.glob("date=*/symbol=*/*.parquet"))

    def read(
        self,
        dataset: str,
        *,
        symbols: Sequence[str] | None = None,
        start_ms: TimestampMs | None = None,
        end_ms: TimestampMs | None = None,
    ) -> Any:
        """Rilegge un dataset come DataFrame polars, filtrato per simbolo e finestra.

        I prezzi tornano come ``Decimal``: la conversione avviene qui, una
        volta sola, cosi' il resto del sistema non vede mai stringhe.
        """
        import polars as pl

        files = self.partitions(dataset)
        if symbols:
            wanted = {s.upper() for s in symbols}
            files = [f for f in files if f.parent.name.removeprefix("symbol=") in wanted]
        if not files:
            return pl.DataFrame()

        frame = pl.concat([pl.read_parquet(f) for f in files], how="vertical_relaxed")
        time_column = "ts_ms" if "ts_ms" in frame.columns else "close_time_ms"
        if start_ms is not None:
            frame = frame.filter(pl.col(time_column) >= start_ms)
        if end_ms is not None:
            frame = frame.filter(pl.col(time_column) < end_ms)
        return frame.sort(time_column)

    def read_candles(
        self,
        *,
        symbols: Sequence[str] | None = None,
        start_ms: TimestampMs | None = None,
        end_ms: TimestampMs | None = None,
    ) -> list[Candle]:
        """Candele ordinate nel tempo, pronte per il backtest event-driven."""
        frame = self.read("candles", symbols=symbols, start_ms=start_ms, end_ms=end_ms)
        if frame.is_empty():
            return []
        return [
            Candle(
                symbol=row["symbol"],
                open_time_ms=row["open_time_ms"],
                close_time_ms=row["close_time_ms"],
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                quote_volume=Decimal(row["quote_volume"]),
                trades=row["trades"],
                is_closed=row["is_closed"],
            )
            for row in frame.iter_rows(named=True)
        ]

    def purge_older_than(self, cutoff_ms: TimestampMs) -> int:
        """Cancella le partizioni anteriori al taglio. Restituisce i file rimossi."""
        cutoff_day = day_partition(cutoff_ms)
        removed = 0
        for dataset_dir in self.root.glob("*"):
            for day_dir in dataset_dir.glob("date=*"):
                if day_dir.name.removeprefix("date=") >= cutoff_day:
                    continue
                for path in sorted(day_dir.rglob("*"), reverse=True):
                    if path.is_file():
                        path.unlink()
                        removed += 1
                    else:
                        path.rmdir()
                day_dir.rmdir()
        return removed
