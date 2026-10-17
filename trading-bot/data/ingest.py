"""Ingest WebSocket multi-simbolo con riconnessione robusta.

Vincolo operativo del progetto: il bot deve sopravvivere a una disconnessione.
Qui "sopravvivere" significa tre cose precise:

1. riconnettersi con backoff esponenziale e jitter, senza martellare l'endpoint;
2. invalidare i book ricostruiti, perche' gli eventi persi durante la caduta
   non si recuperano e un book con un buco e' peggio di nessun book;
3. segnalare ogni riconnessione al kill switch, che conta quante ne avvengono.

La connessione e' iniettata (``ConnectionFactory``) invece di essere creata
qui dentro: cosi' i test coprono riconnessione e gestione dei buchi senza
toccare la rete.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from config.schema import AppConfig
from core.clock import Clock
from core.types import Candle, TimestampMs, Trade
from data.orderbook import DepthUpdate

__all__ = [
    "ConnectionFactory",
    "MarketDataHandler",
    "MarketDataIngestor",
    "WebSocketConnection",
    "parse_stream_message",
    "websockets_connection_factory",
]

logger = logging.getLogger("trading_bot.ingest")


class WebSocketConnection(Protocol):
    """Minimo indispensabile di una connessione WebSocket."""

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def close(self) -> None: ...


ConnectionFactory = Callable[[str], Awaitable[WebSocketConnection]]


class MarketDataHandler(Protocol):
    """Destinatario dei dati di mercato decodificati."""

    def on_trade(self, trade: Trade) -> None: ...

    def on_candle(self, candle: Candle) -> None: ...

    def on_depth(self, update: DepthUpdate) -> None: ...

    def on_disconnect(self, ts_ms: TimestampMs, error: str) -> None: ...


def parse_stream_message(payload: str) -> Trade | Candle | DepthUpdate | None:
    """Decodifica un messaggio del combined stream Binance.

    Restituisce ``None`` per i messaggi che non ci interessano (risposte ai
    comandi di sottoscrizione, ping applicativi): non sono un errore.
    I numeri arrivano come stringhe e restano ``Decimal``, mai ``float``:
    un prezzo passato per float perde cifre che l'exchange invece conta.
    """
    message = json.loads(payload)
    data = message.get("data", message)
    if not isinstance(data, dict):
        return None
    event = data.get("e")

    if event in ("trade", "aggTrade"):
        return Trade(
            symbol=data["s"],
            trade_id=int(data["t"] if "t" in data else data["a"]),
            price=Decimal(data["p"]),
            quantity=Decimal(data["q"]),
            ts_ms=int(data["T"]),
            is_buyer_maker=bool(data["m"]),
        )

    if event == "kline":
        kline = data["k"]
        return Candle(
            symbol=data["s"],
            open_time_ms=int(kline["t"]),
            close_time_ms=int(kline["T"]),
            open=Decimal(kline["o"]),
            high=Decimal(kline["h"]),
            low=Decimal(kline["l"]),
            close=Decimal(kline["c"]),
            volume=Decimal(kline["v"]),
            quote_volume=Decimal(kline["q"]),
            trades=int(kline["n"]),
            is_closed=bool(kline["x"]),
        )

    if event == "depthUpdate":
        return DepthUpdate(
            symbol=data["s"],
            first_update_id=int(data["U"]),
            final_update_id=int(data["u"]),
            bids=tuple((Decimal(p), Decimal(q)) for p, q in data["b"]),
            asks=tuple((Decimal(p), Decimal(q)) for p, q in data["a"]),
            event_time_ms=int(data["E"]),
        )

    return None


async def websockets_connection_factory(url: str) -> WebSocketConnection:
    """Factory di produzione, basata sulla libreria ``websockets``.

    Importata qui dentro e non in testa al modulo: il backtest non ha bisogno
    di websockets installato per girare.
    """
    import websockets

    connection: WebSocketConnection = await websockets.connect(
        url, ping_interval=20, ping_timeout=20
    )
    return connection


@dataclass
class MarketDataIngestor:
    """Gestisce una connessione al combined stream e la sua vita travagliata."""

    config: AppConfig
    clock: Clock
    handler: MarketDataHandler
    connection_factory: ConnectionFactory = websockets_connection_factory
    #: Numero massimo di cicli di connessione. ``None`` = per sempre (produzione).
    max_cycles: int | None = None
    reconnects: int = 0
    messages_received: int = 0
    parse_errors: int = 0
    last_message_ms: TimestampMs = 0
    _running: bool = field(default=False, repr=False)

    @property
    def is_running(self) -> bool:
        return self._running

    def stream_names(self) -> list[str]:
        """Flussi sottoscritti: trade, candele e profondita' per ogni simbolo."""
        depth_speed = self.config.data.orderbook_update_speed_ms
        interval = self.config.data.candle_interval
        names: list[str] = []
        for symbol in self.config.enabled_symbols:
            lower = symbol.symbol.lower()
            names.append(f"{lower}@trade")
            names.append(f"{lower}@kline_{interval}")
            names.append(f"{lower}@depth@{depth_speed}ms")
        return names

    def stream_url(self) -> str:
        return f"{self.config.ws_url}?streams={'/'.join(self.stream_names())}"

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Ciclo di vita della connessione: connetti, consuma, riconnetti.

        Non solleva mai per un errore di rete: una disconnessione e' un evento
        atteso, non un'eccezione. Solleva solo se la configurazione e' tale da
        rendere impossibile connettersi.
        """
        self._running = True
        delay = self.config.data.ws_reconnect_initial_s
        cycles = 0

        while self._running:
            if self.max_cycles is not None and cycles >= self.max_cycles:
                break
            cycles += 1
            try:
                consumed = await self._consume()
                if consumed > 0:
                    # La sessione ha funzionato: il prossimo backoff riparte dal minimo
                    # invece di ereditare l'attesa di una caduta vecchia di ore.
                    delay = self.config.data.ws_reconnect_initial_s
                # Uscita pulita dal flusso: l'endpoint ha chiuso la connessione.
                raise ConnectionError("flusso terminato dall'endpoint")
            except Exception as exc:
                if not self.is_running:
                    break
                self.reconnects += 1
                now_ms = self.clock.now_ms()
                self.handler.on_disconnect(now_ms, str(exc))
                if self.max_cycles is not None and cycles >= self.max_cycles:
                    # Ultimo ciclo consentito: inutile attendere per una
                    # riconnessione che non avverra'.
                    break
                logger.warning(
                    "disconnessione dal flusso (%s), riconnessione fra %.1fs",
                    exc,
                    delay,
                )
                # jitter: evita che piu' istanze si riconnettano in sincrono
                await self.clock.sleep(delay * (0.5 + random.random()))  # noqa: S311
                delay = min(
                    delay * 2.0,
                    self.config.data.ws_reconnect_max_s,
                )

    async def _consume(self) -> int:
        """Una sessione di connessione, dal collegamento alla caduta.

        Restituisce quanti messaggi ha consegnato: zero indica una connessione
        che non ha mai funzionato davvero, e il backoff non va azzerato.
        """
        connection = await self.connection_factory(self.stream_url())
        consumed = 0
        try:
            async for raw in connection:
                if not self.is_running:
                    break
                payload = raw.decode() if isinstance(raw, bytes) else raw
                consumed += 1
                self.messages_received += 1
                self.last_message_ms = self.clock.now_ms()
                self._dispatch(payload)
        finally:
            await connection.close()
        return consumed

    def _dispatch(self, payload: str) -> None:
        """Decodifica e consegna. Un messaggio malformato non ferma il flusso."""
        try:
            event = parse_stream_message(payload)
        except Exception as exc:
            self.parse_errors += 1
            logger.warning("messaggio non decodificabile scartato: %s", exc)
            return

        if event is None:
            return
        if isinstance(event, Trade):
            self.handler.on_trade(event)
        elif isinstance(event, Candle):
            self.handler.on_candle(event)
        else:
            self.handler.on_depth(event)
