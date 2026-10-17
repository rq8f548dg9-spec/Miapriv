"""Implementazione concreta per Binance Spot (testnet e live).

Tre vincoli operativi sono implementati qui, non lasciati alla speranza:

* **filtri letti, mai ipotizzati**: tick size, step size e MIN_NOTIONAL
  arrivano da ``exchangeInfo`` e vengono riletti periodicamente;
* **rate limit rispettati**: un throttle a token bucket lato client, piu'
  stretto dei limiti dell'exchange, perche' farsi bandire l'IP mentre si ha
  una posizione aperta e' il modo peggiore di scoprire un limite;
* **errori 5xx e 429 gestiti**: ritentati con backoff esponenziale, mentre i
  4xx (ordine rifiutato, firma sbagliata) non si ritentano mai: ripetere una
  richiesta sbagliata la rende solo sbagliata due volte.

Il trasporto HTTP e' iniettato (``HttpClient``): i test coprono firma,
throttle e retry senza rete.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlencode

from config.credentials import Credentials
from config.schema import AppConfig
from core.clock import Clock
from core.types import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Side,
    SymbolFilters,
    TimeInForce,
    TimestampMs,
)
from execution.broker import AccountState, Broker, BrokerError, DepthSnapshot, OrderAck

__all__ = ["BinanceBroker", "HttpClient", "HttpResponse", "RateLimiter", "parse_symbol_filters"]

logger = logging.getLogger("trading_bot.binance")

#: Stati Binance -> stati interni.
_STATUS_MAP = {
    "NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "PENDING_CANCEL": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
    "EXPIRED_IN_MATCH": OrderStatus.EXPIRED,
}


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    payload: Any

    @property
    def is_rate_limited(self) -> bool:
        # 418 = IP bandito dopo ripetuti 429. Entrambi vanno rispettati.
        return self.status in (429, 418)

    @property
    def is_server_error(self) -> bool:
        return 500 <= self.status < 600


class HttpClient(Protocol):
    """Trasporto HTTP minimo, iniettabile per i test."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse: ...

    async def close(self) -> None: ...


@dataclass
class RateLimiter:
    """Token bucket per il peso delle richieste.

    Binance conta un "peso" per endpoint, non le richieste. Il bucket si
    ricarica linearmente: se i token non bastano si attende, invece di
    inviare e scoprire il limite con un 429.
    """

    capacity: float
    refill_per_second: float
    clock: Clock
    _tokens: float = field(init=False)
    _last_refill_ns: int = field(init=False)
    waits: int = 0

    def __post_init__(self) -> None:
        if self.capacity <= 0 or self.refill_per_second <= 0:
            raise ValueError("capacita' e ricarica devono essere positive")
        self._tokens = self.capacity
        self._last_refill_ns = self.clock.monotonic_ns()

    async def acquire(self, weight: float = 1.0) -> None:
        if weight > self.capacity:
            raise ValueError(
                f"peso {weight} oltre la capacita' {self.capacity}: richiesta non inviabile"
            )
        while True:
            self._refill()
            if self._tokens >= weight:
                self._tokens -= weight
                return
            missing = weight - self._tokens
            self.waits += 1
            await self.clock.sleep(missing / self.refill_per_second)

    def _refill(self) -> None:
        now_ns = self.clock.monotonic_ns()
        elapsed_s = (now_ns - self._last_refill_ns) / 1_000_000_000
        if elapsed_s <= 0:
            return
        self._last_refill_ns = now_ns
        self._tokens = min(self.capacity, self._tokens + elapsed_s * self.refill_per_second)


def parse_symbol_filters(payload: Mapping[str, Any], fetched_at_ms: TimestampMs) -> SymbolFilters:
    """Estrae i filtri di un simbolo dalla risposta di ``exchangeInfo``.

    Una chiave mancante solleva: meglio non partire che partire con un
    MIN_NOTIONAL inventato.
    """
    filters = {f["filterType"]: f for f in payload["filters"]}
    try:
        price_filter = filters["PRICE_FILTER"]
        lot_size = filters["LOT_SIZE"]
        notional = filters.get("NOTIONAL") or filters["MIN_NOTIONAL"]
    except KeyError as exc:
        raise BrokerError(
            f"filtro {exc} assente per {payload.get('symbol')}: exchangeInfo incompleto"
        ) from exc

    min_notional = notional.get("minNotional") or notional.get("notional")
    if min_notional is None:
        raise BrokerError(f"minNotional assente per {payload.get('symbol')}")

    return SymbolFilters(
        symbol=payload["symbol"],
        tick_size=Decimal(price_filter["tickSize"]),
        step_size=Decimal(lot_size["stepSize"]),
        min_quantity=Decimal(lot_size["minQty"]),
        max_quantity=Decimal(lot_size["maxQty"]),
        min_notional=Decimal(min_notional),
        base_precision=int(payload.get("baseAssetPrecision", 8)),
        quote_precision=int(payload.get("quoteAssetPrecision", 8)),
        fetched_at_ms=fetched_at_ms,
    )


@dataclass
class BinanceBroker(Broker):
    """Client REST firmato, con throttle e backoff."""

    config: AppConfig
    credentials: Credentials
    clock: Clock
    http: HttpClient
    limiter: RateLimiter | None = None
    _filters_cache: dict[str, SymbolFilters] = field(default_factory=dict, repr=False)
    _filters_fetched_ms: TimestampMs = 0
    requests_sent: int = 0
    retries: int = 0

    def __post_init__(self) -> None:
        if self.limiter is None:
            weight = float(self.config.exchange.max_request_weight_per_minute)
            self.limiter = RateLimiter(
                capacity=weight, refill_per_second=weight / 60.0, clock=self.clock
            )

    @property
    def base_url(self) -> str:
        """URL scelto dalla modalita'. Unico punto che distingue testnet e live."""
        return self.config.rest_url

    # -- firma e trasporto -------------------------------------------------

    def _sign(self, params: dict[str, Any]) -> str:
        """Firma HMAC-SHA256 della query string, come richiede Binance.

        Il segreto non viene mai loggato ne' incluso nella query: entra solo
        nella firma.
        """
        query = urlencode(params)
        signature = hmac.new(
            self.credentials.api_secret.get_secret_value().encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{query}&signature={signature}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        weight: float = 1.0,
    ) -> Any:
        """Esegue una richiesta con throttle, firma e backoff.

        I 4xx non vengono ritentati: un ordine rifiutato per filtro violato o
        una firma sbagliata restano tali anche al secondo tentativo, e
        ritentare consuma solo rate limit.
        """
        assert self.limiter is not None
        parameters = dict(params or {})
        headers: dict[str, str] = {}

        if signed:
            parameters["timestamp"] = self.clock.now_ms()
            parameters["recvWindow"] = self.config.exchange.recv_window_ms
            query = self._sign(parameters)
            headers["X-MBX-APIKEY"] = self.credentials.api_key.get_secret_value()
        else:
            query = urlencode(parameters)

        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        delay = self.config.exchange.backoff_initial_s
        last_error = ""
        for attempt in range(self.config.exchange.max_retries + 1):
            await self.limiter.acquire(weight)
            self.requests_sent += 1
            response = await self.http.request(
                method,
                url,
                headers=headers,
                timeout=self.config.exchange.request_timeout_s,
            )
            if 200 <= response.status < 300:
                return response.payload

            retryable = response.is_rate_limited or response.is_server_error
            last_error = f"HTTP {response.status}: {response.payload}"
            if not retryable:
                raise BrokerError(f"{method} {path} rifiutata dall'exchange - {last_error}")
            if attempt >= self.config.exchange.max_retries:
                break

            self.retries += 1
            logger.warning(
                "%s %s fallita (%s), nuovo tentativo fra %.1fs",
                method,
                path,
                last_error,
                delay,
            )
            await self.clock.sleep(delay)
            delay = min(
                delay * self.config.exchange.backoff_multiplier,
                self.config.exchange.backoff_max_s,
            )

        raise BrokerError(
            f"{method} {path} fallita dopo {self.config.exchange.max_retries} tentativi "
            f"- {last_error}"
        )

    # -- interfaccia Broker ------------------------------------------------

    async def exchange_filters(self, *, force: bool = False) -> dict[str, SymbolFilters]:
        """Filtri correnti, con cache a scadenza.

        Un filtro vecchio e' peggio di nessun filtro: il risk manager rifiuta
        gli ordini se i filtri sono scaduti, quindi qui si rilegge.
        """
        now_ms = self.clock.now_ms()
        age_ms = now_ms - self._filters_fetched_ms
        max_age_ms = self.config.exchange.exchange_info_refresh_s * 1000
        if not force and self._filters_cache and age_ms < max_age_ms:
            return dict(self._filters_cache)

        symbols = [s.symbol for s in self.config.enabled_symbols]
        payload = await self._request(
            "GET",
            "/api/v3/exchangeInfo",
            params={"symbols": "[" + ",".join(f'"{s}"' for s in symbols) + "]"},
            weight=10,
        )
        parsed = {
            item["symbol"]: parse_symbol_filters(item, now_ms)
            for item in payload.get("symbols", [])
        }
        missing = set(symbols) - set(parsed)
        if missing:
            raise BrokerError(f"simboli assenti da exchangeInfo: {sorted(missing)}")
        self._filters_cache = parsed
        self._filters_fetched_ms = now_ms
        return dict(parsed)

    async def account_state(self) -> AccountState:
        account = await self._request("GET", "/api/v3/account", signed=True, weight=10)
        balances = {
            item["asset"]: Decimal(item["free"]) + Decimal(item["locked"])
            for item in account.get("balances", [])
            if Decimal(item["free"]) + Decimal(item["locked"]) > 0
        }
        raw_orders = await self._request("GET", "/api/v3/openOrders", signed=True, weight=40)
        open_orders = tuple(self._to_order(item) for item in raw_orders)
        return AccountState(
            balances=balances, open_orders=open_orders, fetched_at_ms=self.clock.now_ms()
        )

    async def submit(self, order: Order) -> OrderAck:
        """Invia un ordine. Il client order id garantisce l'idempotenza."""
        params: dict[str, Any] = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": format(order.quantity.normalize(), "f"),
            "newClientOrderId": order.client_order_id,
            "newOrderRespType": "FULL",
        }
        if order.order_type is not OrderType.MARKET:
            if order.price is None:
                raise BrokerError(f"ordine {order.client_order_id}: prezzo mancante")
            params["price"] = format(order.price.normalize(), "f")
            params["timeInForce"] = (order.time_in_force or TimeInForce.GTC).value

        started_ns = self.clock.monotonic_ns()
        payload = await self._request(
            "POST", "/api/v3/order", params=params, signed=True, weight=1
        )
        latency_ms = (self.clock.monotonic_ns() - started_ns) / 1_000_000
        return self._to_ack(payload, latency_ms)

    async def cancel(self, symbol: str, client_order_id: str) -> bool:
        try:
            await self._request(
                "DELETE",
                "/api/v3/order",
                params={"symbol": symbol, "origClientOrderId": client_order_id},
                signed=True,
            )
        except BrokerError as exc:
            # -2011 "Unknown order sent": l'ordine non esiste piu'. Non e' un
            # errore da propagare, e' la risposta alla domanda.
            if "-2011" in str(exc):
                return False
            raise
        return True

    async def order_status(self, symbol: str, client_order_id: str) -> OrderAck | None:
        try:
            payload = await self._request(
                "GET",
                "/api/v3/order",
                params={"symbol": symbol, "origClientOrderId": client_order_id},
                signed=True,
                weight=4,
            )
        except BrokerError as exc:
            if "-2013" in str(exc) or "-2011" in str(exc):
                return None  # l'exchange non conosce questo ordine
            raise
        return self._to_ack(payload, latency_ms=0.0)

    async def recent_fills(self, symbol: str, since_ms: TimestampMs) -> list[Fill]:
        payload = await self._request(
            "GET",
            "/api/v3/myTrades",
            params={"symbol": symbol, "startTime": since_ms},
            signed=True,
            weight=20,
        )
        return [
            Fill(
                client_order_id=str(item.get("clientOrderId", "")),
                symbol=item["symbol"],
                side=Side.BUY if item["isBuyer"] else Side.SELL,
                price=Decimal(item["price"]),
                quantity=Decimal(item["qty"]),
                fee=Decimal(item["commission"]),
                fee_asset=item["commissionAsset"],
                ts_ms=int(item["time"]),
                is_maker=bool(item["isMaker"]),
                trade_id=str(item["id"]),
                exchange_order_id=str(item.get("orderId")),
            )
            for item in payload
        ]

    async def depth_snapshot(
        self, symbol: str, limit: int = 100
    ) -> DepthSnapshot | None:
        """Snapshot del book, per riallineare dopo un buco di sequenza."""
        payload = await self._request(
            "GET", "/api/v3/depth", params={"symbol": symbol, "limit": limit}, weight=5
        )
        bids = tuple((Decimal(p), Decimal(q)) for p, q in payload["bids"])
        asks = tuple((Decimal(p), Decimal(q)) for p, q in payload["asks"])
        return int(payload["lastUpdateId"]), bids, asks

    async def klines(
        self, symbol: str, interval: str, *, start_ms: TimestampMs, limit: int = 1000
    ) -> list[list[Any]]:
        """Candele storiche, per popolare lo store prima di un backtest."""
        return list(
            await self._request(
                "GET",
                "/api/v3/klines",
                params={
                    "symbol": symbol,
                    "interval": interval,
                    "startTime": start_ms,
                    "limit": limit,
                },
                weight=2,
            )
        )

    async def close(self) -> None:
        await self.http.close()

    # -- conversioni -------------------------------------------------------

    def _to_ack(self, payload: Mapping[str, Any], latency_ms: float) -> OrderAck:
        executed = Decimal(str(payload.get("executedQty", "0")))
        quote_executed = Decimal(str(payload.get("cummulativeQuoteQty", "0")))
        average = quote_executed / executed if executed > 0 else None
        symbol = str(payload["symbol"])
        side = Side(str(payload["side"]))

        fills = tuple(
            Fill(
                client_order_id=str(payload["clientOrderId"]),
                symbol=symbol,
                side=side,
                price=Decimal(item["price"]),
                quantity=Decimal(item["qty"]),
                fee=Decimal(item["commission"]),
                fee_asset=item["commissionAsset"],
                ts_ms=int(payload.get("transactTime", self.clock.now_ms())),
                is_maker=bool(item.get("isMaker", False)),
                trade_id=str(item.get("tradeId", item.get("id", ""))),
                exchange_order_id=str(payload.get("orderId")),
            )
            for item in payload.get("fills", [])
        )

        return OrderAck(
            client_order_id=str(payload["clientOrderId"]),
            exchange_order_id=str(payload.get("orderId")),
            status=_STATUS_MAP.get(str(payload.get("status", "NEW")), OrderStatus.NEW),
            filled_quantity=executed,
            average_price=average,
            fills=fills,
            latency_ms=latency_ms,
            raw=dict(payload),
        )

    def _to_order(self, payload: Mapping[str, Any]) -> Order:
        return Order(
            client_order_id=str(payload["clientOrderId"]),
            symbol=str(payload["symbol"]),
            side=Side(str(payload["side"])),
            order_type=OrderType(str(payload["type"])),
            quantity=Decimal(str(payload["origQty"])),
            created_at_ms=int(payload.get("time", 0)),
            price=Decimal(str(payload["price"])) if Decimal(str(payload["price"])) > 0 else None,
            status=_STATUS_MAP.get(str(payload.get("status", "NEW")), OrderStatus.NEW),
            exchange_order_id=str(payload.get("orderId")),
            filled_quantity=Decimal(str(payload.get("executedQty", "0"))),
        )


class AiohttpClient:
    """Trasporto di produzione. Importa aiohttp solo quando serve davvero."""

    def __init__(self) -> None:
        self._session: Any = None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        import aiohttp

        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            async with self._session.request(
                method,
                url,
                headers=dict(headers or {}),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                payload = await response.json(content_type=None)
                return HttpResponse(status=response.status, payload=payload)
        except TimeoutError as exc:
            # Un timeout si comporta come un 5xx: la richiesta puo' essere
            # arrivata comunque, quindi il reinvio deve restare idempotente.
            # Nel messaggio va solo il percorso: la query contiene la firma
            # HMAC e i parametri, e questo testo finisce nel pannello.
            raise BrokerError(f"timeout su {method} {url.split('?', 1)[0]}") from exc

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
