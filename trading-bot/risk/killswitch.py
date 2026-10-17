"""Kill switch: le condizioni per cui il bot smette di operare.

Non e' un limite come gli altri. Gli altri rifiutano un ordine, questo ferma
il sistema: una volta scattato nessun ordine di apertura passa piu', e se
configurato chiude tutte le posizioni a mercato.

Il riarmo automatico e' disattivato per default: dopo uno stop deve guardare
i log un essere umano. Un bot che si riarma da solo ripete l'errore che lo ha
fermato, piu' in fretta.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from config.schema import KillSwitchConfig
from core.portfolio import PortfolioSnapshot
from core.types import TimestampMs

__all__ = ["KillSwitch", "KillSwitchEvent", "TriggerReason"]


class TriggerReason(StrEnum):
    DAILY_DRAWDOWN = "DAILY_DRAWDOWN"
    TOTAL_DRAWDOWN = "TOTAL_DRAWDOWN"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    ORDER_LATENCY = "ORDER_LATENCY"
    DATA_STALENESS = "DATA_STALENESS"
    RECONNECT_STORM = "RECONNECT_STORM"
    MANUAL = "MANUAL"
    #: Incoerenza fra stato locale e stato sull'exchange (cfr. reconcile).
    STATE_MISMATCH = "STATE_MISMATCH"


@dataclass(frozen=True, slots=True)
class KillSwitchEvent:
    reason: TriggerReason
    detail: str
    ts_ms: TimestampMs
    observed: float
    threshold: float

    def describe(self) -> str:
        return (
            f"kill switch {self.reason.value}: {self.detail} "
            f"(osservato {self.observed:.4f}, soglia {self.threshold:.4f})"
        )


@dataclass
class KillSwitch:
    """Macchina a stati del blocco operativo.

    Ha una sola transizione automatica: armato -> scattato. Il ritorno ad
    armato passa da ``reset()``, che un operatore chiama consapevolmente, o
    dalla scadenza del cooldown solo se ``auto_resume`` e' attivo.
    """

    config: KillSwitchConfig
    events: list[KillSwitchEvent] = field(default_factory=list)
    tripped_at_ms: TimestampMs | None = None
    _reconnects: deque[TimestampMs] = field(default_factory=deque, repr=False)
    _last_data_ms: dict[str, TimestampMs] = field(default_factory=dict, repr=False)
    _flatten_pending: bool = False

    # -- stato ------------------------------------------------------------

    @property
    def is_tripped(self) -> bool:
        return self.tripped_at_ms is not None

    @property
    def last_event(self) -> KillSwitchEvent | None:
        return self.events[-1] if self.events else None

    @property
    def flatten_pending(self) -> bool:
        """Vero finche' la chiusura forzata non e' stata eseguita."""
        return self._flatten_pending

    def mark_flattened(self) -> None:
        self._flatten_pending = False

    def allows_opening(self, now_ms: TimestampMs) -> bool:
        """Se si possono ancora aprire posizioni."""
        if not self.is_tripped:
            return True
        return self._cooldown_expired(now_ms) and self.config.auto_resume

    def reset(self, now_ms: TimestampMs) -> bool:
        """Riarmo manuale. Rifiutato finche' il cooldown non e' scaduto."""
        if not self.is_tripped:
            return True
        if not self._cooldown_expired(now_ms):
            return False
        self.tripped_at_ms = None
        self._flatten_pending = False
        return True

    # -- controlli --------------------------------------------------------

    def record_reconnect(self, ts_ms: TimestampMs) -> None:
        self._reconnects.append(ts_ms)

    def record_data(self, symbol: str, ts_ms: TimestampMs) -> None:
        self._last_data_ms[symbol] = ts_ms

    def check(
        self,
        snapshot: PortfolioSnapshot,
        *,
        now_ms: TimestampMs,
        last_order_latency_ms: float | None = None,
    ) -> tuple[KillSwitchEvent, ...]:
        """Valuta tutte le condizioni. Registra e restituisce quelle scattate.

        Le valuta tutte anche se la prima e' gia' sufficiente: in sede di
        autopsia serve sapere quante cose sono andate storte insieme.
        """
        triggered: list[KillSwitchEvent] = []

        daily = snapshot.daily_drawdown_pct
        if daily >= self.config.max_daily_drawdown_pct:
            triggered.append(
                KillSwitchEvent(
                    TriggerReason.DAILY_DRAWDOWN,
                    f"drawdown giornaliero {daily:.2%} dall'equity di inizio giornata "
                    f"({snapshot.day_start_equity})",
                    now_ms,
                    daily,
                    self.config.max_daily_drawdown_pct,
                )
            )

        total = snapshot.total_drawdown_pct
        if total >= self.config.max_total_drawdown_pct:
            triggered.append(
                KillSwitchEvent(
                    TriggerReason.TOTAL_DRAWDOWN,
                    f"drawdown totale {total:.2%} dal massimo storico "
                    f"({snapshot.peak_equity})",
                    now_ms,
                    total,
                    self.config.max_total_drawdown_pct,
                )
            )

        losses = snapshot.consecutive_losses
        if losses >= self.config.max_consecutive_losses:
            triggered.append(
                KillSwitchEvent(
                    TriggerReason.CONSECUTIVE_LOSSES,
                    f"{losses} perdite consecutive",
                    now_ms,
                    float(losses),
                    float(self.config.max_consecutive_losses),
                )
            )

        if last_order_latency_ms is not None and (
            last_order_latency_ms >= self.config.max_order_latency_ms
        ):
            triggered.append(
                KillSwitchEvent(
                    TriggerReason.ORDER_LATENCY,
                    f"latenza ordine {last_order_latency_ms:.0f} ms: l'exchange "
                    "non risponde in tempo utile",
                    now_ms,
                    last_order_latency_ms,
                    float(self.config.max_order_latency_ms),
                )
            )

        for symbol, last_ms in self._last_data_ms.items():
            age = now_ms - last_ms
            if age >= self.config.max_data_staleness_ms:
                triggered.append(
                    KillSwitchEvent(
                        TriggerReason.DATA_STALENESS,
                        f"nessun dato su {symbol} da {age} ms: si starebbe "
                        "decidendo su un prezzo fermo",
                        now_ms,
                        float(age),
                        float(self.config.max_data_staleness_ms),
                    )
                )

        cutoff = now_ms - 3_600_000
        while self._reconnects and self._reconnects[0] < cutoff:
            self._reconnects.popleft()
        if len(self._reconnects) >= self.config.max_reconnects_per_hour:
            triggered.append(
                KillSwitchEvent(
                    TriggerReason.RECONNECT_STORM,
                    f"{len(self._reconnects)} riconnessioni nell'ultima ora: "
                    "connessione inaffidabile",
                    now_ms,
                    float(len(self._reconnects)),
                    float(self.config.max_reconnects_per_hour),
                )
            )

        for event in triggered:
            self._trip(event)
        return tuple(triggered)

    def trip_manually(self, detail: str, now_ms: TimestampMs) -> KillSwitchEvent:
        """Arresto richiesto da un operatore o da un componente esterno."""
        event = KillSwitchEvent(TriggerReason.MANUAL, detail, now_ms, 1.0, 1.0)
        self._trip(event)
        return event

    def trip_on_state_mismatch(self, detail: str, now_ms: TimestampMs) -> KillSwitchEvent:
        """Stato locale divergente da quello dell'exchange.

        E' la condizione piu' pericolosa: operare su una posizione che non si
        sa quanto sia grande e' peggio che non operare.
        """
        event = KillSwitchEvent(TriggerReason.STATE_MISMATCH, detail, now_ms, 1.0, 1.0)
        self._trip(event)
        return event

    # -- interno ----------------------------------------------------------

    def _trip(self, event: KillSwitchEvent) -> None:
        self.events.append(event)
        if self.tripped_at_ms is None:
            self.tripped_at_ms = event.ts_ms
            self._flatten_pending = self.config.flatten_positions_on_trigger

    def _cooldown_expired(self, now_ms: TimestampMs) -> bool:
        if self.tripped_at_ms is None:
            return True
        return now_ms - self.tripped_at_ms >= self.config.cooldown_minutes * 60_000
