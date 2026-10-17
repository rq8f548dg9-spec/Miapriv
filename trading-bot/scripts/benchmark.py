"""Misura la capacita' del bot: quanti eventi al secondo regge e quanto consuma.

    python scripts/benchmark.py

Serve a rispondere a una domanda concreta: questo bot sta dietro al mercato?
Un bot che impiega piu' di un secondo a decidere su un flusso che produce
cento eventi al secondo non e' lento, e' inutile: decide sul passato.

Le misure sono sul percorso vero (stessi componenti del live), non su
microbenchmark di funzioni isolate.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass
from decimal import Decimal

from backtest.engine import BacktestEngine, default_filters
from config.schema import AppConfig, load_config
from core.clock import SimulatedClock
from core.portfolio import Portfolio
from core.types import (
    BookLevel,
    Candle,
    OrderBookSnapshot,
    Side,
    SignalDirection,
    TimestampMs,
    Trade,
)
from features.engine import FeatureEngine, FeatureVector
from risk.manager import MarketContext, RiskManager
from signals.base import GeneratedSignal, SignalGenerator, TradePlan
from signals.rules import RuleBasedSignalGenerator

MINUTE = 60_000
START = 1_700_000_000_000


@dataclass(frozen=True, slots=True)
class Misura:
    nome: str
    operazioni: int
    secondi: float
    unita: str

    @property
    def al_secondo(self) -> float:
        return self.operazioni / self.secondi if self.secondi > 0 else float("inf")

    @property
    def microsecondi_per_operazione(self) -> float:
        return self.secondi / self.operazioni * 1_000_000 if self.operazioni else 0.0

    def riga(self) -> str:
        return (
            f"  {self.nome:<42} {self.al_secondo:>12,.0f} {self.unita}/s"
            f"   ({self.microsecondi_per_operazione:>7.1f} us ciascuna)"
        )


if sys.platform == "win32":

    def picco_memoria_mb() -> float | None:
        """Windows non espone ``getrusage``: il picco non si misura.

        Dire "non misurabile" e' l'unica risposta onesta. Importare
        ``resource`` a prescindere fermava il programma all'avvio, prima
        ancora di stampare l'aiuto.
        """
        return None

else:
    import resource

    def picco_memoria_mb() -> float | None:
        """Picco di memoria del processo, in MB."""
        return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024


def candele(count: int, symbol: str = "BTCUSDT", seed: int = 7) -> list[Candle]:
    rng = random.Random(seed)  # noqa: S311 - serie sintetica, non crittografia
    price = Decimal("60000")
    out: list[Candle] = []
    for i in range(count):
        price = (price * (Decimal(1) + Decimal(str(round(rng.gauss(0, 0.0012), 6))))).quantize(
            Decimal("0.01")
        )
        out.append(
            Candle(
                symbol=symbol,
                open_time_ms=START + i * MINUTE,
                close_time_ms=START + (i + 1) * MINUTE - 1,
                open=price,
                high=price * Decimal("1.001"),
                low=price * Decimal("0.999"),
                close=price,
                volume=Decimal(10),
                quote_volume=price * 10,
                trades=30,
                is_closed=True,
            )
        )
    return out


class GeneratoreDiProva(SignalGenerator):
    """Genera sempre un segnale: misura il costo del percorso completo."""

    name = "benchmark"

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        if not self.warmup_satisfied(vector):
            return None
        return GeneratedSignal(
            signal=self._build_signal(
                symbol=vector.symbol,
                now_ms=now_ms,
                direction=SignalDirection.LONG,
                probability=0.72,
                features=vector.values,
                generator=self.name,
                horizon_ms=300_000,
            ),
            plan=TradePlan(stop_bps=40.0, take_profit_bps=80.0),
        )


def misura_feature_engine(config: AppConfig) -> list[Misura]:
    engine = FeatureEngine(config.features)
    barre = candele(5_000)

    inizio = time.perf_counter()
    for candle in barre:
        engine.on_candle(candle)
    durata = time.perf_counter() - inizio
    risultati = [Misura("Candele nel feature engine", len(barre), durata, "candele")]

    trades = [
        Trade("BTCUSDT", i, Decimal("60000"), Decimal("0.01"), START + i * 10, i % 2 == 0)
        for i in range(50_000)
    ]
    inizio = time.perf_counter()
    for trade in trades:
        engine.on_trade(trade)
    risultati.append(
        Misura("Trade nell'order flow", len(trades), time.perf_counter() - inizio, "trade")
    )

    book = OrderBookSnapshot(
        symbol="BTCUSDT",
        last_update_id=1,
        bids=tuple(BookLevel(Decimal(60000 - i), Decimal("1")) for i in range(20)),
        asks=tuple(BookLevel(Decimal(60001 + i), Decimal("1")) for i in range(20)),
        ts_ms=START,
    )
    # Il costo di un vettore dipende da quanti trade ci sono nella finestra
    # dell'order flow: su un simbolo attivo sono migliaia, e si rileggono a
    # ogni calcolo. Si misurano entrambi i casi, perche' un numero solo
    # darebbe un'idea sbagliata in un verso o nell'altro.
    inizio = time.perf_counter()
    for i in range(5_000):
        engine.on_book("BTCUSDT", book)
        engine.compute("BTCUSDT", START + i)
    risultati.append(
        Misura(
            "Vettori con order flow denso (~3k trade)",
            5_000,
            time.perf_counter() - inizio,
            "vettori",
        )
    )

    calmo = FeatureEngine(config.features)
    for candle in barre:
        calmo.on_candle(candle)
    inizio = time.perf_counter()
    for i in range(20_000):
        calmo.on_book("BTCUSDT", book)
        calmo.compute("BTCUSDT", START + i)
    risultati.append(
        Misura(
            "Vettori con mercato calmo", 20_000, time.perf_counter() - inizio, "vettori"
        )
    )
    return risultati


def misura_decisione(config: AppConfig) -> Misura:
    """Il ciclo vero: feature + segnale + tutti i controlli di rischio."""
    clock = SimulatedClock(START)
    engine = FeatureEngine(config.features)
    for candle in candele(400):
        engine.on_candle(candle)

    # Order flow realistico in finestra: senza, il ciclo sembrerebbe quattro
    # volte piu' veloce di quanto sia su un simbolo vero.
    for i in range(2_000):
        engine.on_trade(
            Trade("BTCUSDT", i, Decimal("60000"), Decimal("0.01"), START + i * 15, i % 2 == 0)
        )

    generatore = GeneratoreDiProva()
    def order_id(
        *, symbol: str, side: Side, quantity: Decimal, intent: str, ts_ms: TimestampMs
    ) -> str:
        return f"bench-{symbol}-{side.value}-{intent}-{quantity}-{ts_ms}"

    risk = RiskManager.from_config(config, clock, order_id)
    portfolio = Portfolio(Decimal("200"))
    portfolio.update_mark("BTCUSDT", Decimal("60000"))
    filtri = default_filters("BTCUSDT", fetched_at_ms=START)

    iterazioni = 5_000
    inizio = time.perf_counter()
    for i in range(iterazioni):
        clock.advance_by(1)
        # un trade nuovo a ogni giro, come dal vivo
        engine.on_trade(
            Trade("BTCUSDT", 10_000 + i, Decimal("60000"), Decimal("0.01"),
                  clock.now_ms(), i % 2 == 0)
        )
        vector = engine.compute("BTCUSDT", clock.now_ms())
        proposta = generatore.generate(vector, has_position=False, now_ms=clock.now_ms())
        if proposta is None:
            continue
        risk.evaluate(
            proposta.signal,
            portfolio.snapshot(clock.now_ms()),
            MarketContext(
                price=Decimal("60000"),
                filters=filtri,
                payoff_ratio=proposta.plan.payoff_ratio,
                expected_move_bps=proposta.plan.expected_move_bps,
                estimated_cost_bps=22.0,
            ),
        )
        # Le decisioni si accumulano: si tiene corta la lista come in esercizio.
        if len(risk.decisions) > 1_000:
            del risk.decisions[:-500]
    return Misura(
        "Ciclo completo (feature + segnale + rischio)",
        iterazioni,
        time.perf_counter() - inizio,
        "decisioni",
    )


def misura_backtest(config: AppConfig) -> Misura:
    barre = candele(20_000)
    motore = BacktestEngine(config, RuleBasedSignalGenerator())
    inizio = time.perf_counter()
    motore.run(barre)
    return Misura("Backtest completo", len(barre), time.perf_counter() - inizio, "barre")


def main() -> int:
    config = load_config("config/config.yaml")
    print("\n  CAPACITA' DEL BOT — misure sul percorso reale\n")
    print(f"  Configurazione: {len(config.enabled_symbols)} simboli, "
          f"candele da {config.data.candle_interval}, "
          f"warmup {config.features.warmup_bars} barre\n")

    misure: list[Misura] = []
    misure.extend(misura_feature_engine(config))
    misure.append(misura_decisione(config))
    misure.append(misura_backtest(config))

    print("  THROUGHPUT")
    for misura in misure:
        print(misura.riga())

    memoria_mb = picco_memoria_mb()
    if memoria_mb is None:
        print("\n  MEMORIA  picco non misurabile su questo sistema")
    else:
        print(f"\n  MEMORIA  picco del processo: {memoria_mb:,.0f} MB")

    decisioni = next(m for m in misure if m.nome.startswith("Ciclo completo"))
    backtest = next(m for m in misure if m.nome.startswith("Backtest"))
    giorni_al_secondo = backtest.al_secondo / 1_440
    print(
        f"\n  IN PRATICA\n"
        f"    - regge circa {decisioni.al_secondo:,.0f} decisioni al secondo:\n"
        f"      il flusso di Binance su pochi simboli ne produce 10-100 al secondo.\n"
        f"    - simula {giorni_al_secondo:,.1f} giorni di mercato al secondo:\n"
        f"      un anno di storico su un simbolo in circa "
        f"{365 / giorni_al_secondo:,.0f} secondi.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
