"""Notifiche su kill switch ed errori critici.

Un alert serve a far arrivare a una persona un fatto su cui il bot non puo'
decidere da solo. Due proprieta' lo rendono utile invece che rumoroso:

* **deduplica**: lo stesso allarme ripetuto ogni secondo smette di essere
  letto dopo il terzo. Un alert gia' inviato di recente viene soppresso e
  conteggiato;
* **nessun segreto**: i canali esterni ricevono testo, mai chiavi API ne'
  saldi di conti diversi da quello in uso.

I canali sono iniettabili: il canale di default scrive nel log, cosi' il bot
funziona anche senza nessuna integrazione configurata.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Protocol

from core.clock import Clock
from core.types import TimestampMs
from risk.killswitch import KillSwitchEvent

__all__ = ["Alert", "AlertChannel", "AlertManager", "FileChannel", "LogChannel", "Severity"]

logger = logging.getLogger("trading_bot.alerts")


class Severity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class Alert:
    key: str
    severity: Severity
    title: str
    detail: str
    ts_ms: TimestampMs

    def render(self) -> str:
        return f"[{self.severity.label}] {self.title} - {self.detail}"


class AlertChannel(Protocol):
    """Destinazione di un alert."""

    name: str

    def send(self, alert: Alert) -> None: ...


@dataclass
class LogChannel:
    """Canale di default: scrive nel log strutturato."""

    name: str = "log"

    def send(self, alert: Alert) -> None:
        level = {
            Severity.INFO: logging.INFO,
            Severity.WARNING: logging.WARNING,
            Severity.CRITICAL: logging.CRITICAL,
        }[alert.severity]
        logger.log(
            level,
            "%s",
            alert.render(),
            extra={
                "context": {
                    "event": "alert",
                    "key": alert.key,
                    "severity": alert.severity.label,
                    "ts_ms": alert.ts_ms,
                    "title": alert.title,
                    "detail": alert.detail,
                }
            },
        )


@dataclass
class FileChannel:
    """Appende gli alert a un file di testo, uno per riga."""

    path: Path
    name: str = "file"

    def send(self, alert: Alert) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{alert.ts_ms}\t{alert.render()}\n")


@dataclass
class CallbackChannel:
    """Canale generico per integrazioni esterne (Telegram, email, webhook).

    Riceve una funzione che invia il testo. Tenerla iniettata evita di
    mettere qui dentro credenziali e client HTTP di terze parti: il
    trasporto si configura all'avvio, questo modulo resta puro.
    """

    sender: Callable[[str], None]
    name: str = "callback"
    min_severity: Severity = Severity.WARNING
    failures: int = 0

    def send(self, alert: Alert) -> None:
        if alert.severity < self.min_severity:
            return
        try:
            self.sender(alert.render())
        except Exception as exc:
            self.failures += 1
            logger.error("invio alert fallito sul canale %s: %s", self.name, exc)


@dataclass
class AlertManager:
    """Smista gli alert ai canali, con deduplica temporale."""

    clock: Clock
    channels: list[AlertChannel] = field(default_factory=list)
    enabled: bool = True
    #: Finestra entro cui lo stesso allarme non viene ripetuto.
    dedup_window_s: int = 300
    sent: int = 0
    suppressed: int = 0
    history: list[Alert] = field(default_factory=list)
    _last_sent_ms: dict[str, TimestampMs] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.channels:
            self.channels = [LogChannel()]

    def notify(
        self, key: str, title: str, detail: str, severity: Severity = Severity.WARNING
    ) -> bool:
        """Invia un alert. ``False`` se soppresso come duplicato o se disattivato."""
        if not self.enabled:
            return False

        now_ms = self.clock.now_ms()
        last = self._last_sent_ms.get(key)
        if last is not None and now_ms - last < self.dedup_window_s * 1000:
            self.suppressed += 1
            return False

        alert = Alert(key=key, severity=severity, title=title, detail=detail, ts_ms=now_ms)
        self._last_sent_ms[key] = now_ms
        self.history.append(alert)
        self.sent += 1
        for channel in self.channels:
            channel.send(alert)
        return True

    # -- scorciatoie per gli eventi che contano davvero --------------------

    def killswitch_triggered(self, event: KillSwitchEvent) -> bool:
        """Il bot ha smesso di operare: e' sempre un alert critico."""
        return self.notify(
            key=f"killswitch:{event.reason.value}",
            title="KILL SWITCH ATTIVATO - il bot non apre piu' posizioni",
            detail=event.describe(),
            severity=Severity.CRITICAL,
        )

    def reconciliation_failed(self, description: str) -> bool:
        return self.notify(
            key="reconcile:mismatch",
            title="STATO LOCALE DIVERGENTE DALL'EXCHANGE",
            detail=description,
            severity=Severity.CRITICAL,
        )

    def connection_lost(self, detail: str) -> bool:
        return self.notify(
            key="connection:lost",
            title="connessione al flusso dati persa",
            detail=detail,
            severity=Severity.WARNING,
        )

    def order_rejected(self, client_order_id: str, reason: str) -> bool:
        return self.notify(
            key=f"order:rejected:{client_order_id}",
            title="ordine rifiutato dall'exchange",
            detail=f"{client_order_id}: {reason}",
            severity=Severity.WARNING,
        )

    def critical_error(self, where: str, error: BaseException) -> bool:
        return self.notify(
            key=f"error:{where}",
            title=f"errore critico in {where}",
            detail=f"{type(error).__name__}: {error}",
            severity=Severity.CRITICAL,
        )
