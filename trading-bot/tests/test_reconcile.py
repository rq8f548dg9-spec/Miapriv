"""Test della riconciliazione: recupero dello stato dopo un crash.

Scenario di riferimento: il bot invia un ordine, il processo muore prima di
ricevere la risposta, l'ordine viene eseguito sull'exchange. Al riavvio il
file locale e' vecchio e la verita' sta solo sull'exchange.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import load_config
from core.clock import SimulatedClock
from core.portfolio import Portfolio
from core.types import (
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Side,
    SymbolFilters,
    TimestampMs,
)
from execution.broker import AccountState, Broker, OrderAck
from execution.idempotency import OrderRegistry, client_order_id
from execution.reconcile import Reconciler
from risk.killswitch import KillSwitch

D = Decimal
NOW = 1_700_000_000_000

FILTERS = {
    "BTCUSDT": SymbolFilters(
        symbol="BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"),
        min_quantity=D("0.00001"), max_quantity=D("9000"), min_notional=D("5"),
        base_precision=5, quote_precision=2, fetched_at_ms=NOW,
    )
}


class StubBroker(Broker):
    """Exchange finto, pilotabile dal test."""

    def __init__(
        self,
        *,
        balances: dict[str, Decimal] | None = None,
        locked: dict[str, Decimal] | None = None,
        statuses: dict[str, OrderAck | None] | None = None,
        open_orders: tuple[Order, ...] = (),
    ) -> None:
        self.balances = balances or {"USDT": D("200")}
        self.locked = locked or {}
        self.statuses = statuses or {}
        self.open_orders = open_orders
        self.status_queries: list[str] = []

    async def exchange_filters(self) -> dict[str, SymbolFilters]:
        return dict(FILTERS)

    async def account_state(self) -> AccountState:
        return AccountState(
            balances=dict(self.balances),
            locked=dict(self.locked),
            open_orders=self.open_orders,
            fetched_at_ms=NOW,
        )

    async def submit(self, order: Order) -> OrderAck:
        raise NotImplementedError

    async def cancel(self, symbol: str, client_order_id: str) -> bool:
        return False

    async def order_status(self, symbol: str, client_order_id: str) -> OrderAck | None:
        self.status_queries.append(client_order_id)
        return self.statuses.get(client_order_id)

    async def recent_fills(self, symbol: str, since_ms: TimestampMs) -> list[Fill]:
        return []


def filled_ack(order_id: str, quantity: str = "0.001", price: str = "60000") -> OrderAck:
    fill = Fill(
        client_order_id=order_id,
        symbol="BTCUSDT",
        side=Side.BUY,
        price=D(price),
        quantity=D(quantity),
        fee=D("0.06"),
        fee_asset="USDT",
        ts_ms=NOW,
        is_maker=False,
        trade_id="t1",
        exchange_order_id="x1",
    )
    return OrderAck(
        client_order_id=order_id,
        exchange_order_id="x1",
        status=OrderStatus.FILLED,
        filled_quantity=D(quantity),
        average_price=D(price),
        fills=(fill,),
        latency_ms=12.0,
    )


def pending_order(ts: int = NOW) -> Order:
    return Order(
        client_order_id=client_order_id(
            symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"), decision_ts_ms=ts
        ),
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=D("0.001"),
        created_at_ms=ts,
    )


def reconciler(broker: Broker, registry: OrderRegistry, **kwargs: object) -> Reconciler:
    """Riconciliatore con base nota e vuota.

    Equivale a un bot gia' avviato in passato che sa di non aver trovato
    nulla sul conto: e' lo scenario in cui le posizioni sull'exchange sono
    effettivamente sue. Il caso "primo avvio su un conto gia' pieno" ha la
    sua classe di test dedicata, TestSaldiPreesistenti.
    """
    kwargs.setdefault("baseline_holdings", {})
    return Reconciler(
        broker=broker,
        registry=registry,
        symbols=("BTCUSDT",),
        **kwargs,  # type: ignore[arg-type]
    )


class TestRebuildFromExchange:
    @pytest.mark.asyncio
    async def test_ricostruisce_dai_saldi_reali(self) -> None:
        """Al riavvio non ci si fida del file locale."""
        broker = StubBroker(balances={"USDT": D("140"), "BTC": D("0.001")})
        portfolio = await reconciler(broker, OrderRegistry()).rebuild_portfolio(
            prices={"BTCUSDT": D("60000")}
        )
        assert portfolio.cash == D("140")
        assert portfolio.positions["BTCUSDT"].quantity == D("0.001")
        assert portfolio.equity == D("200")

    @pytest.mark.asyncio
    async def test_nessuna_posizione_se_il_saldo_base_e_nullo(self) -> None:
        broker = StubBroker(balances={"USDT": D("200")})
        portfolio = await reconciler(broker, OrderRegistry()).rebuild_portfolio(prices={})
        assert portfolio.positions == {}

    @pytest.mark.asyncio
    async def test_pnl_non_realizzato_dichiarato_nullo(self) -> None:
        """Inventare un prezzo di carico produrrebbe un PnL falso."""
        broker = StubBroker(balances={"USDT": D("140"), "BTC": D("0.001")})
        portfolio = await reconciler(broker, OrderRegistry()).rebuild_portfolio(
            prices={"BTCUSDT": D("60000")}
        )
        assert portfolio.unrealized_pnl == 0

    @pytest.mark.asyncio
    async def test_senza_capitale_non_parte(self) -> None:
        broker = StubBroker(balances={"BTC": D("0.001")})
        with pytest.raises(ValueError, match="senza capitale"):
            await reconciler(broker, OrderRegistry()).rebuild_portfolio(prices={})

    @pytest.mark.asyncio
    async def test_posizione_senza_prezzo_blocca_lavvio(self) -> None:
        """Una posizione non valutabile non si puo' gestire: meglio non partire."""
        broker = StubBroker(balances={"USDT": D("140"), "BTC": D("0.001")})
        with pytest.raises(ValueError, match="senza prezzo"):
            await reconciler(broker, OrderRegistry()).rebuild_portfolio(prices={})


class TestPendingOrders:
    @pytest.mark.asyncio
    async def test_ordine_eseguito_mentre_il_processo_era_morto(self) -> None:
        """Il caso peggiore: inviato, risposta persa, eseguito davvero."""
        order = pending_order()
        registry = OrderRegistry()
        registry.reserve(order)
        broker = StubBroker(statuses={order.client_order_id: filled_ack(order.client_order_id)})

        resolved, fills = await reconciler(broker, registry).resolve_pending_orders()

        assert resolved == [order.client_order_id]
        assert len(fills) == 1
        assert registry.status_of(order.client_order_id) is OrderStatus.FILLED
        assert registry.pending_ids() == []

    @pytest.mark.asyncio
    async def test_ordine_mai_arrivato_allexchange(self) -> None:
        order = pending_order()
        registry = OrderRegistry()
        registry.reserve(order)
        broker = StubBroker(statuses={})  # l'exchange non lo conosce

        resolved, fills = await reconciler(broker, registry).resolve_pending_orders()

        assert resolved == [order.client_order_id]
        assert fills == []
        assert registry.status_of(order.client_order_id) is OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_ordine_parziale_resta_in_volo(self) -> None:
        """Vincolo operativo: il bot deve sopravvivere a un fill parziale."""
        order = pending_order()
        registry = OrderRegistry()
        registry.reserve(order)
        partial = OrderAck(
            client_order_id=order.client_order_id,
            exchange_order_id="x1",
            status=OrderStatus.PARTIALLY_FILLED,
            filled_quantity=D("0.0004"),
            average_price=D("60000"),
            fills=(),
            latency_ms=10.0,
        )
        broker = StubBroker(statuses={order.client_order_id: partial})

        resolved, _ = await reconciler(broker, registry).resolve_pending_orders()

        assert resolved == []
        assert registry.pending_ids() == [order.client_order_id]
        assert registry.status_of(order.client_order_id) is OrderStatus.PARTIALLY_FILLED

    @pytest.mark.asyncio
    async def test_il_registro_su_disco_sopravvive_al_riavvio(self, tmp_path: Path) -> None:
        path = tmp_path / "orders.json"
        order = pending_order()
        OrderRegistry(path).reserve(order)

        reloaded = OrderRegistry(path)
        broker = StubBroker(statuses={order.client_order_id: filled_ack(order.client_order_id)})
        resolved, fills = await reconciler(broker, reloaded).resolve_pending_orders()

        assert resolved == [order.client_order_id]
        assert len(fills) == 1


class TestReconcile:
    @pytest.mark.asyncio
    async def test_stato_allineato(self) -> None:
        portfolio = Portfolio(D("140"))
        portfolio.apply_fill(
            Fill("c", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0"), "USDT", NOW, False, "t")
        )
        broker = StubBroker(balances={"USDT": D("80"), "BTC": D("0.001")})
        result = await reconciler(broker, OrderRegistry()).reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent
        assert "allineato" in result.describe()

    @pytest.mark.asyncio
    async def test_divergenza_rilevata(self) -> None:
        portfolio = Portfolio(D("200"))  # il bot crede di essere piatto
        broker = StubBroker(balances={"USDT": D("140"), "BTC": D("0.001")})
        result = await reconciler(broker, OrderRegistry()).reconcile(portfolio, now_ms=NOW)
        assert not result.is_consistent
        assert result.differences[0].symbol == "BTCUSDT"
        assert result.differences[0].delta == D("0.001")

    @pytest.mark.asyncio
    async def test_divergenza_fa_scattare_il_kill_switch(self) -> None:
        """Operare su una posizione di dimensione ignota e' peggio che non operare."""
        config = load_config(Path("config/config.yaml"))
        switch = KillSwitch(config.risk.killswitch)
        broker = StubBroker(balances={"USDT": D("140"), "BTC": D("0.5")})
        await reconciler(broker, OrderRegistry(), killswitch=switch).reconcile(
            Portfolio(D("200")), now_ms=NOW
        )
        assert switch.is_tripped
        assert not switch.allows_opening(NOW)

    @pytest.mark.asyncio
    async def test_scarto_minimo_tollerato(self) -> None:
        """Le polveri di arrotondamento non sono una divergenza."""
        portfolio = Portfolio(D("140"))
        portfolio.apply_fill(
            Fill("c", "BTCUSDT", Side.BUY, D("60000"), D("1"), D("0"), "USDT", NOW, False, "t")
        )
        broker = StubBroker(balances={"USDT": D("80"), "BTC": D("1.0000001")})
        result = await reconciler(broker, OrderRegistry()).reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent

    @pytest.mark.asyncio
    async def test_ordine_aperto_sconosciuto_segnalato(self) -> None:
        """Un ordine che il bot non ha inviato: intervento manuale o bug."""
        stranger = Order(
            client_order_id="non-mio-123",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=D("0.001"),
            created_at_ms=NOW,
            price=D("59000"),
        )
        broker = StubBroker(open_orders=(stranger,))
        result = await reconciler(broker, OrderRegistry()).reconcile(
            Portfolio(D("200")), now_ms=NOW
        )
        assert not result.is_consistent
        assert "non-mio-123" in result.unknown_open_orders

    @pytest.mark.asyncio
    async def test_allineamento_esplicito(self) -> None:
        """La riconciliazione non riallinea in silenzio: serve una scelta."""
        portfolio = Portfolio(D("200"))
        broker = StubBroker(balances={"USDT": D("140"), "BTC": D("0.001")})
        rec = reconciler(broker, OrderRegistry())
        result = await rec.reconcile(portfolio, now_ms=NOW)
        assert portfolio.positions.get("BTCUSDT") is None

        rec.adopt_exchange_state(portfolio, result, prices={"BTCUSDT": D("60000")})
        assert portfolio.positions["BTCUSDT"].quantity == D("0.001")

    @pytest.mark.asyncio
    async def test_la_storia_e_conservata(self) -> None:
        rec = reconciler(StubBroker(), OrderRegistry())
        await rec.reconcile(Portfolio(D("200")), now_ms=NOW)
        await rec.reconcile(Portfolio(D("200")), now_ms=NOW + 1_000)
        assert len(rec.history) == 2


class TestLiquiditaERealta:
    """Il confronto riguardava solo le posizioni: un conto svuotato passava
    per allineato, e il bot continuava a dimensionare ordini su soldi che non
    c'erano piu'."""

    @pytest.mark.asyncio
    async def test_cassa_locale_maggiore_di_quella_vera_e_una_divergenza(self) -> None:
        portfolio = Portfolio(D("200"))
        broker = StubBroker(balances={"USDT": D("1")})
        result = await reconciler(broker, OrderRegistry()).reconcile(portfolio, now_ms=NOW)
        assert not result.is_consistent
        assert "liquidit" in result.describe().lower()

    @pytest.mark.asyncio
    async def test_cassa_vera_maggiore_non_e_una_divergenza(self) -> None:
        """Il tetto al capitale fa esattamente questo: il conto puo' avere
        molto piu' di quanto il bot si concede."""
        portfolio = Portfolio(D("200"))
        broker = StubBroker(balances={"USDT": D("5000")})
        rec = reconciler(broker, OrderRegistry())
        rec.max_capital = D("200")
        result = await rec.reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent

    @pytest.mark.asyncio
    async def test_un_acquisto_in_volo_non_e_una_divergenza(self) -> None:
        """Un ordine di acquisto aperto blocca la valuta, che resta sul
        conto: contarla come sparita farebbe scattare il kill switch per un
        ordine del bot stesso."""
        portfolio = Portfolio(D("200"))
        broker = StubBroker(balances={"USDT": D("194")}, locked={"USDT": D("6")})
        result = await reconciler(broker, OrderRegistry()).reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent, result.describe()

    @pytest.mark.asyncio
    async def test_le_monete_impegnate_in_un_ordine_restano_possedute(self) -> None:
        """Un BTC bloccato da un ordine di vendita aperto e' ancora nostro:
        contarlo come sparito sarebbe una divergenza inventata."""
        portfolio = Portfolio(D("140"))
        portfolio.apply_fill(
            Fill("c", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0"), "USDT", NOW, False, "t")
        )
        broker = StubBroker(balances={"USDT": D("80")}, locked={"BTC": D("0.001")})
        result = await reconciler(broker, OrderRegistry()).reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent, result.describe()

    @pytest.mark.asyncio
    async def test_la_liquidita_impegnata_non_e_spendibile(self) -> None:
        """Il saldo di un conto con un ordine di acquisto aperto e' in parte
        bloccato: sommarlo al disponibile fa dimensionare su soldi gia'
        promessi a un altro ordine."""
        broker = StubBroker(balances={"USDT": D("50")}, locked={"USDT": D("150")})
        state = await broker.account_state()
        assert state.free("USDT") == D("50")
        assert state.total("USDT") == D("200")


class TestCapitaleDichiarato:
    """Il capitale dichiarato deve limitare quello che il bot mette a
    rischio, non solo la cassa."""

    @pytest.mark.asyncio
    async def test_le_monete_gia_sul_conto_non_gonfiano_il_capitale(self) -> None:
        """Un conto con 1.000 di cassa e BTC gia' comprati a mano non diventa
        un portafoglio da 1.100 perche' il bot ci gira sopra."""
        broker = StubBroker(balances={"USDT": D("1000"), "BTC": D("0.01")})
        # Primo avvio su un conto gia' pieno: la base non e' ancora nota.
        rec = Reconciler(
            broker=broker, registry=OrderRegistry(), symbols=("BTCUSDT",),
            max_capital=D("200"),
        )
        portfolio = await rec.rebuild_portfolio(prices={"BTCUSDT": D("60000")})
        assert portfolio.cash == D("200")
        assert "BTCUSDT" not in portfolio.positions
        assert portfolio.equity == D("200")


class TestFullRestartScenario:
    @pytest.mark.asyncio
    async def test_crash_dopo_linvio_recupero_completo(self, tmp_path: Path) -> None:
        """Simulazione completa: invio, crash, riavvio, recupero."""
        path = tmp_path / "orders.json"

        # --- prima della morte: l'ordine viene registrato e inviato
        order = pending_order()
        OrderRegistry(path).reserve(order)

        # --- il processo muore qui. L'exchange pero' lo ha eseguito.
        broker = StubBroker(
            balances={"USDT": D("139.94"), "BTC": D("0.001")},
            statuses={order.client_order_id: filled_ack(order.client_order_id)},
        )

        # --- riavvio: registro riletto, stato ricostruito dall'exchange.
        # La base arriva dallo stato salvato: il conto era vuoto all'inizio,
        # quindi lo 0.001 BTC che c'e' adesso lo ha comprato il bot.
        registry = OrderRegistry(path)
        rec = reconciler(broker, registry, baseline_holdings={"BTCUSDT": D("0")})
        resolved, fills = await rec.resolve_pending_orders()
        portfolio = await rec.rebuild_portfolio(prices={"BTCUSDT": D("60000")})

        assert resolved == [order.client_order_id]
        assert len(fills) == 1
        assert portfolio.positions["BTCUSDT"].quantity == D("0.001")

        # --- e adesso lo stato combacia
        result = await rec.reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent

        # --- e il reinvio dello stesso ordine resta bloccato
        from execution.idempotency import DuplicateOrderError

        with pytest.raises(DuplicateOrderError):
            registry.reserve(order)

    @pytest.mark.asyncio
    async def test_il_clock_non_viene_letto_dal_sistema(self) -> None:
        """La riconciliazione riceve l'istante, non lo legge: backtest = live."""
        clock = SimulatedClock(NOW)
        rec = reconciler(StubBroker(), OrderRegistry())
        result = await rec.reconcile(Portfolio(D("200")), now_ms=clock.now_ms())
        assert result.ts_ms == NOW


class TestTettoDiCapitale:
    """Il capitale dichiarato deve valere anche quando il conto ne ha di piu'."""

    @pytest.mark.asyncio
    async def test_il_saldo_abbondante_viene_limitato(self) -> None:
        """In testnet il conto di prova ha migliaia di unita': senza tetto la
        prova non somiglierebbe al conto reale."""
        broker = StubBroker(balances={"USDT": D("15000")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            max_capital=D("200"),
        )
        portfolio = await rec.rebuild_portfolio(prices={})
        assert portfolio.cash == D("200")
        assert portfolio.equity == D("200")

    @pytest.mark.asyncio
    async def test_un_saldo_inferiore_al_tetto_resta_intatto(self) -> None:
        broker = StubBroker(balances={"USDT": D("120")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            max_capital=D("200"),
        )
        portfolio = await rec.rebuild_portfolio(prices={})
        assert portfolio.cash == D("120")

    @pytest.mark.asyncio
    async def test_senza_tetto_si_usa_tutto_il_saldo(self) -> None:
        broker = StubBroker(balances={"USDT": D("15000")})
        portfolio = await reconciler(broker, OrderRegistry()).rebuild_portfolio(prices={})
        assert portfolio.cash == D("15000")

    @pytest.mark.asyncio
    async def test_il_tetto_non_e_una_divergenza(self) -> None:
        """Il cash limitato non deve far scattare il kill switch."""
        broker = StubBroker(balances={"USDT": D("15000")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            max_capital=D("200"),
        )
        portfolio = await rec.rebuild_portfolio(prices={})
        result = await rec.reconcile(portfolio, now_ms=NOW)
        assert result.is_consistent


class TestSaldiPreesistenti:
    """Il conto non e' vuoto quando il bot arriva.

    La testnet accredita monete di prova (1 BTC, 1 ETH...), e un conto reale
    puo' contenere monete comprate a mano. Il bot deve gestire solo cio' che
    ha comprato lui: adottare il resto significa calcolare drawdown su un
    capitale che non controlla e, al primo segnale di uscita, vendere roba
    che non e' sua.
    """

    @pytest.mark.asyncio
    async def test_le_monete_gia_presenti_non_diventano_sue(self) -> None:
        broker = StubBroker(balances={"USDT": D("15000"), "BTC": D("1")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            max_capital=D("200"),
        )
        portfolio = await rec.rebuild_portfolio(prices={"BTCUSDT": D("60000")})

        assert portfolio.positions.get("BTCUSDT", Position("BTCUSDT")).is_flat
        assert portfolio.equity == D("200")

    @pytest.mark.asyncio
    async def test_la_quota_comprata_dal_bot_resta_sua(self) -> None:
        """Dopo un riavvio il bot deve ritrovare la propria posizione."""
        broker = StubBroker(balances={"USDT": D("15000"), "BTC": D("1.0005")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            max_capital=D("200"),
            baseline_holdings={"BTCUSDT": D("1")},
        )
        portfolio = await rec.rebuild_portfolio(prices={"BTCUSDT": D("60000")})
        assert portfolio.positions["BTCUSDT"].quantity == D("0.0005")

    @pytest.mark.asyncio
    async def test_la_base_viene_stabilita_al_primo_avvio(self) -> None:
        broker = StubBroker(balances={"USDT": D("200"), "BTC": D("2"), "ETH": D("3")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT", "ETHUSDT"),
        )
        await rec.rebuild_portfolio(prices={"BTCUSDT": D("60000"), "ETHUSDT": D("3000")})
        assert rec.baseline_holdings == {"BTCUSDT": D("2"), "ETHUSDT": D("3")}

    @pytest.mark.asyncio
    async def test_la_riconciliazione_confronta_al_netto_della_base(self) -> None:
        """Con la base sottratta, un conto pre-caricato non e' una divergenza."""
        broker = StubBroker(balances={"USDT": D("15000"), "BTC": D("1")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            max_capital=D("200"),
            baseline_holdings={"BTCUSDT": D("1")},
        )
        result = await rec.reconcile(Portfolio(D("200")), now_ms=NOW)
        assert result.is_consistent

    @pytest.mark.asyncio
    async def test_se_il_saldo_scende_sotto_la_base_la_base_si_abbassa(self) -> None:
        """Se l'utente vende a mano, il bot non puo' ritrovarsi una posizione
        negativa: abbassa il riferimento e lo dice."""
        broker = StubBroker(balances={"USDT": D("200"), "BTC": D("0.4")})
        rec = Reconciler(
            broker=broker,
            registry=OrderRegistry(),
            symbols=("BTCUSDT",),
            baseline_holdings={"BTCUSDT": D("1")},
        )
        portfolio = await rec.rebuild_portfolio(prices={"BTCUSDT": D("60000")})
        assert portfolio.positions.get("BTCUSDT", Position("BTCUSDT")).is_flat
        assert rec.baseline_holdings is not None
        assert rec.baseline_holdings["BTCUSDT"] == D("0.4")
