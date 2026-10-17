"""Snapshot periodico dello stato del bot su disco.

Serve a due cose diverse, e vale la pena tenerle distinte:

* **osservabilita'**: sapere cosa sta facendo il bot senza interrogarlo;
* **ripartenza informata**: al riavvio il file dice cosa il bot credeva,
  cosi' la riconciliazione sa cosa confrontare con l'exchange.

Quello che il file NON e': la fonte della verita' sulle posizioni. Quella
sta sull'exchange, sempre (cfr. ``execution/reconcile.py``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.clock import Clock
from core.portfolio import Portfolio
from core.types import TimestampMs
from monitoring.logger import to_jsonable
from risk.killswitch import KillSwitch

__all__ = ["BotState", "StateStore"]

logger = logging.getLogger("trading_bot.state")

#: Versione 2: aggiunge le monete gia' presenti sul conto (baseline). Uno
#: stato di versione 1 non la contiene, e senza di essa non si puo' dire
#: quali monete appartengano al bot: viene scartato e si riparte dai saldi.
STATE_VERSION = 2


@dataclass(frozen=True, slots=True)
class BotState:
    """Fotografia serializzabile del bot."""

    version: int
    saved_at_ms: TimestampMs
    mode: str
    equity: Decimal
    cash: Decimal
    realized_pnl: Decimal
    fees_paid: Decimal
    positions: dict[str, str]
    peak_equity: Decimal
    day_start_equity: Decimal
    consecutive_losses: int
    killswitch_tripped: bool
    killswitch_reason: str | None
    #: Monete presenti sul conto prima che il bot iniziasse: non sono sue.
    baseline_holdings: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        # slots=True: niente __dict__, si passa da asdict della dataclass.
        return {k: to_jsonable(v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BotState:
        version = int(payload.get("version", 0))
        if version != STATE_VERSION:
            raise ValueError(
                f"stato salvato con versione {version}, attesa {STATE_VERSION}: "
                "il formato e' cambiato, lo stato va ricostruito dall'exchange"
            )
        return cls(
            version=version,
            saved_at_ms=int(payload["saved_at_ms"]),
            mode=str(payload["mode"]),
            equity=Decimal(str(payload["equity"])),
            cash=Decimal(str(payload["cash"])),
            realized_pnl=Decimal(str(payload["realized_pnl"])),
            fees_paid=Decimal(str(payload["fees_paid"])),
            positions={k: str(v) for k, v in dict(payload.get("positions", {})).items()},
            peak_equity=Decimal(str(payload["peak_equity"])),
            day_start_equity=Decimal(str(payload["day_start_equity"])),
            consecutive_losses=int(payload["consecutive_losses"]),
            killswitch_tripped=bool(payload["killswitch_tripped"]),
            killswitch_reason=payload.get("killswitch_reason"),
            baseline_holdings={
                str(k): str(v) for k, v in dict(payload.get("baseline_holdings", {})).items()
            },
            extra=dict(payload.get("extra", {})),
        )


@dataclass
class StateStore:
    """Scrive e rilegge lo stato, con scrittura atomica e cadenza controllata."""

    path: Path
    clock: Clock
    interval_s: int = 60
    mode: str = "unknown"
    last_saved_ms: TimestampMs = 0
    saves: int = 0

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    def capture(
        self,
        portfolio: Portfolio,
        killswitch: KillSwitch,
        *,
        baseline_holdings: dict[str, Decimal] | None = None,
        **extra: Any,
    ) -> BotState:
        last_event = killswitch.last_event
        return BotState(
            version=STATE_VERSION,
            saved_at_ms=self.clock.now_ms(),
            mode=self.mode,
            equity=portfolio.equity,
            cash=portfolio.cash,
            realized_pnl=portfolio.realized_pnl,
            fees_paid=portfolio.fees_paid,
            positions={
                symbol: str(position.quantity)
                for symbol, position in portfolio.positions.items()
                if not position.is_flat
            },
            peak_equity=portfolio.peak_equity,
            day_start_equity=portfolio.day_start_equity,
            consecutive_losses=portfolio.consecutive_losses,
            killswitch_tripped=killswitch.is_tripped,
            killswitch_reason=last_event.describe() if last_event else None,
            baseline_holdings={
                symbol: str(quantity)
                for symbol, quantity in (baseline_holdings or {}).items()
            },
            extra=dict(extra),
        )

    def should_save(self) -> bool:
        return self.clock.now_ms() - self.last_saved_ms >= self.interval_s * 1000

    def save(self, state: BotState, *, force: bool = False) -> bool:
        """Salva se e' passato abbastanza tempo. ``force`` ignora la cadenza."""
        if not force and not self.should_save():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.path)
        self.last_saved_ms = state.saved_at_ms
        self.saves += 1
        return True

    def load(self) -> BotState | None:
        """Rilegge lo stato. ``None`` se non esiste o se e' illeggibile.

        Un file corrotto non blocca l'avvio: lo stato vero si ricostruisce
        comunque dall'exchange. Viene pero' segnalato come errore, perche'
        significa che l'ultimo salvataggio e' stato interrotto.
        """
        if not self.path.is_file():
            return None
        try:
            return BotState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("stato locale illeggibile (%s): si riparte dall'exchange", exc)
            return None
