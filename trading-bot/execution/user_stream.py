"""Flusso dati dell'account: le esecuzioni appena avvengono.

Finora i fill si scoprivano chiedendo: nella risposta all'ordine, e poi
nella riconciliazione periodica. Funziona, ma lascia una finestra in cui
una posizione esiste sull'exchange e non nei libri del bot. E' corta, ed
e' esattamente la finestra in cui un crash fa danno.

Questo modulo ascolta il flusso dell'account, dove le esecuzioni vengono
annunciate quando avvengono. Dal 2025 Binance ha tolto dalla
documentazione il vecchio ``listenKey`` su ``stream.binance.com`` e
indica come strada preferita la sottoscrizione firmata sulla WebSocket
API, ``userDataStream.subscribe.signature``: e' quella implementata qui,
e non c'e' nessun listenKey da creare o rinnovare.

**Cosa non e' stato provato.** La stretta di mano con l'endpoint vero
richiede credenziali e una connessione autenticata, e in questo
ambiente non e' stata fatta. Quello che e' verificato dai test e' la
forma della richiesta firmata e il trattamento degli eventi. Prima di
affidarci la contabilita' su denaro vero va provata su testnet, con
l'autorizzazione di chi possiede il conto.

Il flusso **aggiunge** informazione, non la sostituisce: la
riconciliazione periodica resta, perche' un messaggio puo' perdersi e
un bot che si fida di un solo canale non se ne accorge.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from core.types import Fill, Side, TimestampMs

__all__ = [
    "METODO_SOTTOSCRIZIONE",
    "FlussoAccount",
    "costruisci_sottoscrizione",
    "interpreta_evento",
]

logger = logging.getLogger("trading_bot.user_stream")

#: Il metodo nuovo. Il vecchio listenKey non compare da nessuna parte,
#: nemmeno come ripiego: appoggiarsi a un endpoint che la documentazione
#: non descrive piu' significa dipendere da qualcosa che puo' sparire
#: senza preavviso.
METODO_SOTTOSCRIZIONE = "userDataStream.subscribe.signature"


def costruisci_sottoscrizione(
    *, api_key: str, api_secret: str, ts_ms: TimestampMs
) -> dict[str, Any]:
    """Richiesta firmata di sottoscrizione al flusso dell'account.

    La firma copre i parametri ordinati, come per le chiamate REST. Il
    segreto entra solo nell'HMAC: non compare nel messaggio, e il
    messaggio e' una cosa che prima o poi finisce in un log.
    """
    params: dict[str, Any] = {"apiKey": api_key, "timestamp": ts_ms}
    base = "&".join(f"{chiave}={valore}" for chiave, valore in sorted(params.items()))
    firma = hmac.new(
        api_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "id": uuid.uuid4().hex,
        "method": METODO_SOTTOSCRIZIONE,
        "params": {**params, "signature": firma},
    }


def _decimale(valore: Any) -> Decimal | None:
    if valore is None:
        return None
    try:
        return Decimal(str(valore))
    except (InvalidOperation, ValueError):
        return None


def interpreta_evento(messaggio: dict[str, Any]) -> Fill | None:
    """Converte un evento in un'esecuzione, o None se non lo e'.

    Si accetta sia l'evento nudo sia quello avvolto in
    ``{"subscriptionId": ..., "event": {...}}``: alcuni endpoint
    consegnano la seconda forma, e un parser che non la conosce butta
    via proprio le esecuzioni.
    """
    evento = messaggio.get("event") if isinstance(messaggio.get("event"), dict) else messaggio
    if not isinstance(evento, dict) or evento.get("e") != "executionReport":
        return None
    if evento.get("x") != "TRADE":
        # NEW, CANCELED, EXPIRED: cambiano lo stato dell'ordine, non la
        # posizione. Trattarli come esecuzioni sposterebbe quantita' che
        # non si sono mosse.
        return None

    quantita = _decimale(evento.get("l"))
    prezzo = _decimale(evento.get("L"))
    if quantita is None or prezzo is None or quantita <= 0 or prezzo <= 0:
        return None

    lato = str(evento.get("S", ""))
    if lato not in {"BUY", "SELL"}:
        return None

    trade_id = evento.get("t")
    if trade_id is None:
        return None

    return Fill(
        client_order_id=str(evento.get("c", "")),
        symbol=str(evento.get("s", "")),
        side=Side(lato),
        price=prezzo,
        quantity=quantita,
        fee=_decimale(evento.get("n")) or Decimal(0),
        fee_asset=str(evento.get("N") or ""),
        ts_ms=int(evento.get("T", 0)),
        is_maker=bool(evento.get("m", False)),
        trade_id=str(trade_id),
        exchange_order_id=str(evento.get("i")) if evento.get("i") is not None else None,
    )


@dataclass
class FlussoAccount:
    """Stato dell'ascolto. La connessione la gestisce chi lo usa."""

    api_key: str
    api_secret: str
    on_fill: Callable[[Fill], None]
    url: str
    esecuzioni_ricevute: int = field(default=0, init=False)
    messaggi_illeggibili: int = field(default=0, init=False)
    eventi_ignorati: int = field(default=0, init=False)
    ultimo_errore: str | None = field(default=None, init=False)

    def sottoscrizione(self, ts_ms: TimestampMs) -> str:
        """Il messaggio da inviare appena la connessione e' aperta."""
        return json.dumps(
            costruisci_sottoscrizione(
                api_key=self.api_key, api_secret=self.api_secret, ts_ms=ts_ms
            )
        )

    def gestisci_messaggio(self, testo: str) -> None:
        """Smista un messaggio. Non solleva mai: il flusso deve restare su.

        Un errore dell'exchange pero' non viene ingoiato: resta scritto in
        ``ultimo_errore`` e nei log, perche' una sottoscrizione rifiutata
        che passa in silenzio lascia il bot convinto di ascoltare.
        """
        try:
            messaggio = json.loads(testo)
        except json.JSONDecodeError:
            self.messaggi_illeggibili += 1
            logger.warning("messaggio del flusso account illeggibile")
            return

        if not isinstance(messaggio, dict):
            self.messaggi_illeggibili += 1
            return

        errore = messaggio.get("error")
        if isinstance(errore, dict):
            self.ultimo_errore = (
                f"{errore.get('code')}: {errore.get('msg')}"
            )
            logger.error("flusso account rifiutato dall'exchange: %s", self.ultimo_errore)
            return

        fill = interpreta_evento(messaggio)
        if fill is None:
            self.eventi_ignorati += 1
            return

        self.esecuzioni_ricevute += 1
        self.on_fill(fill)
