"""Test del data layer: buffer, ricostruzione book, ingest, persistenza."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import AppConfig, load_config
from core.clock import SimulatedClock
from core.types import Candle, TimestampMs, Trade
from data.buffer import RingBuffer
from data.ingest import MarketDataIngestor, parse_stream_message
from data.orderbook import ApplyResult, DepthUpdate, OrderBookState
from data.store import ParquetStore, day_partition

D = Decimal
NOW = 1_700_000_000_000


class TestRingBuffer:
    def test_append_e_lettura(self) -> None:
        buffer: RingBuffer[int] = RingBuffer(5)
        buffer.extend([1, 2, 3])
        assert buffer.snapshot() == [1, 2, 3]
        assert buffer.last() == 3
        assert len(buffer) == 3

    def test_scarta_i_piu_vecchi_a_capienza_piena(self) -> None:
        buffer: RingBuffer[int] = RingBuffer(3)
        buffer.extend([1, 2, 3, 4, 5])
        assert buffer.snapshot() == [3, 4, 5]

    def test_conta_gli_scarti(self) -> None:
        """Uno scarto silenzioso nasconde un problema di throughput."""
        buffer: RingBuffer[int] = RingBuffer(3)
        buffer.extend(range(10))
        assert buffer.dropped == 7
        assert buffer.received == 10

    def test_latest(self) -> None:
        buffer: RingBuffer[int] = RingBuffer(10)
        buffer.extend(range(5))
        assert buffer.latest(2) == [3, 4]
        assert buffer.latest(99) == [0, 1, 2, 3, 4]
        assert buffer.latest(0) == []

    def test_drain_consuma(self) -> None:
        buffer: RingBuffer[int] = RingBuffer(10)
        buffer.extend(range(5))
        assert buffer.drain(2) == [0, 1]
        assert buffer.snapshot() == [2, 3, 4]
        assert buffer.drain() == [2, 3, 4]
        assert len(buffer) == 0

    def test_buffer_vuoto(self) -> None:
        buffer: RingBuffer[int] = RingBuffer(3)
        assert buffer.last() is None
        assert buffer.drain() == []

    def test_capienza_non_positiva_rifiutata(self) -> None:
        with pytest.raises(ValueError):
            RingBuffer(0)

    def test_scrittura_concorrente_non_perde_eventi(self) -> None:
        """Produttore e consumatore girano su thread diversi."""
        import threading

        buffer: RingBuffer[int] = RingBuffer(100_000)

        def produce(base: int) -> None:
            for i in range(1_000):
                buffer.append(base + i)

        threads = [threading.Thread(target=produce, args=(base,)) for base in (0, 10_000, 20_000)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(buffer) == 3_000
        assert len(set(buffer.snapshot())) == 3_000


def depth(first: int, final: int, bids: tuple[tuple[str, str], ...] = (),
          asks: tuple[tuple[str, str], ...] = (), ts: TimestampMs = NOW) -> DepthUpdate:
    return DepthUpdate(
        symbol="BTCUSDT",
        first_update_id=first,
        final_update_id=final,
        bids=tuple((D(p), D(q)) for p, q in bids),
        asks=tuple((D(p), D(q)) for p, q in asks),
        event_time_ms=ts,
    )


class TestOrderBookState:
    def _synced_book(self) -> OrderBookState:
        book = OrderBookState("BTCUSDT")
        book.apply_snapshot(
            last_update_id=100,
            bids=((D("100"), D("1")), (D("99"), D("2"))),
            asks=((D("101"), D("1")), (D("102"), D("3"))),
            ts_ms=NOW,
        )
        return book

    def test_senza_snapshot_non_e_allineato(self) -> None:
        """Finche' il book non e' allineato non si decide niente."""
        book = OrderBookState("BTCUSDT")
        assert not book.is_synced
        assert book.snapshot() is None
        assert book.apply(depth(1, 2)) is ApplyResult.BUFFERED

    def test_snapshot_rende_leggibile_il_book(self) -> None:
        book = self._synced_book()
        snapshot = book.snapshot()
        assert snapshot is not None
        assert snapshot.best_bid is not None and snapshot.best_bid.price == D("100")
        assert snapshot.best_ask is not None and snapshot.best_ask.price == D("101")
        assert snapshot.mid_price == D("100.5")

    def test_eventi_anteriori_allo_snapshot_scartati(self) -> None:
        book = self._synced_book()
        assert book.apply(depth(50, 90)) is ApplyResult.DROPPED_OLD

    def test_evento_a_cavallo_dello_snapshot_applicato(self) -> None:
        """Il primo evento valido copre lastUpdateId + 1."""
        book = self._synced_book()
        assert book.apply(depth(95, 105, bids=(("100", "5"),))) is ApplyResult.APPLIED
        snapshot = book.snapshot()
        assert snapshot is not None
        assert snapshot.best_bid is not None and snapshot.best_bid.quantity == D("5")

    def test_sequenza_contigua_applicata(self) -> None:
        book = self._synced_book()
        assert book.apply(depth(101, 110, bids=(("100", "4"),))) is ApplyResult.APPLIED
        assert book.apply(depth(111, 120, bids=(("100", "6"),))) is ApplyResult.APPLIED
        assert book.last_update_id == 120

    def test_buco_di_sequenza_invalida_il_book(self) -> None:
        """Un evento perso rende il book inattendibile: serve un nuovo snapshot."""
        book = self._synced_book()
        book.apply(depth(101, 110))
        result = book.apply(depth(130, 140))  # manca 111..129
        assert result is ApplyResult.GAP
        assert not book.is_synced
        assert book.snapshot() is None
        assert book.gaps_detected == 1

    def test_dopo_il_buco_il_nuovo_snapshot_recupera(self) -> None:
        book = self._synced_book()
        book.apply(depth(101, 110))
        book.apply(depth(130, 140, bids=(("100", "9"),)))  # buco, finisce in attesa
        applied = book.apply_snapshot(
            last_update_id=129,
            bids=((D("100"), D("1")),),
            asks=((D("101"), D("1")),),
            ts_ms=NOW + 1,
        )
        assert book.is_synced
        assert applied == 1  # l'evento in attesa viene riapplicato
        snapshot = book.snapshot()
        assert snapshot is not None
        assert snapshot.best_bid is not None and snapshot.best_bid.quantity == D("9")

    def test_quantita_zero_cancella_il_livello(self) -> None:
        book = self._synced_book()
        book.apply(depth(101, 110, bids=(("100", "0"),)))
        snapshot = book.snapshot()
        assert snapshot is not None
        assert snapshot.best_bid is not None and snapshot.best_bid.price == D("99")

    def test_invalidate_dopo_la_riconnessione(self) -> None:
        """Gli eventi persi mentre la connessione era giu' non si recuperano."""
        book = self._synced_book()
        book.invalidate("riconnessione")
        assert not book.is_synced
        assert book.snapshot() is None

    def test_profondita_limitata(self) -> None:
        book = OrderBookState("BTCUSDT", max_depth=2)
        book.apply_snapshot(
            last_update_id=1,
            bids=tuple((D(100 - i), D("1")) for i in range(10)),
            asks=tuple((D(101 + i), D("1")) for i in range(10)),
            ts_ms=NOW,
        )
        snapshot = book.snapshot()
        assert snapshot is not None
        assert len(snapshot.bids) == 2
        assert snapshot.bids[0].price > snapshot.bids[1].price

    def test_evento_di_un_altro_simbolo_rifiutato(self) -> None:
        book = self._synced_book()
        other = DepthUpdate("ETHUSDT", 101, 110, (), (), NOW)
        with pytest.raises(ValueError, match="ETHUSDT"):
            book.apply(other)


class TestParsing:
    def test_trade(self) -> None:
        payload = json.dumps(
            {
                "stream": "btcusdt@trade",
                "data": {
                    "e": "trade", "s": "BTCUSDT", "t": 12345, "p": "60000.12",
                    "q": "0.001", "T": NOW, "m": True,
                },
            }
        )
        event = parse_stream_message(payload)
        assert isinstance(event, Trade)
        assert event.price == D("60000.12")
        assert event.is_buyer_maker

    def test_i_prezzi_restano_decimal(self) -> None:
        """Un prezzo passato per float perde cifre che l'exchange conta."""
        payload = json.dumps(
            {"data": {"e": "trade", "s": "BTCUSDT", "t": 1, "p": "60123.456789012345",
                      "q": "1", "T": NOW, "m": False}}
        )
        event = parse_stream_message(payload)
        assert isinstance(event, Trade)
        assert event.price == D("60123.456789012345")
        # lo stesso prezzo passato per float perde le ultime cifre
        assert event.price != D(str(float("60123.456789012345")))

    def test_candela(self) -> None:
        payload = json.dumps(
            {
                "data": {
                    "e": "kline", "s": "BTCUSDT",
                    "k": {
                        "t": NOW, "T": NOW + 59_999, "o": "1", "h": "2", "l": "0.5",
                        "c": "1.5", "v": "10", "q": "15", "n": 7, "x": True,
                    },
                }
            }
        )
        event = parse_stream_message(payload)
        assert isinstance(event, Candle)
        assert event.is_closed
        assert event.close == D("1.5")

    def test_depth(self) -> None:
        payload = json.dumps(
            {
                "data": {
                    "e": "depthUpdate", "s": "BTCUSDT", "U": 10, "u": 20, "E": NOW,
                    "b": [["100", "1"]], "a": [["101", "2"]],
                }
            }
        )
        event = parse_stream_message(payload)
        assert isinstance(event, DepthUpdate)
        assert event.first_update_id == 10
        assert event.bids == ((D("100"), D("1")),)

    def test_messaggio_di_controllo_ignorato(self) -> None:
        """Le risposte ai comandi di sottoscrizione non sono un errore."""
        assert parse_stream_message(json.dumps({"result": None, "id": 1})) is None


class RecordingHandler:
    """Handler di prova che registra tutto quello che riceve."""

    def __init__(self) -> None:
        self.trades: list[Trade] = []
        self.candles: list[Candle] = []
        self.depths: list[DepthUpdate] = []
        self.disconnects: list[tuple[TimestampMs, str]] = []

    def on_trade(self, trade: Trade) -> None:
        self.trades.append(trade)

    def on_candle(self, candle: Candle) -> None:
        self.candles.append(candle)

    def on_depth(self, update: DepthUpdate) -> None:
        self.depths.append(update)

    def on_disconnect(self, ts_ms: TimestampMs, error: str) -> None:
        self.disconnects.append((ts_ms, error))


class FakeConnection:
    """Connessione finta: restituisce messaggi, poi puo' esplodere."""

    def __init__(self, messages: list[str], *, fail_with: Exception | None = None) -> None:
        self._messages = messages
        self._fail_with = fail_with
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[str]:
        for message in self._messages:
            yield message
        if self._fail_with is not None:
            raise self._fail_with

    async def close(self) -> None:
        self.closed = True


async def run_with_clock(ingestor: MarketDataIngestor, clock: SimulatedClock) -> None:
    """Esegue l'ingestor facendo avanzare il tempo simulato sulle sue attese.

    Il tempo salta direttamente alla prossima scadenza invece di scorrere: il
    test resta deterministico e non attende secondi reali per i backoff.
    """
    import asyncio

    task = asyncio.create_task(ingestor.run())
    for _ in range(200):
        await asyncio.sleep(0)
        if task.done():
            break
        next_wakeup = clock.next_wakeup_ms()
        if next_wakeup is not None:
            clock.advance_to(next_wakeup)
    await asyncio.wait_for(task, timeout=2)


class TestIngestor:
    @pytest.fixture
    def config(self) -> AppConfig:
        return load_config(Path("config/config.yaml"))

    def test_nomi_dei_flussi(self, config: AppConfig) -> None:
        ingestor = MarketDataIngestor(
            config, SimulatedClock(NOW), RecordingHandler(), max_cycles=0
        )
        names = ingestor.stream_names()
        assert "btcusdt@trade" in names
        assert "btcusdt@kline_1m" in names
        assert "btcusdt@depth@100ms" in names
        assert all("bnbusdt" not in n for n in names)  # simbolo disabilitato

    def test_url_usa_lendpoint_testnet(self, config: AppConfig) -> None:
        ingestor = MarketDataIngestor(
            config, SimulatedClock(NOW), RecordingHandler(), max_cycles=0
        )
        assert "testnet" in ingestor.stream_url()

    @pytest.mark.asyncio
    async def test_consegna_i_messaggi_allhandler(self, config: AppConfig) -> None:
        handler = RecordingHandler()
        messages = [
            json.dumps({"data": {"e": "trade", "s": "BTCUSDT", "t": 1, "p": "60000",
                                 "q": "0.1", "T": NOW, "m": False}}),
            json.dumps({"data": {"e": "depthUpdate", "s": "BTCUSDT", "U": 1, "u": 2,
                                 "E": NOW, "b": [], "a": []}}),
        ]

        async def factory(url: str) -> FakeConnection:
            return FakeConnection(messages)

        ingestor = MarketDataIngestor(
            config, SimulatedClock(NOW), handler, connection_factory=factory, max_cycles=1
        )
        await ingestor.run()
        assert len(handler.trades) == 1
        assert len(handler.depths) == 1

    @pytest.mark.asyncio
    async def test_si_riconnette_dopo_una_caduta(self, config: AppConfig) -> None:
        """Una disconnessione e' un evento atteso, non un'eccezione."""
        handler = RecordingHandler()
        attempts = {"n": 0}
        message = json.dumps(
            {"data": {"e": "trade", "s": "BTCUSDT", "t": 1, "p": "60000",
                      "q": "0.1", "T": NOW, "m": False}}
        )

        async def factory(url: str) -> FakeConnection:
            attempts["n"] += 1
            return FakeConnection([message], fail_with=ConnectionResetError("caduta"))

        clock = SimulatedClock(NOW)
        ingestor = MarketDataIngestor(
            config, clock, handler, connection_factory=factory, max_cycles=3
        )

        await run_with_clock(ingestor, clock)

        assert attempts["n"] == 3
        assert ingestor.reconnects == 3
        assert len(handler.disconnects) == 3
        assert len(handler.trades) == 3

    @pytest.mark.asyncio
    async def test_errore_di_connessione_non_propaga(self, config: AppConfig) -> None:
        async def factory(url: str) -> FakeConnection:
            raise OSError("endpoint irraggiungibile")

        clock = SimulatedClock(NOW)
        ingestor = MarketDataIngestor(
            config, clock, RecordingHandler(), connection_factory=factory, max_cycles=2
        )
        await run_with_clock(ingestor, clock)
        assert ingestor.reconnects == 2

    @pytest.mark.asyncio
    async def test_messaggio_malformato_non_ferma_il_flusso(self, config: AppConfig) -> None:
        handler = RecordingHandler()
        good = json.dumps({"data": {"e": "trade", "s": "BTCUSDT", "t": 1, "p": "60000",
                                    "q": "0.1", "T": NOW, "m": False}})

        async def factory(url: str) -> FakeConnection:
            return FakeConnection(["{non json", json.dumps({"data": {"e": "trade"}}), good])

        ingestor = MarketDataIngestor(
            config, SimulatedClock(NOW), handler, connection_factory=factory, max_cycles=1
        )
        await ingestor.run()
        assert ingestor.parse_errors == 2
        assert len(handler.trades) == 1


class TestParquetStore:
    def _candle(self, ts: TimestampMs, symbol: str = "BTCUSDT", close: str = "100") -> Candle:
        return Candle(
            symbol=symbol,
            open_time_ms=ts,
            close_time_ms=ts + 59_999,
            open=D("100"),
            high=D("101"),
            low=D("99"),
            close=D(close),
            volume=D("1"),
            quote_volume=D("100"),
            trades=5,
            is_closed=True,
        )

    def test_partizione_giornaliera_in_utc(self) -> None:
        assert day_partition(1_700_000_000_000) == "2023-11-14"

    def test_scrive_e_rilegge(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path, batch_size=1_000)
        store.add_candles([self._candle(NOW + i * 60_000) for i in range(10)])
        assert store.flush() == 1
        candles = store.read_candles(symbols=["BTCUSDT"])
        assert len(candles) == 10
        assert candles[0].close == D("100")

    def test_i_decimali_sopravvivono_al_roundtrip(self, tmp_path: Path) -> None:
        """Un prezzo che passa per float torna indietro diverso."""
        store = ParquetStore(tmp_path)
        store.add_candle(self._candle(NOW, close="60123.456789012345"))
        store.flush()
        candles = store.read_candles()
        assert candles[0].close == D("60123.456789012345")

    def test_le_candele_aperte_non_vengono_salvate(self, tmp_path: Path) -> None:
        """Riletta dal backtest, una candela in formazione sarebbe look-ahead."""
        store = ParquetStore(tmp_path)
        open_candle = Candle(
            symbol="BTCUSDT", open_time_ms=NOW, close_time_ms=NOW + 59_999,
            open=D("1"), high=D("1"), low=D("1"), close=D("1"),
            volume=D("1"), quote_volume=D("1"), trades=1, is_closed=False,
        )
        store.add_candle(open_candle)
        assert store.flush() == 0

    def test_partizionamento_per_giorno_e_simbolo(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        store.add_candle(self._candle(NOW))
        store.add_candle(self._candle(NOW + 86_400_000))
        store.add_candle(self._candle(NOW, symbol="ETHUSDT"))
        store.flush()
        partitions = store.partitions("candles")
        assert len(partitions) == 3
        days = {p.parent.parent.name for p in partitions}
        assert len(days) == 2

    def test_filtro_per_simbolo(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        store.add_candle(self._candle(NOW))
        store.add_candle(self._candle(NOW, symbol="ETHUSDT", close="3000"))
        store.flush()
        candles = store.read_candles(symbols=["ETHUSDT"])
        assert len(candles) == 1
        assert candles[0].symbol == "ETHUSDT"

    def test_filtro_per_finestra_temporale(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        for i in range(10):
            store.add_candle(self._candle(NOW + i * 60_000))
        store.flush()
        candles = store.read_candles(start_ms=NOW + 5 * 60_000, end_ms=NOW + 8 * 60_000)
        assert len(candles) == 3

    def test_scrittura_automatica_a_lotto_pieno(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path, batch_size=5)
        for i in range(12):
            store.add_candle(self._candle(NOW + i * 60_000))
        assert store.files_written == 2  # due lotti pieni, due candele ancora in memoria
        store.flush()
        assert store.files_written == 3
        assert len(store.read_candles()) == 12

    def test_lettura_di_un_dataset_inesistente(self, tmp_path: Path) -> None:
        assert ParquetStore(tmp_path).read_candles() == []

    def test_retention_cancella_le_partizioni_vecchie(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        store.add_candle(self._candle(NOW))
        store.add_candle(self._candle(NOW + 5 * 86_400_000))
        store.flush()
        removed = store.purge_older_than(NOW + 3 * 86_400_000)
        assert removed == 1
        assert len(store.read_candles()) == 1

    def test_i_trade_si_salvano_separatamente(self, tmp_path: Path) -> None:
        store = ParquetStore(tmp_path)
        store.add_trades(
            [Trade("BTCUSDT", i, D("60000"), D("0.001"), NOW + i, i % 2 == 0) for i in range(5)]
        )
        store.flush()
        frame = store.read("trades")
        assert len(frame) == 5


class TestSimboliOstili:
    """Il simbolo arriva dal flusso di rete e finisce in due posti pericolosi:
    nei percorsi dei file salvati e, attraverso i messaggi di rifiuto, nella
    pagina del pannello. Va trattato come testo ostile, non come un nome."""

    def test_un_simbolo_con_html_viene_rifiutato(self) -> None:
        payload = json.dumps(
            {"data": {"e": "trade", "s": "<img src=x onerror=alert(1)>", "t": 1,
                      "p": "100", "q": "1", "T": NOW, "m": False}}
        )
        with pytest.raises(ValueError, match="simbolo"):
            parse_stream_message(payload)

    def test_un_simbolo_con_percorso_viene_rifiutato(self) -> None:
        payload = json.dumps(
            {"data": {"e": "trade", "s": "X/../../../../tmp/evil", "t": 1,
                      "p": "100", "q": "1", "T": NOW, "m": False}}
        )
        with pytest.raises(ValueError, match="simbolo"):
            parse_stream_message(payload)

    def test_un_simbolo_normale_passa(self) -> None:
        payload = json.dumps(
            {"data": {"e": "trade", "s": "BTCUSDT", "t": 1, "p": "100",
                      "q": "1", "T": NOW, "m": False}}
        )
        event = parse_stream_message(payload)
        assert isinstance(event, Trade)

    @pytest.mark.asyncio
    async def test_un_simbolo_non_configurato_viene_scartato(self) -> None:
        """Anche un simbolo dalla forma lecita, se non e' fra i nostri, non
        deve entrare: non lo abbiamo chiesto noi."""
        config = load_config(Path("config/config.yaml"))
        handler = RecordingHandler()
        messaggio = json.dumps(
            {"data": {"e": "trade", "s": "DOGEUSDT", "t": 1, "p": "1",
                      "q": "1", "T": NOW, "m": False}}
        )

        async def factory(url: str) -> FakeConnection:
            return FakeConnection([messaggio])

        ingestor = MarketDataIngestor(
            config, SimulatedClock(NOW), handler, connection_factory=factory, max_cycles=1
        )
        await ingestor.run()
        assert handler.trades == []
        assert ingestor.ignored_symbols == 1

    def test_lo_store_rifiuta_un_simbolo_con_percorso(self, tmp_path: Path) -> None:
        """Seconda barriera: anche se un simbolo ostile arrivasse da altrove,
        non deve poter scrivere fuori dalla cartella dei dati."""
        store = ParquetStore(tmp_path / "dati")
        veleno = Trade("X/../../../fuori", 1, D("100"), D("1"), NOW, False)
        with pytest.raises(ValueError, match="simbolo"):
            store.add_trade(veleno)
