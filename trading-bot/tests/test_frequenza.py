"""Esploratore della frequenza: quante operazioni, e a che prezzo.

"Voglio che operi in continuazione" e' una richiesta legittima, e la
risposta non e' ne' "si'" ne' "no": e' un numero. Ogni giro completo
costa circa 22 punti base fra commissioni e spread, quindi cento
operazioni al giorno costano il 22% del controvalore scambiato ogni
giorno. Questo modulo misura, sui dati veri dell'utente, quante
operazioni produce ogni impostazione e che risultato danno, cosi' la
scelta e' informata invece che di pancia.

Quello che NON fa: non abbassa le protezioni per forzare operazioni. Il
risk manager rifiuta le operazioni a valore atteso negativo, e quella
regola resta.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest

from backtest.frequenza import PROFILI, ProfiloFrequenza, esplora
from config.schema import AppConfig, load_config
from core.types import Candle, Trade
from tests.test_no_lookahead import AlwaysTradingGenerator, synthetic_candles

D = Decimal


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


def flusso_per(candele: list[Candle], per_barra: int = 3) -> list[Trade]:
    trade: list[Trade] = []
    numero = 0
    for candela in candele:
        durata = candela.close_time_ms - candela.open_time_ms
        for k in range(per_barra):
            numero += 1
            trade.append(
                Trade(
                    symbol=candela.symbol,
                    trade_id=numero,
                    price=candela.close,
                    quantity=D("0.4"),
                    ts_ms=candela.open_time_ms + (k + 1) * durata // (per_barra + 1),
                    is_buyer_maker=numero % 3 == 0,
                )
            )
    return trade


class TestProfili:
    def test_i_profili_vanno_dal_prudente_allaggressivo(self) -> None:
        nomi = [p.nome for p in PROFILI]
        assert nomi[0] == "prudente"
        assert nomi[-1] == "massima frequenza"
        # Ordinati per severita' decrescente: ogni profilo chiede meno del
        # precedente, altrimenti la tabella non si legge come una scala.
        for prima, dopo in pairwise(PROFILI):
            assert dopo.min_probability <= prima.min_probability
            assert dopo.min_expected_edge_bps <= prima.min_expected_edge_bps

    def test_nessun_profilo_accetta_valore_atteso_negativo(self) -> None:
        """E' il limite che non si tocca: sotto lo zero il bot starebbe
        pagando per operare. Nessuna impostazione del pannello deve poter
        comprare frequenza a quel prezzo."""
        assert all(p.min_expected_edge_bps >= 0.0 for p in PROFILI)


class TestEsplorazione:
    def test_restituisce_una_riga_per_profilo(
        self, config: AppConfig, request: pytest.FixtureRequest
    ) -> None:
        candele = synthetic_candles(300)
        righe = esplora(
            config,
            candele,
            flusso_per(candele),
            profili=PROFILI[:2],
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        assert [r.profilo.nome for r in righe] == [p.nome for p in PROFILI[:2]]
        for riga in righe:
            assert riga.giorni > 0
            assert riga.operazioni_al_giorno >= 0

    def test_allentare_le_soglie_non_riduce_le_operazioni(
        self, config: AppConfig
    ) -> None:
        """La scala deve essere monotona, altrimenti la tabella che
        l'utente legge per scegliere non vuol dire niente."""
        candele = synthetic_candles(400)
        righe = esplora(
            config,
            candele,
            flusso_per(candele),
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        ordini = [r.ordini for r in righe]
        assert ordini == sorted(ordini), f"scala non monotona: {ordini}"

    def test_dichiara_il_vincolo_che_ferma_le_operazioni(
        self, config: AppConfig
    ) -> None:
        """Sapere QUANTE operazioni non bastano: serve sapere cosa le
        ferma, perche' e' li' che si interviene."""
        candele = synthetic_candles(300)
        righe = esplora(
            config,
            candele,
            flusso_per(candele),
            profili=[PROFILI[0]],
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        assert righe[0].vincolo_principale, "il motivo di rifiuto piu' frequente"

    def test_il_costo_annunciato_e_quello_vero(self, config: AppConfig) -> None:
        """Il numero che serve davvero: quanto costa, in commissioni, la
        frequenza che si sta chiedendo."""
        candele = synthetic_candles(300)
        righe = esplora(
            config,
            candele,
            flusso_per(candele),
            profili=[PROFILI[-1]],
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        riga = righe[0]
        assert riga.costo_giro_bps > 20.0
        atteso = riga.operazioni_al_giorno * riga.costo_giro_bps / 100.0
        assert riga.costo_giornaliero_pct == pytest.approx(atteso, rel=1e-6)

    def test_un_profilo_inventato_si_puo_passare(self, config: AppConfig) -> None:
        candele = synthetic_candles(200)
        mio = ProfiloFrequenza(
            nome="mio",
            descrizione="prova",
            min_probability=0.51,
            min_expected_edge_bps=1.0,
            cooldown_s=0,
        )
        righe = esplora(
            config,
            candele,
            [],
            profili=[mio],
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        assert righe[0].profilo.nome == "mio"


class TestAggregazione:
    """Su candele da un minuto il movimento atteso e' piccolo rispetto ai
    costi: 6 punti base contro 22. Su barre piu' lunghe il movimento
    cresce, i costi no. E' la leva che decide se la strategia puo'
    operare, e va misurata, non supposta."""

    def test_quattro_barre_diventano_una(self) -> None:
        from backtest.frequenza import aggrega

        candele = synthetic_candles(8)
        unite = aggrega(candele, 4)

        assert len(unite) == 2
        prima = unite[0]
        assert prima.open == candele[0].open
        assert prima.close == candele[3].close
        assert prima.high == max(c.high for c in candele[:4])
        assert prima.low == min(c.low for c in candele[:4])
        assert prima.volume == sum((c.volume for c in candele[:4]), D("0"))
        assert prima.trades == sum(c.trades for c in candele[:4])
        assert prima.open_time_ms == candele[0].open_time_ms
        assert prima.close_time_ms == candele[3].close_time_ms

    def test_la_barra_incompleta_in_fondo_si_scarta(self) -> None:
        """Una barra da quindici minuti costruita con sette minuti di dati
        non e' una barra da quindici minuti."""
        from backtest.frequenza import aggrega

        assert len(aggrega(synthetic_candles(10), 4)) == 2

    def test_i_simboli_restano_separati(self) -> None:
        from backtest.frequenza import aggrega

        btc = synthetic_candles(4, symbol="BTCUSDT")
        eth = synthetic_candles(4, symbol="ETHUSDT", start_price=D("3000"))
        unite = aggrega([*btc, *eth], 4)
        assert sorted(c.symbol for c in unite) == ["BTCUSDT", "ETHUSDT"]

    def test_il_fattore_uno_non_cambia_niente(self) -> None:
        from backtest.frequenza import aggrega

        candele = synthetic_candles(5)
        assert aggrega(candele, 1) == candele


class TestOrizzonti:
    def test_misura_ogni_orizzonte(self, config: AppConfig) -> None:
        from backtest.frequenza import esplora_orizzonti

        # Servono abbastanza barre anche DOPO l'aggregazione: il warmup si
        # conta in barre, quindi su barre da cinque minuti ne servono cinque
        # volte tante di partenza.
        candele = synthetic_candles(1600)
        righe = esplora_orizzonti(
            config,
            candele,
            flusso_per(candele),
            fattori=(1, 5),
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        assert [r.etichetta for r in righe] == ["1 barra", "5 barre"]
        assert righe[0].esito.giorni > 0

    def test_un_orizzonte_senza_barre_a_sufficienza_viene_saltato(
        self, config: AppConfig
    ) -> None:
        """Con poche candele, aggregare per cento lascia una barra sola:
        meglio dirlo che restituire una misura su niente."""
        from backtest.frequenza import esplora_orizzonti

        candele = synthetic_candles(400)
        righe = esplora_orizzonti(
            config,
            candele,
            [],
            fattori=(1, 100),
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        assert [r.etichetta for r in righe] == ["1 barra"]
