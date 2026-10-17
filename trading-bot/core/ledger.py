"""Registro a eventi del patrimonio.

Tre numeri che vanno tenuti separati, e che quasi tutti i bot confondono:

* quanto denaro e' stato **conferito** (o prelevato) da fuori;
* quanto e' stato **guadagnato o perso** operando;
* quanto c'e' **adesso**.

Confonderli fa sembrare un versamento un profitto, e fa ripartire il
dimensionamento dal deposito iniziale anche dopo mesi di risultati. Qui
ogni movimento e' un evento scritto in fondo a un file e mai modificato:
il patrimonio corrente non e' un numero salvato, e' il risultato di
rileggerli tutti. Un numero salvato puo' sbagliare in silenzio, una
rilettura no.

Il registro NON e' la verita' sui saldi: quella sta sull'exchange. E' la
verita' su quanto capitale e' affidato al bot. Chi li mette insieme
prende sempre il minore dei due.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path

from core.portfolio import Portfolio
from core.types import Fill, Side, TimestampMs

__all__ = [
    "EventKind",
    "EventLedger",
    "LedgerError",
    "LedgerEvent",
    "LedgerState",
]

logger = logging.getLogger("trading_bot.ledger")


class LedgerError(Exception):
    """Il registro non e' leggibile, o il movimento non e' ammissibile."""


class EventKind(StrEnum):
    CONFERIMENTO = "conferimento"
    PRELIEVO = "prelievo"
    ESECUZIONE = "esecuzione"


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """Un movimento. Scritto una volta, mai corretto.

    Correggere un evento sbagliato vorrebbe dire riscrivere la storia: si
    scrive invece un movimento che lo compensa, cosi' resta visibile che
    e' successo qualcosa.
    """

    seq: int
    kind: EventKind
    ts_ms: TimestampMs
    #: Importo in valuta di conto, per conferimenti e prelievi.
    amount: Decimal = Decimal(0)
    symbol: str = ""
    side: Side | None = None
    quantity: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    fee: Decimal = Decimal(0)
    fee_asset: str = ""
    trade_id: str = ""
    client_order_id: str = ""
    is_maker: bool = False
    note: str = ""

    def to_json(self) -> str:
        """Una riga di JSON. I Decimal diventano stringhe.

        Un registro di denaro che salva gli importi come numeri in virgola
        mobile falsifica cio' che registra: 0.1 + 0.2 non fa 0.3, e la
        differenza si accumula a ogni rilettura.
        """
        payload: dict[str, object] = {
            "seq": self.seq,
            "kind": self.kind.value,
            "ts_ms": self.ts_ms,
        }
        if self.kind is EventKind.ESECUZIONE:
            payload.update(
                {
                    "symbol": self.symbol,
                    "side": self.side.value if self.side else "",
                    "quantity": str(self.quantity),
                    "price": str(self.price),
                    "fee": str(self.fee),
                    "fee_asset": self.fee_asset,
                    "trade_id": self.trade_id,
                    "client_order_id": self.client_order_id,
                    "is_maker": self.is_maker,
                }
            )
        else:
            payload["amount"] = str(self.amount)
        if self.note:
            payload["note"] = self.note
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> LedgerEvent:
        try:
            kind = EventKind(str(payload["kind"]))
            seq = int(str(payload["seq"]))
            ts_ms = int(str(payload["ts_ms"]))
        except (KeyError, ValueError) as exc:
            raise LedgerError(f"evento illeggibile: {exc}") from exc

        if kind is not EventKind.ESECUZIONE:
            return cls(
                seq=seq,
                kind=kind,
                ts_ms=ts_ms,
                amount=_decimale(payload.get("amount"), "amount"),
                note=str(payload.get("note", "")),
            )
        lato = str(payload.get("side", ""))
        return cls(
            seq=seq,
            kind=kind,
            ts_ms=ts_ms,
            symbol=str(payload.get("symbol", "")),
            side=Side(lato) if lato else None,
            quantity=_decimale(payload.get("quantity"), "quantity"),
            price=_decimale(payload.get("price"), "price"),
            fee=_decimale(payload.get("fee"), "fee"),
            fee_asset=str(payload.get("fee_asset", "")),
            trade_id=str(payload.get("trade_id", "")),
            client_order_id=str(payload.get("client_order_id", "")),
            is_maker=bool(payload.get("is_maker", False)),
            note=str(payload.get("note", "")),
        )

    def to_fill(self) -> Fill:
        if self.kind is not EventKind.ESECUZIONE or self.side is None:
            raise LedgerError(f"evento {self.seq} non e' un'esecuzione")
        return Fill(
            client_order_id=self.client_order_id,
            symbol=self.symbol,
            side=self.side,
            price=self.price,
            quantity=self.quantity,
            fee=self.fee,
            fee_asset=self.fee_asset,
            ts_ms=self.ts_ms,
            is_maker=self.is_maker,
            trade_id=self.trade_id,
        )


def _decimale(valore: object, campo: str) -> Decimal:
    if valore is None:
        return Decimal(0)
    try:
        return Decimal(str(valore))
    except InvalidOperation as exc:
        raise LedgerError(f"campo {campo} non numerico: {valore!r}") from exc


@dataclass(frozen=True, slots=True)
class LedgerState:
    """Risultato della rilettura: i tre numeri, separati."""

    conferito: Decimal
    prelevato: Decimal
    portfolio: Portfolio

    @property
    def commissioni(self) -> Decimal:
        return self.portfolio.fees_paid

    @property
    def pnl_netto(self) -> Decimal:
        """Guadagno realizzato al netto delle commissioni in valuta di conto.

        Le commissioni pagate in altre monete (tipicamente BNB) non si
        convertono qui: restano contate a parte, perche' inventare un
        cambio e' un modo per far sembrare il risultato migliore.
        """
        return self.portfolio.realized_pnl

    @property
    def capitale_in_gestione(self) -> Decimal:
        """Patrimonio di cui il bot dispone adesso, ai prezzi di carico.

        E' la base del dimensionamento: cento che diventano centotrenta
        sono centotrenta. Non e' un tetto di rischio: i limiti restano
        quelli in configurazione e non si alzano da soli.
        """
        return self.portfolio.equity


class EventLedger:
    """File di eventi in sola aggiunta, con rilettura all'apertura."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._events: list[LedgerEvent] = []
        #: Esecuzioni gia' registrate. Scandire la lista a ogni fill
        #: costerebbe sempre di piu' col passare dei mesi.
        self._fill_keys: set[str] = set()
        self._carica()

    # -- lettura -----------------------------------------------------------

    @property
    def is_empty(self) -> bool:
        return not self._events

    def events(self) -> list[LedgerEvent]:
        return list(self._events)

    def _carica(self) -> None:
        if not self.path.exists():
            return
        righe = self.path.read_text(encoding="utf-8").splitlines()
        eventi: list[LedgerEvent] = []
        for numero, riga in enumerate(righe, start=1):
            if not riga.strip():
                continue
            try:
                payload = json.loads(riga)
            except json.JSONDecodeError as exc:
                if numero == len(righe):
                    # Ultima riga a meta': il processo e' morto mentre
                    # scriveva, quindi quel movimento non e' mai stato
                    # completato. Si scarta e si riscrive da li'.
                    logger.critical(
                        "ultimo evento del registro incompleto, scartato: %s", exc
                    )
                    self._tronca_a(eventi)
                    break
                raise LedgerError(
                    f"registro {self.path} illeggibile alla riga {numero}: {exc}"
                ) from exc
            if not isinstance(payload, dict):
                raise LedgerError(f"registro {self.path}: riga {numero} non e' un evento")
            evento = LedgerEvent.from_payload(payload)
            atteso = len(eventi) + 1
            if evento.seq != atteso:
                raise LedgerError(
                    f"registro {self.path}: sequenza {evento.seq} alla riga {numero}, "
                    f"attesa {atteso}. Manca un movimento o ne e' stato aggiunto uno "
                    "a mano: il totale che ne uscirebbe sarebbe falso."
                )
            eventi.append(evento)
        self._events = eventi
        self._fill_keys = {
            f"{e.symbol}|{e.trade_id}" for e in eventi if e.kind is EventKind.ESECUZIONE
        }

    def _tronca_a(self, eventi: list[LedgerEvent]) -> None:
        """Riscrive il file con i soli eventi completi."""
        testo = "".join(f"{evento.to_json()}\n" for evento in eventi)
        temporaneo = self.path.with_suffix(self.path.suffix + ".tmp")
        temporaneo.write_text(testo, encoding="utf-8")
        os.replace(temporaneo, self.path)

    # -- scrittura ---------------------------------------------------------

    def _append(self, evento: LedgerEvent) -> LedgerEvent:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        riga = f"{evento.to_json()}\n"
        # Append con fsync: un evento che risulta scritto e non lo e' fa
        # ripartire il bot con un patrimonio diverso da quello vero.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(riga)
            handle.flush()
            os.fsync(handle.fileno())
        self._events.append(evento)
        return evento

    def _prossima_seq(self) -> int:
        return len(self._events) + 1

    def conferimento(
        self, importo: Decimal, *, ts_ms: TimestampMs, note: str = ""
    ) -> LedgerEvent:
        """Registra denaro entrato da fuori. Non e' un guadagno."""
        if importo <= 0:
            raise LedgerError(f"conferimento {importo} non positivo")
        return self._append(
            LedgerEvent(
                seq=self._prossima_seq(),
                kind=EventKind.CONFERIMENTO,
                ts_ms=ts_ms,
                amount=importo,
                note=note,
            )
        )

    def prelievo(
        self, importo: Decimal, *, ts_ms: TimestampMs, note: str = ""
    ) -> LedgerEvent:
        """Registra denaro uscito. Non e' una perdita.

        Si puo' prelevare solo quello che e' liquido: il resto e' dentro
        alle posizioni aperte, e tirarlo fuori vorrebbe dire chiuderle.
        """
        if importo <= 0:
            raise LedgerError(f"prelievo {importo} non positivo")
        disponibile = self.replay().portfolio.cash
        if importo > disponibile:
            raise LedgerError(
                f"prelievo {importo} oltre i {disponibile} liquidi: il resto e' "
                "dentro alle posizioni aperte"
            )
        return self._append(
            LedgerEvent(
                seq=self._prossima_seq(),
                kind=EventKind.PRELIEVO,
                ts_ms=ts_ms,
                amount=importo,
                note=note,
            )
        )

    def record_fill(self, fill: Fill) -> bool:
        """Registra un'esecuzione. Falso se era gia' registrata.

        Il doppione non e' un caso di scuola: lo stesso fill arriva dalla
        risposta all'ordine e poi di nuovo dalla riconciliazione.
        """
        chiave = f"{fill.symbol}|{fill.trade_id}"
        if chiave in self._fill_keys:
            return False
        self._append(
            LedgerEvent(
                seq=self._prossima_seq(),
                kind=EventKind.ESECUZIONE,
                ts_ms=fill.ts_ms,
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                fee=fill.fee,
                fee_asset=fill.fee_asset,
                trade_id=fill.trade_id,
                client_order_id=fill.client_order_id,
                is_maker=fill.is_maker,
            )
        )
        self._fill_keys.add(chiave)
        return True

    def apri_se_vuoto(
        self, capitale: Decimal, *, ts_ms: TimestampMs
    ) -> bool:
        """Primo conferimento, solo se il registro e' ancora vuoto.

        Il capitale scritto in configurazione vale al primo avvio e basta:
        dopo, cambiarlo non puo' riscrivere la storia del conto. Chi vuole
        aggiungere o togliere denaro lo registra come movimento.
        """
        if not self.is_empty:
            return False
        self.conferimento(capitale, ts_ms=ts_ms, note="capitale iniziale dichiarato")
        return True

    # -- rilettura ---------------------------------------------------------

    def replay(self, quote_asset: str = "USDT") -> LedgerState:
        """Ricostruisce il patrimonio rileggendo tutti gli eventi."""
        conferito = Decimal(0)
        prelevato = Decimal(0)
        portfolio: Portfolio | None = None

        for evento in self._events:
            if evento.kind is EventKind.CONFERIMENTO:
                conferito += evento.amount
                if portfolio is None:
                    portfolio = Portfolio(evento.amount, quote_asset=quote_asset)
                else:
                    portfolio.deposita(evento.amount)
            elif evento.kind is EventKind.PRELIEVO:
                prelevato += evento.amount
                if portfolio is None:
                    raise LedgerError("prelievo prima di qualunque conferimento")
                portfolio.preleva(evento.amount)
            else:
                if portfolio is None:
                    raise LedgerError("esecuzione prima di qualunque conferimento")
                portfolio.apply_fill(evento.to_fill())

        if portfolio is None:
            portfolio = Portfolio(Decimal(1), quote_asset=quote_asset)
            portfolio.cash = Decimal(0)
            portfolio.peak_equity = Decimal(0)
            portfolio.day_start_equity = Decimal(0)
        return LedgerState(conferito=conferito, prelevato=prelevato, portfolio=portfolio)
