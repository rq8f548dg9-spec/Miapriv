"""Log strutturato JSON: una riga per decisione.

Principio 4 del progetto. Il log non serve a sapere che il bot "sta
girando": serve a rispondere, mesi dopo, alla domanda "perche' il 14 marzo
alle 03:12 ha comprato?". Per rispondere servono nella stessa riga: il
segnale, tutte le feature che lo hanno generato, lo stato di rischio del
momento e l'esito.

Formato JSON Lines: una riga per evento, nessun parser da scrivere, leggibile
con ``jq`` o direttamente da polars e DuckDB per l'analisi a posteriori.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from core.types import Decision, Fill, Order, TimestampMs, ms_to_datetime

__all__ = ["DecisionLogger", "JsonFormatter", "setup_logging", "to_jsonable"]


def to_jsonable(value: Any) -> Any:
    """Rende serializzabile qualunque oggetto di dominio.

    I ``Decimal`` diventano stringhe e non float: un prezzo che passa per
    float e torna indietro non e' piu' lo stesso numero, e un log che
    falsifica i numeri che registra e' peggio di nessun log.
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    return str(value)


class JsonFormatter(logging.Formatter):
    """Formatter che emette una riga JSON per record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, Mapping):
            payload.update(to_jsonable(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    *, level: str = "INFO", json_logs: bool = True, log_file: Path | None = None
) -> None:
    """Configura il logging del processo. Da chiamare una sola volta, all'avvio."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter: logging.Formatter = (
        JsonFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)


class DecisionLogger:
    """Scrive la riga di autopsia di ogni decisione, approvata o rifiutata.

    Anche i rifiuti vengono scritti, e sono anzi i piu' istruttivi: un bot
    che non opera e di cui non si sa perche' e' un bot che non si puo'
    correggere.
    """

    def __init__(self, logger: logging.Logger | None = None, *, mode: str = "unknown") -> None:
        self._logger = logger or logging.getLogger("trading_bot.decisions")
        self._mode = mode
        self.decisions_logged = 0

    def log_decision(self, decision: Decision) -> dict[str, Any]:
        """Registra una decisione completa e restituisce il payload scritto."""
        signal = decision.signal
        payload: dict[str, Any] = {
            "event": "decision",
            "mode": decision.mode or self._mode,
            "ts_ms": decision.ts_ms,
            "ts_iso": ms_to_datetime(decision.ts_ms).isoformat(),
            "symbol": decision.symbol,
            "approved": decision.approved,
            "rejection_reasons": list(decision.rejection_reasons),
            "risk": to_jsonable(decision.risk_snapshot),
        }
        if signal is not None:
            payload["signal"] = {
                "direction": signal.direction.value,
                "probability": signal.probability,
                "edge": signal.edge,
                "generator": signal.generator,
                "horizon_ms": signal.horizon_ms,
                "metadata": to_jsonable(signal.metadata),
            }
            # Le feature per intero: senza di esse l'autopsia e' impossibile.
            payload["features"] = dict(signal.features)
        if decision.order is not None:
            payload["order"] = {
                "client_order_id": decision.order.client_order_id,
                "side": decision.order.side.value,
                "type": decision.order.order_type.value,
                "quantity": str(decision.order.quantity),
                "reduce_only": decision.order.reduce_only,
            }

        self._logger.info(
            "decisione %s su %s",
            "approvata" if decision.approved else "rifiutata",
            decision.symbol,
            extra={"context": payload},
        )
        self.decisions_logged += 1
        return payload

    def log_fill(self, fill: Fill, order: Order | None = None) -> None:
        self._logger.info(
            "fill %s %s %s @ %s",
            fill.symbol,
            fill.side.value,
            fill.quantity,
            fill.price,
            extra={
                "context": {
                    "event": "fill",
                    "mode": self._mode,
                    "ts_ms": fill.ts_ms,
                    "symbol": fill.symbol,
                    "side": fill.side.value,
                    "price": str(fill.price),
                    "quantity": str(fill.quantity),
                    "fee": str(fill.fee),
                    "fee_asset": fill.fee_asset,
                    "is_maker": fill.is_maker,
                    "client_order_id": fill.client_order_id,
                    "intent": None if order is None else order.metadata.get("intent"),
                }
            },
        )

    def log_equity(self, ts_ms: TimestampMs, equity: Decimal, **extra: Any) -> None:
        self._logger.info(
            "equity %s",
            equity,
            extra={
                "context": {
                    "event": "equity",
                    "mode": self._mode,
                    "ts_ms": ts_ms,
                    "equity": str(equity),
                    **to_jsonable(extra),
                }
            },
        )
