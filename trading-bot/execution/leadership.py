"""Unicita' dell'emittente: un solo processo per conto puo' inviare ordini.

Il caso da cui difendersi non e' il doppio avvio distratto, e' il processo
vecchio che non e' ancora morto mentre il nuovo e' gia' partito: riavvio
automatico, container riprogrammato, terminale dimenticato aperto. Due
processi sullo stesso conto non raddoppiano soltanto gli ordini; ognuno
vede meta' della verita' e nessuno dei due riconcilia correttamente.

Il meccanismo e' un lease su file con contrassegno crescente (fencing
token). Non e' un lock distribuito: vale per processi sulla stessa
macchina, che e' esattamente il perimetro dichiarato (il bot gira sul PC
dell'utente). Le proprieta' che garantisce:

* chi tiene il lease lo rinnova; se smette, dopo la scadenza un altro puo'
  subentrare, e il contrassegno cresce;
* chi e' stato superato se ne accorge e non puo' riprendersi il posto di
  nascosto: il suo rinnovo non sovrascrive il titolare;
* un file illeggibile non viene sovrascritto in silenzio: meglio non
  partire che partire in due.

L'acquisizione si appoggia a ``O_CREAT | O_EXCL``, che e' atomico sia su
POSIX sia su Windows: fra due processi che trovano un lease scaduto e lo
rimuovono, uno solo riesce a crearlo, e l'altro rilegge e si ritira.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from core.clock import Clock
from core.types import TimestampMs

__all__ = ["FileLease", "LeaseInfo", "LeaseLostError"]

logger = logging.getLogger("trading_bot.leadership")


class LeaseLostError(RuntimeError):
    """Il processo non e' piu' l'emittente autorizzato."""


@dataclass(frozen=True, slots=True)
class LeaseInfo:
    """Contenuto del file di lease."""

    owner: str
    token: int
    acquired_at_ms: TimestampMs
    renewed_at_ms: TimestampMs
    ttl_ms: int

    def scaduto(self, now_ms: TimestampMs) -> bool:
        # Un orologio che torna indietro (cambio d'ora, correzione NTP) non
        # deve far sembrare scaduto un lease vivo, ne' il contrario: si
        # guarda solo la distanza in avanti.
        return now_ms - self.renewed_at_ms >= self.ttl_ms

    def to_dict(self) -> dict[str, object]:
        return {
            "owner": self.owner,
            "token": self.token,
            "acquired_at_ms": self.acquired_at_ms,
            "renewed_at_ms": self.renewed_at_ms,
            "ttl_ms": self.ttl_ms,
        }

    @classmethod
    def from_dict(cls, payload: object, percorso: Path) -> LeaseInfo:
        if not isinstance(payload, dict):
            raise ValueError(f"lease {percorso} illeggibile: non e' un oggetto")
        try:
            owner = payload["owner"]
            token = payload["token"]
            acquired = payload["acquired_at_ms"]
            renewed = payload["renewed_at_ms"]
            ttl = payload["ttl_ms"]
        except KeyError as exc:
            raise ValueError(f"lease {percorso} illeggibile: manca {exc}") from exc
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError(f"lease {percorso} illeggibile: proprietario non valido")
        for nome, valore in (
            ("token", token),
            ("acquired_at_ms", acquired),
            ("renewed_at_ms", renewed),
            ("ttl_ms", ttl),
        ):
            if type(valore) is not int or valore < 0:
                raise ValueError(f"lease {percorso} illeggibile: {nome} non valido")
        return cls(
            owner=owner,
            token=token,
            acquired_at_ms=acquired,
            renewed_at_ms=renewed,
            ttl_ms=ttl,
        )


def _identita() -> str:
    """Identita' del processo: macchina e pid, leggibile in un log."""
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass
class FileLease:
    """Lease su file con contrassegno crescente."""

    path: Path
    clock: Clock
    owner: str = field(default_factory=_identita)
    ttl_ms: int = 30_000
    token: int = 0
    _tenuto: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.ttl_ms <= 0:
            raise ValueError("ttl_ms deve essere positivo")

    # -- lettura ----------------------------------------------------------

    def read(self) -> LeaseInfo | None:
        """Lease corrente, o ``None`` se non c'e'. Solleva se e' illeggibile."""
        try:
            testo = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            payload = json.loads(testo)
        except json.JSONDecodeError as exc:
            raise ValueError(f"lease {self.path} illeggibile: {exc}") from exc
        return LeaseInfo.from_dict(payload, self.path)

    def holds(self) -> bool:
        """Vero se questo processo e' ancora l'emittente autorizzato.

        Rilegge il file: credere alla propria variabile sarebbe credere a
        se' stessi proprio nel caso in cui si e' stati superati.
        """
        if not self._tenuto:
            return False
        corrente = self.read()
        if corrente is None:
            self._tenuto = False
            return False
        mio = corrente.owner == self.owner and corrente.token == self.token
        if not mio:
            self._tenuto = False
        return mio and not corrente.scaduto(self.clock.now_ms())

    def require(self) -> None:
        """Solleva se la guida non e' piu' di questo processo.

        Prima di arrendersi prova a rinnovare: fra un ordine e l'altro puo'
        essere passato piu' tempo del previsto, e se nessuno e' subentrato
        la guida e' ancora nostra.
        """
        if not self.holds() and not self.renew():
            raise LeaseLostError(
                f"{self.owner} non e' piu' l'emittente autorizzato su {self.path}: "
                "nessun ordine puo' partire da qui"
            )

    # -- scrittura --------------------------------------------------------

    def acquire(self) -> bool:
        """Prova a prendere la guida. Falso se e' di qualcun altro e viva."""
        corrente = self.read()
        now_ms = self.clock.now_ms()
        if corrente is not None:
            if corrente.owner == self.owner and not corrente.scaduto(now_ms):
                self.token = corrente.token
                self._tenuto = True
                return True
            if not corrente.scaduto(now_ms):
                logger.warning(
                    "guida gia' presa da %s (contrassegno %d): questo processo non invia ordini",
                    corrente.owner,
                    corrente.token,
                )
                return False
            logger.warning(
                "lease di %s scaduto da %d ms: subentro",
                corrente.owner,
                now_ms - corrente.renewed_at_ms,
            )
            # Rimozione condizionata: se nel frattempo qualcun altro lo ha
            # gia' rinnovato o sostituito, non si tocca nulla.
            if not self._rimuovi_se_invariato(corrente):
                return False
        prossimo = (corrente.token if corrente else 0) + 1
        if not self._crea(prossimo, now_ms):
            return False
        self.token = prossimo
        self._tenuto = True
        return True

    def renew(self) -> bool:
        """Rinnova la scadenza. Falso se la guida non e' piu' di questo processo.

        Un lease scaduto ma ancora intestato a noi si rinnova: vuol dire
        che nessuno e' subentrato mentre il processo era fermo (una
        sospensione del PC, una pausa lunga). Se invece il record porta un
        altro proprietario o un contrassegno piu' alto, il rinnovo fallisce
        e non sovrascrive niente.
        """
        corrente = self.read()
        if corrente is None or corrente.owner != self.owner or corrente.token != self.token:
            if self._tenuto:
                logger.critical(
                    "guida persa su %s: ora e' di %s",
                    self.path,
                    corrente.owner if corrente else "nessuno",
                )
            self._tenuto = False
            return False
        self._scrivi(
            LeaseInfo(
                owner=self.owner,
                token=self.token,
                acquired_at_ms=corrente.acquired_at_ms,
                renewed_at_ms=self.clock.now_ms(),
                ttl_ms=self.ttl_ms,
            )
        )
        self._tenuto = True
        return True

    def release(self) -> None:
        """Rilascia la guida, se e' ancora nostra."""
        if self.holds():
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self._tenuto = False

    # -- interno ----------------------------------------------------------

    def _mio(self, now_ms: TimestampMs) -> LeaseInfo:
        return LeaseInfo(
            owner=self.owner,
            token=self.token,
            acquired_at_ms=now_ms,
            renewed_at_ms=now_ms,
            ttl_ms=self.ttl_ms,
        )

    def _crea(self, token: int, now_ms: TimestampMs) -> bool:
        """Crea il file in modo esclusivo. Falso se qualcun altro ha vinto."""
        info = LeaseInfo(
            owner=self.owner,
            token=token,
            acquired_at_ms=now_ms,
            renewed_at_ms=now_ms,
            ttl_ms=self.ttl_ms,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descrittore = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            logger.warning("un altro processo ha preso la guida per primo")
            return False
        with os.fdopen(descrittore, "w", encoding="utf-8") as uscita:
            json.dump(info.to_dict(), uscita)
        return True

    def _rimuovi_se_invariato(self, atteso: LeaseInfo) -> bool:
        """Toglie il lease scaduto solo se e' ancora quello che abbiamo letto."""
        try:
            corrente = self.read()
        except ValueError:
            return False
        if corrente is None:
            return True
        if corrente != atteso:
            return False
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        return True

    def _scrivi(self, info: LeaseInfo) -> None:
        temporaneo = self.path.with_suffix(self.path.suffix + ".tmp")
        temporaneo.write_text(json.dumps(info.to_dict()), encoding="utf-8")
        temporaneo.replace(self.path)
