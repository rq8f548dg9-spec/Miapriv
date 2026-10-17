"""Client order id deterministici: lo stesso ordine inviato due volte resta uno.

Lo scenario da cui ci si difende e' preciso. Il bot invia un ordine, la
risposta si perde (timeout, processo ucciso, rete caduta). Al riavvio il bot
non sa se l'ordine e' stato accettato. Se lo rimanda con un id nuovo rischia
una posizione doppia; se non lo rimanda rischia di restare senza.

La soluzione e' far dipendere l'id solo dal contenuto della decisione: allo
stesso ordine corrisponde sempre lo stesso id, e l'exchange rifiuta il
duplicato perche' l'id esiste gia'. Il secondo invio diventa una domanda
("questo ordine c'e'?") invece di un secondo ordine.

Vincolo Binance: ``newClientOrderId`` ammette al massimo 36 caratteri fra
lettere, cifre e ``. _ - : /``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from core.types import Order, OrderStatus, Side, TimestampMs

__all__ = [
    "MAX_CLIENT_ORDER_ID_LENGTH",
    "DuplicateOrderError",
    "OrderRegistry",
    "client_order_id",
]

logger = logging.getLogger("trading_bot.idempotency")

MAX_CLIENT_ORDER_ID_LENGTH = 36
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _base36(value: int, length: int) -> str:
    digits: list[str] = []
    for _ in range(length):
        value, remainder = divmod(value, 36)
        digits.append(_ALPHABET[remainder])
    return "".join(reversed(digits))


def client_order_id(
    *,
    symbol: str,
    side: Side,
    quantity: Decimal,
    decision_ts_ms: TimestampMs,
    intent: str = "",
    prefix: str = "tb",
    bucket_ms: int = 1_000,
) -> str:
    """Id deterministico per un ordine.

    Due invii della stessa decisione producono lo stesso id. Due decisioni
    diverse, anche a un secondo di distanza, ne producono di diversi.

    ``bucket_ms`` arrotonda l'istante della decisione: protegge dal caso in
    cui il riavvio ricalcoli la stessa decisione con un timestamp spostato di
    pochi millisecondi. Un bucket troppo largo accorperebbe decisioni
    distinte, quindi resta a un secondo.
    """
    bucket = decision_ts_ms // max(1, bucket_ms)
    payload = "|".join(
        (symbol.upper(), side.value, format(quantity.normalize(), "f"), intent, str(bucket))
    )
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    fingerprint = _base36(int.from_bytes(digest[:16], "big"), 20)
    order_id = f"{prefix}-{fingerprint}"
    if len(order_id) > MAX_CLIENT_ORDER_ID_LENGTH:
        raise ValueError(
            f"client order id lungo {len(order_id)} caratteri, "
            f"il massimo Binance e' {MAX_CLIENT_ORDER_ID_LENGTH}: accorciare il prefisso"
        )
    return order_id


class DuplicateOrderError(RuntimeError):
    """Tentativo di reinviare un ordine gia' registrato come inviato."""


def _validate_record(order_id: object, record: object) -> tuple[Decimal, Decimal]:
    """Validate the fields needed to recover and reconcile a persisted order."""
    invalid = ValueError(f"registro ordini malformato: {order_id}")
    if not isinstance(order_id, str) or not order_id.strip() or not isinstance(record, dict):
        raise invalid
    try:
        symbol = record["symbol"]
        side = record["side"]
        status = record["status"]
        created_at_ms = record["created_at_ms"]
        exchange_order_id = record["exchange_order_id"]
        quantity_value = record["quantity"]
        filled_value = record.get("filled_quantity", "0")
        if not isinstance(symbol, str) or not symbol.strip():
            raise invalid
        if not isinstance(side, str) or not isinstance(status, str):
            raise invalid
        Side(side)
        OrderStatus(status)
        if type(created_at_ms) is not int or created_at_ms < 0:
            raise invalid
        if exchange_order_id is not None and not isinstance(exchange_order_id, str):
            raise invalid
        if not isinstance(quantity_value, str) or not isinstance(filled_value, str):
            raise invalid
        quantity = Decimal(quantity_value)
        filled = Decimal(filled_value)
        if not quantity.is_finite() or not filled.is_finite():
            raise invalid
        if quantity <= 0 or filled < 0 or filled > quantity:
            raise invalid
    except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
        raise invalid from exc
    return quantity, filled


@dataclass
class OrderRegistry:
    """Registro persistente degli ordini inviati.

    E' la prima linea di difesa, prima ancora dell'exchange: se un id risulta
    gia' inviato, il secondo invio viene bloccato qui, senza consumare rate
    limit per scoprirlo.

    Il salvataggio avviene prima dell'invio, non dopo. Un ordine registrato e
    mai partito e' un falso positivo che la riconciliazione risolve; un
    ordine partito e mai registrato e' una posizione fantasma.
    """

    path: Path | None = None
    _records: dict[str, dict[str, object]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None:
            self.path = Path(self.path)
            self.load()

    # -- interrogazione ---------------------------------------------------

    def __contains__(self, client_order_id_value: str) -> bool:
        return client_order_id_value in self._records

    def __len__(self) -> int:
        return len(self._records)

    @property
    def known_ids(self) -> frozenset[str]:
        return frozenset(self._records)

    def status_of(self, client_order_id_value: str) -> OrderStatus | None:
        record = self._records.get(client_order_id_value)
        if record is None:
            return None
        return OrderStatus(str(record["status"]))

    def symbol_of(self, client_order_id_value: str) -> str | None:
        """Simbolo di un ordine registrato: serve per interrogarne lo stato."""
        record = self._records.get(client_order_id_value)
        return None if record is None else str(record["symbol"])

    def pending_ids(self) -> list[str]:
        """Ordini inviati e non ancora in stato terminale.

        Sono quelli da chiedere all'exchange al riavvio: il loro destino e'
        l'unica cosa che lo stato locale non puo' sapere da solo.
        """
        return [
            order_id
            for order_id, record in self._records.items()
            if not OrderStatus(str(record["status"])).is_terminal
        ]

    def pending_quantities(self, side: Side | None = None) -> dict[str, Decimal]:
        """Quantita' ancora in volo per simbolo, al netto di quanto gia' eseguito.

        Serve a riservare esposizione: un ordine inviato e non concluso e'
        una posizione che sta arrivando, e va contata prima che arrivi.
        """
        totali: dict[str, Decimal] = {}
        for order_id in self.pending_ids():
            record = self._records[order_id]
            if side is not None and Side(str(record["side"])) is not side:
                continue
            residuo = Decimal(str(record["quantity"])) - Decimal(
                str(record.get("filled_quantity", "0"))
            )
            if residuo <= 0:
                continue
            symbol = str(record["symbol"])
            totali[symbol] = totali.get(symbol, Decimal(0)) + residuo
        return totali

    # -- aggiornamento ----------------------------------------------------

    def reserve(self, order: Order) -> None:
        """Registra un ordine PRIMA dell'invio. Rifiuta i duplicati."""
        if order.client_order_id in self._records:
            existing = self.status_of(order.client_order_id)
            raise DuplicateOrderError(
                f"ordine {order.client_order_id} gia' registrato (stato {existing}): "
                "reinvio bloccato, verificare lo stato sull'exchange"
            )
        record: dict[str, object] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": str(order.quantity),
            "created_at_ms": order.created_at_ms,
            "status": OrderStatus.PENDING_NEW.value,
            "exchange_order_id": None,
            "filled_quantity": "0",
        }
        _validate_record(order.client_order_id, record)
        self._records[order.client_order_id] = record
        self.save()

    def update(
        self,
        client_order_id_value: str,
        *,
        status: OrderStatus,
        exchange_order_id: str | None = None,
        filled_quantity: Decimal | None = None,
    ) -> None:
        record = self._records.get(client_order_id_value)
        if record is None:
            raise KeyError(f"ordine {client_order_id_value} non registrato")
        if not isinstance(status, OrderStatus):
            raise ValueError(f"stato ordine non valido: {status}")
        previous_status = OrderStatus(str(record["status"]))
        _, previous_filled = _validate_record(client_order_id_value, record)
        updated = record.copy()
        updated["status"] = status.value
        if exchange_order_id is not None:
            updated["exchange_order_id"] = exchange_order_id
        if filled_quantity is not None:
            updated["filled_quantity"] = str(filled_quantity)
        _, current_filled = _validate_record(client_order_id_value, updated)
        if current_filled < previous_filled:
            raise ValueError(f"ordine {client_order_id_value}: filled quantity diminuita")
        if previous_status.is_terminal and not status.is_terminal:
            raise ValueError(f"ordine {client_order_id_value}: stato terminale non riapribile")
        self._records[client_order_id_value] = updated
        self.save()

    def forget(self, client_order_ids: Iterable[str]) -> int:
        """Rimuove record terminali vecchi. Non tocca quelli ancora aperti."""
        removed = 0
        for order_id in list(client_order_ids):
            status = self.status_of(order_id)
            if status is not None and status.is_terminal:
                del self._records[order_id]
                removed += 1
        if removed:
            self.save()
        return removed

    # -- persistenza ------------------------------------------------------

    def load(self) -> int:
        if self.path is None or not self.path.is_file():
            return 0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Un registro illeggibile non va ignorato in silenzio: senza di
            # esso ogni ordine in volo diventa invisibile.
            logger.error("registro ordini illeggibile: %s", self.path)
            raise
        if not isinstance(data, dict):
            raise ValueError(f"registro ordini malformato: {self.path}")
        for order_id, record in data.items():
            _validate_record(order_id, record)
        self._records = data
        return len(self._records)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Scrittura atomica: un crash a meta' salvataggio lascerebbe un
        # registro troncato, cioe' ordini in volo dimenticati.
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._records, indent=2), encoding="utf-8")
        temporary.replace(self.path)
