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
from decimal import Decimal
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

    # -- aggiornamento ----------------------------------------------------

    def reserve(self, order: Order) -> None:
        """Registra un ordine PRIMA dell'invio. Rifiuta i duplicati."""
        if order.client_order_id in self._records:
            existing = self.status_of(order.client_order_id)
            raise DuplicateOrderError(
                f"ordine {order.client_order_id} gia' registrato (stato {existing}): "
                "reinvio bloccato, verificare lo stato sull'exchange"
            )
        self._records[order.client_order_id] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "quantity": str(order.quantity),
            "created_at_ms": order.created_at_ms,
            "status": OrderStatus.PENDING_NEW.value,
            "exchange_order_id": None,
            "filled_quantity": "0",
        }
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
        record["status"] = status.value
        if exchange_order_id is not None:
            record["exchange_order_id"] = exchange_order_id
        if filled_quantity is not None:
            record["filled_quantity"] = str(filled_quantity)
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
