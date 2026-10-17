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
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.clock import Clock
from core.portfolio import Portfolio
from core.types import TimestampMs, ms_to_datetime
from monitoring.logger import to_jsonable
from risk.killswitch import KillSwitch

__all__ = ["BotState", "StateStore"]

logger = logging.getLogger("trading_bot.state")

#: Versione 3: aggiunge l'istante dello stop, senza il quale il kill
#: switch non puo' essere ripristinato con il suo periodo di attesa.
#: Versione 2: aggiunge le monete gia' presenti sul conto (baseline). Uno
#: stato di versione 1 non la contiene, e senza di essa non si puo' dire
#: quali monete appartengano al bot: viene scartato e si riparte dai saldi.
STATE_VERSION = 4


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
    #: Istante in cui il kill switch e' scattato: serve a far ripartire il
    #: periodo di attesa da dove era, non da capo.
    killswitch_tripped_at_ms: TimestampMs | None = None
    #: Monete presenti sul conto prima che il bot iniziasse: non sono sue.
    baseline_holdings: dict[str, str] = field(default_factory=dict)
    #: Stop e target delle posizioni aperte, per simbolo. Un riavvio che li
    #: perdesse lascerebbe la posizione senza rete: nessuno la chiuderebbe
    #: piu' al raggiungimento del piano con cui e' stata aperta.
    exit_plans: dict[str, dict[str, str]] = field(default_factory=dict)
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
        if type(payload["killswitch_tripped"]) is not bool:
            raise ValueError("stato kill switch non booleano")
        saved_at = payload["saved_at_ms"]
        if type(saved_at) is not int or saved_at < 0:
            raise ValueError("stato con timestamp non valido")
        tripped_at = payload.get("killswitch_tripped_at_ms")
        if tripped_at is not None and (
            type(tripped_at) is not int or tripped_at < 0 or tripped_at > saved_at
        ):
            raise ValueError("stato con timestamp kill switch non valido")
        if payload["killswitch_tripped"] != (tripped_at is not None):
            raise ValueError("stato kill switch incoerente con il timestamp")
        extra = payload.get("extra", {})
        if not isinstance(extra, dict):
            raise ValueError("stato extra non valido")
        if "risk_day_key" in extra:
            key = extra["risk_day_key"]
            if not isinstance(key, str):
                raise ValueError("stato con giorno di rischio non valido")
            risk_day = date.fromisoformat(key)
            if risk_day.isoformat() != key or risk_day > ms_to_datetime(saved_at).date():
                raise ValueError("stato con giorno di rischio non valido")
        for name in ("day_start_equity", "peak_equity"):
            value = Decimal(str(payload[name]))
            if not value.is_finite() or value < 0:
                raise ValueError(f"stato con {name} non valido")
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
            killswitch_tripped_at_ms=(
                int(payload["killswitch_tripped_at_ms"])
                if payload.get("killswitch_tripped_at_ms") is not None
                else None
            ),
            baseline_holdings={
                str(k): str(v) for k, v in dict(payload.get("baseline_holdings", {})).items()
            },
            exit_plans={
                str(symbol): {str(k): str(v) for k, v in dict(piano).items()}
                for symbol, piano in dict(payload.get("exit_plans", {})).items()
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
        exit_plans: dict[str, dict[str, str]] | None = None,
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
            killswitch_tripped_at_ms=killswitch.tripped_at_ms,
            baseline_holdings={
                symbol: str(quantity)
                for symbol, quantity in (baseline_holdings or {}).items()
            },
            exit_plans=dict(exit_plans or {}),
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

    def load(self, *, strict: bool = False) -> BotState | None:
        """Rilegge lo stato; il runner usa ``strict=True`` per le protezioni.

        Un file assente restituisce None. Un file esistente ma illeggibile
        blocca il runner: i saldi exchange non ricostruiscono halt, massimo
        storico e piani di uscita. I lettori diagnostici possono usare la
        modalita' non strict, che registra l'errore e restituisce None.
        """
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("stato locale non e' un oggetto")
            return BotState.from_dict(payload)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            if strict:
                raise ValueError(
                    "stato locale illeggibile: ripristinare le protezioni prima dell'avvio"
                ) from exc
            logger.error("stato locale illeggibile (%s): si riparte dall'exchange", exc)
            return None
