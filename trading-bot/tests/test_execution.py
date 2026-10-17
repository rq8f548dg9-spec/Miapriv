"""Test del client Binance: firma, throttle, retry e lettura dei filtri."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr

from config.credentials import Credentials
from config.schema import AppConfig, Mode, load_config
from core.clock import SimulatedClock
from core.types import Order, OrderStatus, OrderType, Side
from execution.binance import BinanceBroker, HttpResponse, RateLimiter, parse_symbol_filters
from execution.broker import BrokerError

D = Decimal
NOW = 1_700_000_000_000
SECRET = "segreto-di-prova"


class FakeHttp:
    """Trasporto finto: restituisce risposte predefinite e registra le richieste."""

    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, Mapping[str, str]]] = []
        self.closed = False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        self.requests.append((method, url, dict(headers or {})))
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


@pytest.fixture
def credentials() -> Credentials:
    return Credentials(
        api_key=SecretStr("chiave-di-prova"), api_secret=SecretStr(SECRET), mode=Mode.TESTNET
    )


def make_broker(config: AppConfig, credentials: Credentials, http: FakeHttp) -> BinanceBroker:
    return BinanceBroker(
        config=config, credentials=credentials, clock=SimulatedClock(NOW), http=http
    )


EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00001000",
                 "minQty": "0.00001000", "maxQty": "9000.00000000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        },
        {
            "symbol": "ETHUSDT",
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.00010000",
                 "minQty": "0.00010000", "maxQty": "9000.00000000"},
                {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
            ],
        },
    ]
}


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_consuma_e_ricarica(self) -> None:
        clock = SimulatedClock(NOW)
        limiter = RateLimiter(capacity=10, refill_per_second=10, clock=clock)
        for _ in range(10):
            await limiter.acquire(1)
        assert limiter.waits == 0

    @pytest.mark.asyncio
    async def test_attende_quando_i_token_finiscono(self) -> None:
        """Farsi bandire l'IP con una posizione aperta e' il modo peggiore
        di scoprire un rate limit."""
        clock = SimulatedClock(NOW)
        limiter = RateLimiter(capacity=2, refill_per_second=10, clock=clock)
        await limiter.acquire(2)

        import asyncio

        task = asyncio.create_task(limiter.acquire(2))
        for _ in range(20):
            await asyncio.sleep(0)
            if task.done():
                break
            wake = clock.next_wakeup_ms()
            if wake is not None:
                clock.advance_to(wake)
        await asyncio.wait_for(task, timeout=1)
        assert limiter.waits >= 1

    @pytest.mark.asyncio
    async def test_peso_oltre_la_capacita_rifiutato(self) -> None:
        limiter = RateLimiter(capacity=5, refill_per_second=5, clock=SimulatedClock(NOW))
        with pytest.raises(ValueError, match="oltre la capacita"):
            await limiter.acquire(100)


class TestExchangeInfo:
    def test_lettura_dei_filtri(self) -> None:
        filters = parse_symbol_filters(EXCHANGE_INFO["symbols"][0], NOW)
        assert filters.min_notional == D("5")
        assert filters.step_size == D("0.00001")
        assert filters.tick_size == D("0.01")

    def test_filtro_mancante_solleva(self) -> None:
        """Meglio non partire che partire con un MIN_NOTIONAL inventato."""
        broken = {"symbol": "BTCUSDT", "filters": [{"filterType": "PRICE_FILTER",
                                                    "tickSize": "0.01"}]}
        with pytest.raises(BrokerError, match="exchangeInfo incompleto"):
            parse_symbol_filters(broken, NOW)

    def test_supporta_il_vecchio_min_notional(self) -> None:
        payload = dict(EXCHANGE_INFO["symbols"][0])
        payload["filters"] = [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "10"},
        ]
        assert parse_symbol_filters(payload, NOW).min_notional == D("10")

    def test_gli_ordini_a_mercato_hanno_i_loro_limiti(self) -> None:
        """MARKET_LOT_SIZE e' un filtro separato, con step e minimi propri
        per gli ordini a mercato. Il bot invia solo ordini a mercato: usare
        lo step di LOT_SIZE significa farsi rifiutare l'ordine."""
        payload = dict(EXCHANGE_INFO["symbols"][0])
        payload["filters"] = [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001", "minQty": "0.00001",
             "maxQty": "9000"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.002",
             "maxQty": "100"},
            {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "9000000"},
        ]
        filtri = parse_symbol_filters(payload, NOW)
        assert filtri.step_size == D("0.00001")  # resta quello degli ordini limite

        mercato = filtri.per_ordine(OrderType.MARKET)
        assert mercato.step_size == D("0.001")
        assert mercato.min_quantity == D("0.002")
        assert mercato.max_quantity == D("100")
        assert mercato.round_quantity(D("0.0027")) == D("0.002")

    def test_senza_market_lot_size_valgono_i_limiti_normali(self) -> None:
        payload = dict(EXCHANGE_INFO["symbols"][0])
        filtri = parse_symbol_filters(payload, NOW)
        assert filtri.per_ordine(OrderType.MARKET).step_size == filtri.step_size

    def test_il_tetto_di_notional_viene_letto(self) -> None:
        payload = dict(EXCHANGE_INFO["symbols"][0])
        payload["filters"] = [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001",
             "maxQty": "100"},
            {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "200"},
        ]
        filtri = parse_symbol_filters(payload, NOW)
        assert filtri.max_notional == D("200")
        assert any(
            "maxNotional" in motivo or "massimo" in motivo
            for motivo in filtri.quantity_violations(D("50"), D("10"))
        ), filtri.quantity_violations(D("50"), D("10"))

    @pytest.mark.asyncio
    async def test_i_filtri_vengono_messi_in_cache(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(200, EXCHANGE_INFO)])
        broker = make_broker(config, credentials, http)
        await broker.exchange_filters()
        await broker.exchange_filters()
        assert len(http.requests) == 1

    @pytest.mark.asyncio
    async def test_simbolo_mancante_segnalato(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        partial = {"symbols": [EXCHANGE_INFO["symbols"][0]]}  # manca ETHUSDT
        broker = make_broker(config, credentials, FakeHttp([HttpResponse(200, partial)]))
        with pytest.raises(BrokerError, match="assenti da exchangeInfo"):
            await broker.exchange_filters()


class TestSigning:
    @pytest.mark.asyncio
    async def test_la_firma_e_corretta(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(200, {"balances": []}), HttpResponse(200, [])])
        broker = make_broker(config, credentials, http)
        await broker.account_state()

        _, url, headers = http.requests[0]
        query = urlparse(url).query
        payload, _, signature = query.rpartition("&signature=")
        expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        assert signature == expected
        assert headers["X-MBX-APIKEY"] == "chiave-di-prova"

    @pytest.mark.asyncio
    async def test_il_segreto_non_compare_nella_query(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(200, {"balances": []}), HttpResponse(200, [])])
        await make_broker(config, credentials, http).account_state()
        assert all(SECRET not in url for _, url, _ in http.requests)

    @pytest.mark.asyncio
    async def test_recv_window_incluso(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(200, {"balances": []}), HttpResponse(200, [])])
        await make_broker(config, credentials, http).account_state()
        params = parse_qs(urlparse(http.requests[0][1]).query)
        assert params["recvWindow"] == [str(config.exchange.recv_window_ms)]
        assert "timestamp" in params

    def test_lurl_dipende_dalla_modalita(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        testnet = make_broker(config, credentials, FakeHttp([]))
        assert "testnet" in testnet.base_url


class TestRetry:
    @pytest.mark.asyncio
    async def _run(self, broker: BinanceBroker, clock: SimulatedClock) -> object:
        import asyncio

        task = asyncio.create_task(broker.exchange_filters())
        for _ in range(60):
            await asyncio.sleep(0)
            if task.done():
                break
            wake = clock.next_wakeup_ms()
            if wake is not None:
                clock.advance_to(wake)
        return await asyncio.wait_for(task, timeout=2)

    @pytest.mark.asyncio
    async def test_i_5xx_vengono_ritentati(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        """Vincolo operativo: il bot deve sopravvivere a un exchange che da' 5xx."""
        http = FakeHttp(
            [HttpResponse(503, {"msg": "service unavailable"}), HttpResponse(200, EXCHANGE_INFO)]
        )
        clock = SimulatedClock(NOW)
        broker = BinanceBroker(
            config=config, credentials=credentials, clock=clock, http=http
        )
        await self._run(broker, clock)
        assert broker.retries == 1
        assert len(http.requests) == 2

    @pytest.mark.asyncio
    async def test_i_429_vengono_ritentati(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(429, {"msg": "too many"}), HttpResponse(200, EXCHANGE_INFO)])
        clock = SimulatedClock(NOW)
        broker = BinanceBroker(config=config, credentials=credentials, clock=clock, http=http)
        await self._run(broker, clock)
        assert broker.retries == 1

    @pytest.mark.asyncio
    async def test_il_tentativo_successivo_viene_rifirmato(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        """La firma include l'istante e vale pochi secondi (recvWindow).

        Firmandola una volta sola prima del ciclo, dopo un backoff di
        decine di secondi il secondo tentativo arriva con un orario
        scaduto: l'exchange lo rifiuta, e il retry e' sprecato proprio
        quando serviva."""
        import asyncio
        from urllib.parse import parse_qs, urlparse

        http = FakeHttp(
            [
                HttpResponse(503, {"msg": "service unavailable"}),
                HttpResponse(200, {"balances": [], "canTrade": True}),
            ]
        )
        clock = SimulatedClock(NOW)
        broker = BinanceBroker(
            config=config, credentials=credentials, clock=clock, http=http
        )
        http.responses.append(HttpResponse(200, []))  # openOrders

        task = asyncio.create_task(broker.account_state())
        for _ in range(60):
            await asyncio.sleep(0)
            if task.done():
                break
            wake = clock.next_wakeup_ms()
            if wake is not None:
                clock.advance_to(wake)
        await asyncio.wait_for(task, timeout=2)

        primi_due = [parse_qs(urlparse(url).query) for _, url, _ in http.requests[:2]]
        assert primi_due[0]["timestamp"] != primi_due[1]["timestamp"]
        assert primi_due[0]["signature"] != primi_due[1]["signature"]

    @pytest.mark.asyncio
    async def test_i_4xx_non_vengono_ritentati(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        """Ripetere una richiesta sbagliata la rende solo sbagliata due volte."""
        http = FakeHttp([HttpResponse(400, {"code": -1013, "msg": "filtro violato"})])
        broker = make_broker(config, credentials, http)
        with pytest.raises(BrokerError, match="rifiutata dall'exchange"):
            await broker.exchange_filters()
        assert broker.retries == 0
        assert len(http.requests) == 1

    @pytest.mark.asyncio
    async def test_si_arrende_dopo_i_tentativi_massimi(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(500, {"msg": "ko"})])
        clock = SimulatedClock(NOW)
        broker = BinanceBroker(config=config, credentials=credentials, clock=clock, http=http)
        import asyncio

        task = asyncio.create_task(broker.exchange_filters())
        for _ in range(100):
            await asyncio.sleep(0)
            if task.done():
                break
            wake = clock.next_wakeup_ms()
            if wake is not None:
                clock.advance_to(wake)
        with pytest.raises(BrokerError, match="dopo"):
            await task
        assert broker.retries == config.exchange.max_retries


class TestSubmit:
    @pytest.mark.asyncio
    async def test_invio_di_un_market_order(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        response = {
            "symbol": "BTCUSDT",
            "clientOrderId": "tb-abc123",
            "orderId": 99,
            "side": "BUY",
            "status": "FILLED",
            "executedQty": "0.00100000",
            "cummulativeQuoteQty": "60.00000000",
            "transactTime": NOW,
            "fills": [
                {"price": "60000.00", "qty": "0.00100000", "commission": "0.06",
                 "commissionAsset": "USDT", "tradeId": 7, "isMaker": False}
            ],
        }
        http = FakeHttp([HttpResponse(200, response)])
        broker = make_broker(config, credentials, http)
        order = Order(
            client_order_id="tb-abc123",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=D("0.001"),
            created_at_ms=NOW,
        )
        ack = await broker.submit(order)
        assert ack.status is OrderStatus.FILLED
        assert ack.filled_quantity == D("0.001")
        assert ack.average_price == D("60000")
        assert len(ack.fills) == 1

        params = parse_qs(urlparse(http.requests[0][1]).query)
        assert params["newClientOrderId"] == ["tb-abc123"]
        assert params["quantity"] == ["0.001"]

    @pytest.mark.asyncio
    async def test_fill_parziale_riconosciuto(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        response = {
            "symbol": "BTCUSDT", "clientOrderId": "tb-x", "orderId": 1, "side": "BUY",
            "status": "PARTIALLY_FILLED", "executedQty": "0.00040000",
            "cummulativeQuoteQty": "24.00000000", "transactTime": NOW, "fills": [],
        }
        broker = make_broker(config, credentials, FakeHttp([HttpResponse(200, response)]))
        ack = await broker.submit(
            Order("tb-x", "BTCUSDT", Side.BUY, OrderType.MARKET, D("0.001"), NOW)
        )
        assert ack.is_partial
        assert ack.filled_quantity == D("0.0004")

    @pytest.mark.asyncio
    async def test_ordine_sconosciuto_non_e_un_errore(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        """-2013 significa 'non lo conosco', ed e' una risposta valida."""
        http = FakeHttp([HttpResponse(400, {"code": -2013, "msg": "Order does not exist."})])
        broker = make_broker(config, credentials, http)
        assert await broker.order_status("BTCUSDT", "tb-ignoto") is None

    @pytest.mark.asyncio
    async def test_annullamento_di_un_ordine_inesistente(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([HttpResponse(400, {"code": -2011, "msg": "Unknown order sent."})])
        broker = make_broker(config, credentials, http)
        assert await broker.cancel("BTCUSDT", "tb-ignoto") is False

    @pytest.mark.asyncio
    async def test_chiusura_rilascia_il_trasporto(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([])
        await make_broker(config, credentials, http).close()
        assert http.closed


class TestInvioAmbiguo:
    """Un 5xx o un timeout non dicono che l'ordine non e' passato: dicono che
    non si sa. Rimandarlo e' il modo piu' diretto per ritrovarsi con due
    posizioni; Binance consente di riusare un client id quando il precedente
    e' gia' eseguito, quindi non protegge da solo."""

    def _order(self) -> Order:
        return Order(
            client_order_id="tb-ambiguo",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            quantity=D("0.001"),
            created_at_ms=NOW,
        )

    ESEGUITO: ClassVar[dict[str, object]] = {
        "symbol": "BTCUSDT", "clientOrderId": "tb-ambiguo", "orderId": 7, "side": "BUY",
        "status": "FILLED", "executedQty": "0.00100000",
        "cummulativeQuoteQty": "60.00000000", "transactTime": NOW, "fills": [],
    }

    @pytest.mark.asyncio
    async def test_dopo_un_5xx_non_reinvia_ma_domanda(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([
            HttpResponse(503, {"msg": "service unavailable"}),  # POST ambiguo
            HttpResponse(200, self.ESEGUITO),                    # GET di verifica
        ])
        clock = SimulatedClock(NOW)
        broker = BinanceBroker(config=config, credentials=credentials, clock=clock, http=http)

        ack = await broker.submit(self._order())

        metodi = [metodo for metodo, _, _ in http.requests]
        assert metodi.count("POST") == 1, "l'ordine non deve essere reinviato"
        assert "GET" in metodi, "lo stato va chiesto all'exchange"
        assert ack.status is OrderStatus.FILLED
        assert ack.filled_quantity == D("0.001")

    @pytest.mark.asyncio
    async def test_se_lexchange_non_lo_conosce_lordine_non_e_partito(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        http = FakeHttp([
            HttpResponse(500, {"msg": "ko"}),
            HttpResponse(400, {"code": -2013, "msg": "Order does not exist."}),
        ])
        clock = SimulatedClock(NOW)
        broker = BinanceBroker(config=config, credentials=credentials, clock=clock, http=http)

        with pytest.raises(BrokerError, match=r"esito sconosciuto|non risulta"):
            await broker.submit(self._order())
        assert [m for m, _, _ in http.requests].count("POST") == 1

    @pytest.mark.asyncio
    async def test_un_rifiuto_certo_resta_un_rifiuto(
        self, config: AppConfig, credentials: Credentials
    ) -> None:
        """Un 400 e' una risposta chiara: non serve interrogare nessuno."""
        http = FakeHttp([HttpResponse(400, {"code": -1013, "msg": "filtro violato"})])
        broker = make_broker(config, credentials, http)
        with pytest.raises(BrokerError, match="rifiutata"):
            await broker.submit(self._order())
        assert len(http.requests) == 1
