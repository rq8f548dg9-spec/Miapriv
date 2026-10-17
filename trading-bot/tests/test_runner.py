"""Test di integrazione del cablaggio: dal dato di mercato all'ordine eseguito."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backtest.costs import CostModel
from config.schema import AppConfig, CostsConfig, load_config
from core.clock import SimulatedClock
from core.portfolio import Portfolio
from core.types import Candle, Fill, Order, OrderStatus, OrderType, Side, SymbolFilters, Trade
from data.orderbook import DepthUpdate
from execution.broker import PaperBroker
from execution.idempotency import OrderRegistry
from monitoring.alerts import Alert, AlertManager
from monitoring.logger import DecisionLogger
from monitoring.state import StateStore
from runner import TradingBot, build_generator
from tests.test_no_lookahead import AlwaysTradingGenerator

D = Decimal
NOW = 1_700_000_000_000
MINUTE = 60_000


def filters_for(symbol: str) -> SymbolFilters:
    return SymbolFilters(
        symbol=symbol, tick_size=D("0.01"), step_size=D("0.00001"),
        min_quantity=D("0.00001"), max_quantity=D("9000"), min_notional=D("5"),
        base_precision=5, quote_precision=2, fetched_at_ms=NOW,
    )


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


@pytest.fixture
def bot(config: AppConfig, tmp_path: Path) -> TradingBot:
    clock = SimulatedClock(NOW)
    broker = PaperBroker(
        clock=clock,
        costs=CostModel(CostsConfig()),
        filters={"BTCUSDT": filters_for("BTCUSDT"), "ETHUSDT": filters_for("ETHUSDT")},
        balances={"USDT": D("200")},
    )
    broker.set_price("BTCUSDT", D("60000"))
    broker.set_price("ETHUSDT", D("3000"))
    instance = TradingBot(
        config=config,
        clock=clock,
        broker=broker,
        generator=AlwaysTradingGenerator(),
        portfolio=Portfolio(D("200")),
        registry=OrderRegistry(tmp_path / "orders.json"),
        alerts=AlertManager(clock),
        decision_log=DecisionLogger(mode="test"),
        state_store=StateStore(tmp_path / "state.json", clock, mode="test"),
        store=None,
    )
    instance.filters = {"BTCUSDT": filters_for("BTCUSDT"), "ETHUSDT": filters_for("ETHUSDT")}
    return instance


def feed_candles(bot: TradingBot, count: int, *, symbol: str = "BTCUSDT") -> None:
    """Riempie il warmup con candele crescenti, facendo scorrere l'orologio.

    Il tempo avanza insieme ai dati, come dal vivo: una fixture che consegna
    trecento candele a orologio fermo crea uno stato che non esiste, e fa
    passare o fallire i test per il motivo sbagliato.
    """
    price = D("60000")
    for i in range(count):
        price = (price * D("1.0005")).quantize(D("0.01"))
        bot.clock.advance_to(NOW + (i + 1) * MINUTE - 1)  # type: ignore[attr-defined]
        # Anche il mercato finto si muove: un exchange fermo a 60.000 mentre
        # le candele salgono crea un mondo che non esiste, e fa fallire o
        # passare i test per il motivo sbagliato.
        bot.broker.set_price(symbol, price)  # type: ignore[attr-defined]
        bot.on_candle(
            Candle(
                symbol=symbol,
                open_time_ms=NOW + i * MINUTE,
                close_time_ms=NOW + (i + 1) * MINUTE - 1,
                open=price,
                high=price * D("1.001"),
                low=price * D("0.999"),
                close=price,
                volume=D("10"),
                quote_volume=price * D("10"),
                trades=25,
                is_closed=True,
            )
        )


class TestWiring:
    def test_il_generatore_viene_scelto_dalla_configurazione(self, config: AppConfig) -> None:
        assert build_generator(config).name == "rules"

    def test_i_book_sono_creati_per_i_simboli_abilitati(self, bot: TradingBot) -> None:
        assert set(bot.books) == {"BTCUSDT", "ETHUSDT"}

    def test_le_candele_aperte_sono_ignorate(self, bot: TradingBot) -> None:
        """Una candela in formazione non deve entrare nel feature engine."""
        bot.on_candle(
            Candle(
                symbol="BTCUSDT", open_time_ms=NOW, close_time_ms=NOW + MINUTE - 1,
                open=D("60000"), high=D("60100"), low=D("59900"), close=D("60050"),
                volume=D("1"), quote_volume=D("60000"), trades=1, is_closed=False,
            )
        )
        assert bot.features.state_for("BTCUSDT").bars_seen == 0

    def test_i_trade_alimentano_order_flow_e_prezzi(self, bot: TradingBot) -> None:
        bot.on_trade(Trade("BTCUSDT", 1, D("60500"), D("0.5"), NOW, False))
        assert bot.last_prices["BTCUSDT"] == D("60500")


class TestDecisionFlow:
    @pytest.mark.asyncio
    async def test_dalla_candela_allordine_eseguito(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        # Nessun salto temporale aggiuntivo: la fixture fa gia' scorrere
        # l'orologio con i dati, e un minuto di silenzio in piu' farebbe
        # scattare il controllo sul feed fermo, giustamente.
        order = await bot.evaluate_symbol("BTCUSDT")
        assert order is not None
        assert bot.portfolio.positions["BTCUSDT"].quantity > 0
        assert bot.decision_log.decisions_logged >= 1

    @pytest.mark.asyncio
    async def test_niente_ordini_prima_del_warmup(self, bot: TradingBot) -> None:
        feed_candles(bot, 10)
        assert await bot.evaluate_symbol("BTCUSDT") is None

    @pytest.mark.asyncio
    async def test_il_reinvio_e_bloccato_dal_registro(self, bot: TradingBot) -> None:
        """Due decisioni identiche nello stesso secondo: un solo ordine."""
        feed_candles(bot, 300)
        first = await bot.evaluate_symbol("BTCUSDT")
        assert first is not None
        fills_after_first = len(bot.broker.fills)  # type: ignore[attr-defined]
        await bot.submit(first)
        assert len(bot.broker.fills) == fills_after_first  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_senza_prezzo_non_si_decide(self, bot: TradingBot) -> None:
        feed_candles(bot, 300, symbol="ETHUSDT")
        bot.last_prices.pop("ETHUSDT", None)
        assert await bot.evaluate_symbol("ETHUSDT") is None


class TestOrdineInviabile:
    """Un ordine piu' piccolo del minimo della coppia non verra' mai eseguito.
    Il bot deve dirlo all'avvio, non lasciare l'utente davanti a un pannello
    che non opera mai senza spiegare perche'."""

    @pytest.mark.asyncio
    async def test_avvisa_se_lordine_dichiarato_e_sotto_il_minimo(
        self, bot: TradingBot
    ) -> None:
        avvisi: list[str] = []
        bot.alerts.channels = [_CanaleDiProva(avvisi)]
        bot.broker.filters["BTCUSDT"] = SymbolFilters(  # type: ignore[attr-defined]
            symbol="BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"),
            min_quantity=D("0.00001"), max_quantity=D("9000"), min_notional=D("50"),
            base_precision=5, quote_precision=2, fetched_at_ms=NOW,
        )
        await bot.startup()
        assert any("50" in avviso and "BTCUSDT" in avviso for avviso in avvisi), avvisi

    @pytest.mark.asyncio
    async def test_niente_avviso_se_lordine_e_inviabile(self, bot: TradingBot) -> None:
        avvisi: list[str] = []
        bot.alerts.channels = [_CanaleDiProva(avvisi)]
        await bot.startup()
        assert not [a for a in avvisi if "minimo" in a], avvisi


class _CanaleDiProva:
    """Canale di allerta che raccoglie invece di scrivere."""

    name = "prova"

    def __init__(self, raccolta: list[str]) -> None:
        self.raccolta = raccolta

    def send(self, alert: Alert) -> None:
        self.raccolta.append(f"{alert.title} {alert.detail}")


class TestResilienza:
    def test_la_disconnessione_invalida_i_book(self, bot: TradingBot) -> None:
        """Gli eventi persi durante la caduta non si recuperano."""
        book = bot.books["BTCUSDT"]
        book.apply_snapshot(
            last_update_id=10,
            bids=((D("60000"), D("1")),),
            asks=((D("60010"), D("1")),),
            ts_ms=NOW,
        )
        synced_before = book.is_synced
        bot.on_disconnect(NOW, "connessione persa")
        assert (synced_before, book.is_synced) == (True, False)
        assert "BTCUSDT" in bot.needs_resync
        assert bot.alerts.sent == 1

    def test_il_buco_di_sequenza_richiede_un_resync(self, bot: TradingBot) -> None:
        book = bot.books["BTCUSDT"]
        book.apply_snapshot(
            last_update_id=10, bids=((D("60000"), D("1")),), asks=((D("60010"), D("1")),),
            ts_ms=NOW,
        )
        bot.on_depth(DepthUpdate("BTCUSDT", 11, 20, (), (), NOW))
        assert "BTCUSDT" not in bot.needs_resync
        bot.on_depth(DepthUpdate("BTCUSDT", 50, 60, (), (), NOW))  # buco
        assert "BTCUSDT" in bot.needs_resync
        assert not book.is_synced

    def test_la_disconnessione_conta_per_il_kill_switch(self, bot: TradingBot) -> None:
        for i in range(bot.config.risk.killswitch.max_reconnects_per_hour):
            bot.on_disconnect(NOW + i, "caduta")
        events = bot.risk.killswitch.check(bot.portfolio.snapshot(NOW), now_ms=NOW)
        assert any(e.reason.value == "RECONNECT_STORM" for e in events)

    @pytest.mark.asyncio
    async def test_il_kill_switch_chiude_le_posizioni(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        await bot.evaluate_symbol("BTCUSDT")
        assert bot.portfolio.positions["BTCUSDT"].quantity > 0

        bot.risk.killswitch.trip_manually("stop di prova", bot.clock.now_ms())
        bot.clock.advance_by(60_000)  # type: ignore[attr-defined]
        await bot.check_killswitch()
        assert bot.portfolio.positions["BTCUSDT"].is_flat
        assert not bot.risk.killswitch.flatten_pending

    @pytest.mark.asyncio
    async def test_dopo_lo_stop_non_si_apre_piu(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        bot.risk.killswitch.trip_manually("stop", bot.clock.now_ms())
        bot.clock.advance_by(120_000)  # type: ignore[attr-defined]
        assert await bot.evaluate_symbol("BTCUSDT") is None


class TestStato:
    def test_lo_snapshot_viene_scritto(self, bot: TradingBot, tmp_path: Path) -> None:
        assert bot.snapshot_state(force=True)
        assert (tmp_path / "state.json").is_file()

    @pytest.mark.asyncio
    async def test_lo_stato_rispecchia_le_posizioni(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        await bot.evaluate_symbol("BTCUSDT")
        bot.snapshot_state(force=True)
        state = bot.state_store.load()
        assert state is not None
        assert "BTCUSDT" in state.positions

    @pytest.mark.asyncio
    async def test_la_riconciliazione_periodica_gira(self, bot: TradingBot) -> None:
        await bot.periodic_reconcile()
        assert bot.reconciler.history


class TestBaseDelleMonete:
    """Il bot deve gestire solo cio' che ha comprato lui, anche dopo un riavvio."""

    def _bot(self, config: AppConfig, tmp_path: Path, clock: SimulatedClock) -> TradingBot:
        broker = PaperBroker(
            clock=clock,
            costs=CostModel(CostsConfig()),
            filters={"BTCUSDT": filters_for("BTCUSDT")},
            balances={"USDT": D("200")},
        )
        return TradingBot(
            config=config,
            clock=clock,
            broker=broker,
            generator=AlwaysTradingGenerator(),
            portfolio=Portfolio(D("200")),
            registry=OrderRegistry(tmp_path / "orders.json"),
            alerts=AlertManager(clock),
            decision_log=DecisionLogger(mode="test"),
            state_store=StateStore(tmp_path / "state.json", clock, mode="test"),
            store=None,
        )

    def test_senza_stato_salvato_la_base_e_da_stabilire(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        bot = self._bot(config, tmp_path, SimulatedClock(NOW))
        assert bot.reconciler.baseline_holdings is None

    def test_la_base_salvata_viene_riletta_allavvio(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        """Senza rilettura, dopo un riavvio la posizione del bot sparirebbe."""
        clock = SimulatedClock(NOW)
        primo = self._bot(config, tmp_path, clock)
        primo.reconciler.baseline_holdings = {"BTCUSDT": D("1.25")}
        primo.snapshot_state(force=True)

        secondo = self._bot(config, tmp_path, clock)
        assert secondo.reconciler.baseline_holdings == {"BTCUSDT": D("1.25")}

    def test_la_base_finisce_nello_snapshot(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        bot = self._bot(config, tmp_path, SimulatedClock(NOW))
        bot.reconciler.baseline_holdings = {"BTCUSDT": D("2")}
        bot.snapshot_state(force=True)
        saved = bot.state_store.load()
        assert saved is not None
        assert saved.baseline_holdings == {"BTCUSDT": "2"}


class TestTracciabilita:
    """Senza un identificativo comune non si puo' risalire dall'ordine
    eseguito alla decisione che lo ha prodotto, e l'autopsia salta."""

    @pytest.mark.asyncio
    async def test_lordine_inviato_porta_lid_della_decisione(
        self, bot: TradingBot
    ) -> None:
        feed_candles(bot, 300)
        order = await bot.evaluate_symbol("BTCUSDT")
        assert order is not None

        decisione = bot.risk.decisions[-1]
        assert decisione.order is not None
        assert decisione.order.client_order_id == order.client_order_id
        assert order.client_order_id in bot.registry

        fill = bot.broker.fills[-1]  # type: ignore[attr-defined]
        assert fill.client_order_id == order.client_order_id

    @pytest.mark.asyncio
    async def test_lid_dipende_dalla_quantita(self, bot: TradingBot) -> None:
        """Due decisioni diverse non devono condividere l'identificativo."""
        from decimal import Decimal

        from core.types import Side

        primo = bot.risk.client_order_id_factory(
            symbol="BTCUSDT", side=Side.BUY, quantity=Decimal("0.001"),
            intent="OPEN", ts_ms=NOW,
        )
        secondo = bot.risk.client_order_id_factory(
            symbol="BTCUSDT", side=Side.BUY, quantity=Decimal("0.002"),
            intent="OPEN", ts_ms=NOW,
        )
        assert primo != secondo


class TestProtezioniPersistenti:
    """Un riavvio non deve cancellare le protezioni: il container ha
    restart automatico, e un kill switch dimenticato significa che il bot
    riprende a operare proprio dopo che qualcosa e' andato storto."""

    def _bot(self, config: AppConfig, tmp_path: Path, clock: SimulatedClock) -> TradingBot:
        broker = PaperBroker(
            clock=clock,
            costs=CostModel(CostsConfig()),
            filters={"BTCUSDT": filters_for("BTCUSDT")},
            balances={"USDT": D("200")},
        )
        broker.set_price("BTCUSDT", D("60000"))
        return TradingBot(
            config=config,
            clock=clock,
            broker=broker,
            generator=AlwaysTradingGenerator(),
            portfolio=Portfolio(D("200")),
            registry=OrderRegistry(tmp_path / "orders.json"),
            alerts=AlertManager(clock),
            decision_log=DecisionLogger(mode="test"),
            state_store=StateStore(tmp_path / "state.json", clock, mode="test"),
            store=None,
        )

    def test_il_kill_switch_sopravvive_al_riavvio(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        clock = SimulatedClock(NOW)
        primo = self._bot(config, tmp_path, clock)
        primo.risk.killswitch.trip_manually("drawdown di prova", NOW)
        primo.snapshot_state(force=True)

        secondo = self._bot(config, tmp_path, clock)
        assert secondo.risk.killswitch.is_tripped
        assert not secondo.risk.killswitch.allows_opening(NOW)

    def test_anche_il_massimo_storico_sopravvive(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        """Senza il massimo storico il drawdown totale riparte da zero e il
        limite complessivo non scatta mai."""
        clock = SimulatedClock(NOW)
        primo = self._bot(config, tmp_path, clock)
        primo.portfolio.peak_equity = D("260")
        primo.portfolio.day_start_equity = D("250")
        primo.snapshot_state(force=True)

        secondo = self._bot(config, tmp_path, clock)
        assert secondo.portfolio.peak_equity == D("260")
        assert secondo.portfolio.day_start_equity == D("250")

    def test_il_cooldown_riparte_dallistante_salvato(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        clock = SimulatedClock(NOW)
        primo = self._bot(config, tmp_path, clock)
        primo.risk.killswitch.trip_manually("stop", NOW)
        primo.snapshot_state(force=True)

        secondo = self._bot(config, tmp_path, clock)
        assert secondo.risk.killswitch.tripped_at_ms == NOW


class TestSupervisione:
    @pytest.mark.asyncio
    async def test_il_prezzo_dei_trade_aggiorna_lequity(self, bot: TradingBot) -> None:
        """F07: con il mark fermo all'ultima candela, il drawdown legge uno
        stato vecchio fino a un minuto."""
        feed_candles(bot, 5)
        bot.portfolio.apply_fill(
            Fill("x", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0"), "USDT",
                 NOW, False, "t")
        )
        prima = bot.portfolio.equity
        bot.on_trade(Trade("BTCUSDT", 1, D("30000"), D("0.1"), NOW + 1, False))
        assert bot.portfolio.equity < prima

    def test_una_candela_aperta_non_vale_come_segno_di_vita(
        self, bot: TradingBot
    ) -> None:
        """F16: registrare la chiusura futura di una candela in formazione
        nasconde un feed fermo."""
        futura = Candle(
            symbol="BTCUSDT", open_time_ms=NOW, close_time_ms=NOW + 3_600_000,
            open=D("60000"), high=D("60000"), low=D("60000"), close=D("60000"),
            volume=D("1"), quote_volume=D("60000"), trades=1, is_closed=False,
        )
        bot.clock.advance_by(1_000)  # type: ignore[attr-defined]
        bot.on_candle(futura)
        eventi = bot.risk.killswitch.check(
            bot.portfolio.snapshot(bot.clock.now_ms() + 60_000),
            now_ms=bot.clock.now_ms() + 60_000,
        )
        assert any(e.reason.value == "DATA_STALENESS" for e in eventi)

    @pytest.mark.asyncio
    async def test_un_guasto_nel_resync_non_salta_il_kill_switch(
        self, bot: TradingBot
    ) -> None:
        """F06: se tutti i compiti stanno nello stesso try, un errore in uno
        impedisce i controlli di sicurezza degli altri."""
        chiamate: list[int] = []

        async def resync_rotto() -> None:
            raise RuntimeError("book irraggiungibile")

        originale = bot.check_killswitch

        async def conta() -> None:
            chiamate.append(1)
            await originale()

        bot.resync_books = resync_rotto  # type: ignore[method-assign]
        bot.check_killswitch = conta  # type: ignore[method-assign]
        bot.running = True
        await bot._supervise(max_iterations=1)
        assert chiamate == [1]

    @pytest.mark.asyncio
    async def test_le_decisioni_non_crescono_senza_limite(
        self, bot: TradingBot
    ) -> None:
        """F31: dieci decisioni al secondo per giorni esauriscono la memoria."""
        from core.types import Decision

        for i in range(bot.risk.max_decisions_kept + 500):
            bot.risk.decisions.append(
                Decision(NOW + i, "BTCUSDT", None, False, rejection_reasons=("x",))
            )
        bot.risk.trim_decisions()
        assert len(bot.risk.decisions) <= bot.risk.max_decisions_kept


class TestChiusuraDiEmergenza:
    """Chiudere le posizioni non basta: un ordine di acquisto gia' inviato e
    non ancora eseguito puo' riaprire esposizione subito dopo lo stop."""

    @pytest.mark.asyncio
    async def test_annulla_anche_gli_ordini_in_volo(self, bot: TradingBot) -> None:
        from core.types import Order, OrderType

        in_volo = Order(
            client_order_id="tb-in-volo",
            symbol="BTCUSDT",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity=D("0.001"),
            created_at_ms=NOW,
            price=D("59000"),
        )
        bot.registry.reserve(in_volo)
        annullati: list[str] = []

        async def cancel(symbol: str, client_order_id: str) -> bool:
            annullati.append(client_order_id)
            return True

        bot.broker.cancel = cancel  # type: ignore[method-assign]
        await bot.flatten_all()
        assert annullati == ["tb-in-volo"]

    @pytest.mark.asyncio
    async def test_non_si_dichiara_chiuso_se_resta_esposizione(
        self, bot: TradingBot
    ) -> None:
        """F05: marcare la chiusura come fatta dopo il solo tentativo lascia
        il kill switch convinto di aver messo tutto al sicuro."""
        feed_candles(bot, 300)
        await bot.evaluate_symbol("BTCUSDT")
        assert not bot.portfolio.positions["BTCUSDT"].is_flat

        async def rifiuta(order: object) -> None:
            raise RuntimeError("exchange non raggiungibile")

        bot.submit = rifiuta  # type: ignore[method-assign]
        bot.risk.killswitch.trip_manually("stop", bot.clock.now_ms())
        await bot.check_killswitch()

        assert not bot.portfolio.positions["BTCUSDT"].is_flat
        assert bot.risk.killswitch.flatten_pending, "la chiusura non e' avvenuta"


class TestFillRecuperati:
    """Gli ordini risolti al riavvio portano con se' le esecuzioni: se non
    vengono applicate, il portafoglio resta indietro; se vengono applicate
    due volte, raddoppia."""

    @pytest.mark.asyncio
    async def test_un_fill_recuperato_entra_nel_portafoglio(
        self, bot: TradingBot
    ) -> None:
        fill = Fill("tb-x", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0.06"),
                    "USDT", NOW, False, "tr-1")
        bot.apply_fill(fill)
        assert bot.portfolio.positions["BTCUSDT"].quantity == D("0.001")

    @pytest.mark.asyncio
    async def test_lo_stesso_fill_applicato_due_volte_conta_una(
        self, bot: TradingBot
    ) -> None:
        fill = Fill("tb-x", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0.06"),
                    "USDT", NOW, False, "tr-1")
        bot.apply_fill(fill)
        bot.apply_fill(fill)
        assert bot.portfolio.positions["BTCUSDT"].quantity == D("0.001")

    @pytest.mark.asyncio
    async def test_un_doppione_non_finisce_nemmeno_nei_log(
        self, bot: TradingBot
    ) -> None:
        """Il log e' l'autopsia: due righe per un'esecuzione sola
        racconterebbero un'operazione che non e' mai avvenuta."""
        import logging

        righe: list[str] = []

        class Conta(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                contesto = getattr(record, "context", {})
                righe.append(str(contesto.get("event")))

        finto = logging.getLogger("test.doppioni")
        finto.handlers = [Conta()]
        finto.setLevel(logging.INFO)
        finto.propagate = False
        bot.decision_log = DecisionLogger(finto, mode="test")

        fill = Fill("tb-z", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0.06"),
                    "USDT", NOW, False, "tr-2")
        bot.apply_fill(fill)
        bot.apply_fill(fill)
        assert righe.count("fill") == 1
        assert righe.count("equity") == 1

    @pytest.mark.asyncio
    async def test_un_ordine_parziale_ripescato_due_volte_conta_una(
        self, bot: TradingBot
    ) -> None:
        """Un ordine eseguito a meta' resta aperto: la riconciliazione lo
        ripesca a ogni giro e ogni volta riporta gli stessi fill."""
        from execution.broker import OrderAck

        fill = Fill("tb-y", "BTCUSDT", Side.BUY, D("60000"), D("0.002"), D("0.12"),
                    "USDT", NOW, False, "tr-9")
        ack = OrderAck(
            client_order_id="tb-y", exchange_order_id="9",
            status=OrderStatus.PARTIALLY_FILLED, filled_quantity=D("0.002"),
            average_price=D("60000"), fills=(fill,), latency_ms=1.0,
        )

        async def stato(symbol: str, client_order_id: str) -> OrderAck:
            return ack

        ordine = Order(
            client_order_id="tb-y", symbol="BTCUSDT", side=Side.BUY,
            order_type=OrderType.LIMIT, quantity=D("0.004"), created_at_ms=NOW,
            price=D("60000"),
        )
        bot.registry.reserve(ordine)
        bot.broker.order_status = stato  # type: ignore[method-assign]

        await bot.periodic_reconcile()
        assert bot.portfolio.positions["BTCUSDT"].quantity == D("0.002")

        await bot.periodic_reconcile()
        assert bot.portfolio.positions["BTCUSDT"].quantity == D("0.002")
