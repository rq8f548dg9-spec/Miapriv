"""Prova di resistenza a tempo accelerato, con guasti iniettati.

I difetti che contano in un bot che gira sempre non si vedono in trenta
secondi di test: si vedono quando il flusso cade e torna, quando il
processo muore fra due scritture, quando il computer va in standby e
l'orologio salta avanti di ore. Nessuno di questi e' un caso
straordinario: su una settimana capitano tutti.

Qui si simulano in sequenza, e dopo ognuno si verificano gli stessi
invarianti. L'idea e' semplice: non "il bot funziona", ma "dopo questo
guasto, la contabilita' e' ancora quella giusta?".

**Quello che questa prova non e'.** Il tempo e' simulato, quindi non
scopre perdite di memoria, descrittori che si accumulano, certificati che
scadono, limiti dell'API raggiunti dopo ore vere. Le 72 ore reali
restano da fare, e questo rapporto lo dice in fondo ogni volta.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from backtest.costs import CostModel
from config.schema import AppConfig, CostsConfig
from core.clock import SimulatedClock
from core.ledger import EventLedger
from core.portfolio import Portfolio
from core.types import Candle, SignalDirection, SymbolFilters, TimestampMs, Trade
from data.store import ParquetStore
from execution.broker import PaperBroker
from execution.idempotency import OrderRegistry
from features.engine import FeatureVector
from monitoring.alerts import AlertManager
from monitoring.logger import DecisionLogger
from monitoring.state import StateStore
from runner import TradingBot, build_generator
from signals.base import GeneratedSignal, SignalGenerator, TradePlan

__all__ = ["Esito", "GeneratoreCheOpera", "Guasto", "ProvaDiResistenza"]

logger = logging.getLogger("trading_bot.resistenza")

_MINUTO = 60_000
_INIZIO = 1_700_000_000_000


class Guasto(StrEnum):
    """I modi in cui le cose vanno storte, nella vita vera."""

    DISCONNESSIONE = "disconnessione"
    RIAVVIO = "riavvio"
    SALTO_OROLOGIO = "salto_orologio"
    FILL_RIPETUTO = "fill_ripetuto"
    FLUSSO_FERMO = "flusso_fermo"


@dataclass
class Esito:
    """Com'e' andata, e cosa non e' stato provato."""

    minuti_simulati: int
    cicli: int
    riavvii: int
    guasti_iniettati: int
    guasti_per_tipo: dict[str, int]
    decisioni: int
    esecuzioni: int
    esecuzioni_doppie: int
    errori_non_gestiti: int
    invarianti_violati: list[str]

    @property
    def superata(self) -> bool:
        return not self.invarianti_violati and self.errori_non_gestiti == 0

    def rapporto(self) -> str:
        righe = [
            f"Prova di resistenza: {self.minuti_simulati} minuti simulati "
            f"({self.minuti_simulati / 60:.1f} ore), {self.cicli} cicli.",
            f"Guasti iniettati: {self.guasti_iniettati} "
            f"({', '.join(f'{k} x{v}' for k, v in sorted(self.guasti_per_tipo.items()))}).",
            f"Riavvii completi: {self.riavvii}. Decisioni: {self.decisioni}. "
            f"Esecuzioni: {self.esecuzioni}, di cui doppie contabilizzate: "
            f"{self.esecuzioni_doppie}.",
            f"Errori non gestiti: {self.errori_non_gestiti}.",
        ]
        if self.invarianti_violati:
            righe.append("INVARIANTI VIOLATI:")
            righe.extend(f"  - {v}" for v in self.invarianti_violati)
        else:
            righe.append("Nessun invariante violato.")
        righe.append(
            "Limite di questa prova: il tempo e' simulato. Non dice niente su "
            "perdite di memoria, descrittori accumulati, certificati scaduti o "
            "limiti dell'API raggiunti dopo ore vere. Le 72 ore reali restano "
            "da fare, e non sono sostituibili con un'accelerazione."
        )
        return "\n".join(righe)


def _filtri(symbol: str, ts_ms: TimestampMs) -> SymbolFilters:
    return SymbolFilters(
        symbol=symbol,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.00001"),
        min_quantity=Decimal("0.00001"),
        max_quantity=Decimal("9000"),
        min_notional=Decimal("5"),
        base_precision=5,
        quote_precision=2,
        fetched_at_ms=ts_ms,
    )


@dataclass
class GeneratoreCheOpera(SignalGenerator):
    """Generatore deterministico che apre davvero, per stressare il resto.

    La baseline a regole, su dati sintetici, rifiuta quasi tutto ed e'
    giusto cosi': ma una prova di resistenza in cui non viene mai
    eseguito un ordine non prova la contabilita', la deduplica e il
    recupero dopo un riavvio, cioe' proprio le cose che deve provare.
    Qui serve un generatore che operi, non uno che guadagni.
    """

    horizon_ms: int = 300_000
    name: str = "resistenza"

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        if not self.warmup_satisfied(vector):
            return None
        atr_bps = vector.values.get("atr_bps")
        ultimo = vector.values.get("ret_1")
        if atr_bps is None or atr_bps <= 0 or ultimo is None:
            return None
        if ultimo > 0:
            direzione, probabilita = SignalDirection.LONG, 0.75
        elif has_position:
            direzione, probabilita = SignalDirection.FLAT, 0.60
        else:
            return None
        return GeneratedSignal(
            signal=self._build_signal(
                symbol=vector.symbol,
                now_ms=now_ms,
                direction=direzione,
                probability=probabilita,
                features=vector.values,
                generator=self.name,
                horizon_ms=self.horizon_ms,
            ),
            plan=TradePlan(stop_bps=float(atr_bps), take_profit_bps=float(atr_bps) * 2),
        )


@dataclass
class ProvaDiResistenza:
    """Costruisce un bot di prova e lo maltratta in modo riproducibile."""

    config: AppConfig
    cartella: Path
    seed: int = 20_261_018
    #: Con ``False`` usa il generatore configurato, che su dati sintetici
    #: non opera quasi mai: la prova resta valida ma non esercita la
    #: contabilita' delle esecuzioni.
    forza_operativita: bool = True
    bot: TradingBot | None = field(default=None, init=False)
    _rng: random.Random = field(init=False, repr=False)
    _prezzo: Decimal = field(default=Decimal("60000"), init=False)
    _barra: int = field(default=0, init=False)
    _esecuzioni_viste: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)  # noqa: S311 - prova, non crittografia

    # -- costruzione -------------------------------------------------------

    def _costruisci(self, clock: SimulatedClock | None = None) -> TradingBot:
        """Bot completo sul broker simulato, con lo stesso cablaggio vero.

        Lo stato e il registro vivono nella stessa cartella fra un
        riavvio e l'altro: e' proprio la continuita' che si vuole
        verificare.
        """
        orologio = clock or SimulatedClock(_INIZIO)
        filtri = {
            s.symbol: _filtri(s.symbol, orologio.now_ms())
            for s in self.config.enabled_symbols
        }
        broker = PaperBroker(
            clock=orologio,
            costs=CostModel(
                self.config.backtest.costs if self.config.backtest else CostsConfig()
            ),
            filters=filtri,
            balances={self.config.quote_asset: self.config.capital_quote},
            quote_asset=self.config.quote_asset,
        )
        for symbol in filtri:
            broker.set_price(symbol, self._prezzo)

        bot = TradingBot(
            config=self.config,
            clock=orologio,
            broker=broker,
            generator=(
                GeneratoreCheOpera(horizon_ms=self.config.signals.signal_horizon_ms)
                if self.forza_operativita
                else build_generator(self.config)
            ),
            portfolio=Portfolio(self.config.capital_quote),
            registry=OrderRegistry(self.cartella / "orders.json"),
            alerts=AlertManager(orologio),
            decision_log=DecisionLogger(mode="resistenza"),
            state_store=StateStore(self.cartella / "state.json", orologio, mode="resistenza"),
            # Lo store serve davvero: al riavvio il bot si riscalda da li',
            # ed e' proprio il percorso che un riavvio deve esercitare.
            store=ParquetStore(self.cartella / "dati", batch_size=200),
            ledger=EventLedger(self.cartella / "ledger.jsonl"),
        )
        bot.filters = filtri
        if bot.lease is not None:
            bot.lease.acquire()
        return bot

    # -- mercato simulato --------------------------------------------------

    def _prossima_candela(self, symbol: str, clock: SimulatedClock) -> Candle:
        passo = Decimal(str(round(self._rng.gauss(0, 0.0012), 6)))
        apertura = self._prezzo
        self._prezzo = max(Decimal("1"), (self._prezzo * (1 + passo)).quantize(Decimal("0.01")))
        inizio = _INIZIO + self._barra * _MINUTO
        return Candle(
            symbol=symbol,
            open_time_ms=inizio,
            close_time_ms=inizio + _MINUTO - 1,
            open=apertura,
            high=max(apertura, self._prezzo) * Decimal("1.0008"),
            low=min(apertura, self._prezzo) * Decimal("0.9992"),
            close=self._prezzo,
            volume=Decimal("12"),
            quote_volume=self._prezzo * 12,
            trades=30,
            is_closed=True,
        )

    # -- invarianti --------------------------------------------------------

    def verifica_invarianti(self) -> list[str]:
        """Le poche cose che devono valere sempre, comunque sia andata."""
        bot = self.bot
        if bot is None:
            return ["nessun bot costruito"]
        violazioni: list[str] = []

        if bot.ledger is not None:
            riletto = bot.ledger.replay(self.config.quote_asset).portfolio
            if riletto.cash != bot.portfolio.cash:
                violazioni.append(
                    f"il registro dice contante {riletto.cash}, la memoria "
                    f"{bot.portfolio.cash}: la contabilita' e' divergente"
                )
            for symbol, posizione in bot.portfolio.positions.items():
                atteso = riletto.positions.get(symbol)
                quantita = atteso.quantity if atteso else Decimal(0)
                if quantita != posizione.quantity:
                    violazioni.append(
                        f"il registro dice {quantita} su {symbol}, la memoria "
                        f"{posizione.quantity}"
                    )

        if bot.portfolio.cash < 0:
            violazioni.append(f"contante negativo: {bot.portfolio.cash}")

        for symbol, posizione in bot.portfolio.positions.items():
            if posizione.quantity < 0:
                violazioni.append(
                    f"posizione negativa su {symbol}: lo spot non ammette scoperto"
                )

        if bot.risk.killswitch.is_tripped and bot.risk.killswitch.allows_opening(
            bot.clock.now_ms()
        ):
            violazioni.append(
                "kill switch scattato ma apertura ancora permessa"
            )
        return violazioni

    # -- guasti ------------------------------------------------------------

    async def _inietta(self, guasto: Guasto) -> None:
        bot = self.bot
        assert bot is not None
        clock = bot.clock
        assert isinstance(clock, SimulatedClock)

        if guasto is Guasto.DISCONNESSIONE:
            bot.on_disconnect(clock.now_ms(), "caduta simulata")
        elif guasto is Guasto.SALTO_OROLOGIO:
            # Computer in standby: il tempo salta avanti di ore.
            clock.advance_by(self._rng.randint(1, 3) * 3_600_000)
        elif guasto is Guasto.FLUSSO_FERMO:
            # Nessun dato per un po': il bot deve accorgersene da solo.
            clock.advance_by(self._rng.randint(3, 15) * _MINUTO)
        elif guasto is Guasto.FILL_RIPETUTO:
            self._ripeti_un_fill()
        elif guasto is Guasto.RIAVVIO:
            await self._riavvia()

    def _ripeti_un_fill(self) -> None:
        """Rimanda al bot un'esecuzione gia' contabilizzata.

        Succede davvero: lo stesso fill arriva dalla risposta all'ordine e
        poi dalla riconciliazione. Contarlo due volte raddoppierebbe la
        posizione sui libri senza che nulla sia cambiato sull'exchange.
        """
        bot = self.bot
        assert bot is not None
        if bot.ledger is None:
            return
        esecuzioni = [
            evento for evento in bot.ledger.events() if evento.kind.value == "esecuzione"
        ]
        if not esecuzioni:
            return
        bot.apply_fill(self._rng.choice(esecuzioni).to_fill())

    async def _riavvia(self) -> None:
        """Spegne e riaccende, come farebbe un crash piu' un rilancio."""
        bot = self.bot
        assert bot is not None
        clock = bot.clock
        assert isinstance(clock, SimulatedClock)
        bot.snapshot_state(force=True)
        if bot.store is not None:
            # Quello che non e' stato scritto, al riavvio non c'e': e'
            # esattamente il caso che si vuole provare.
            bot.store.flush()
        if bot.lease is not None:
            bot.lease.release()
        prezzo_corrente = self._prezzo
        self.bot = self._costruisci(SimulatedClock(clock.now_ms()))
        self._prezzo = prezzo_corrente
        await self.bot.startup()

    # -- ciclo principale --------------------------------------------------

    async def esegui(self, *, minuti: int, ogni_quanti_guasti: int = 240) -> Esito:
        """Fa scorrere ``minuti`` di mercato, iniettando guasti lungo la strada."""
        self.bot = self._costruisci()
        await self.bot.startup()

        guasti: dict[str, int] = {}
        riavvii = 0
        errori = 0
        doppie = 0
        decisioni_totali = 0
        violazioni: list[str] = []
        tipi = list(Guasto)
        prossimo_guasto = 0

        for minuto in range(minuti):
            bot = self.bot
            assert bot is not None
            clock = bot.clock
            assert isinstance(clock, SimulatedClock)
            # Le barre seguono l'orologio, non il contatore: dopo uno
            # standby il mercato e' andato avanti da solo, e il bot si
            # ritrova un buco nei dati invece di una serie continua.
            self._barra = max(self._barra + 1, (clock.now_ms() - _INIZIO) // _MINUTO + 1)
            clock.advance_to(max(clock.now_ms(), _INIZIO + self._barra * _MINUTO))

            for symbol_config in self.config.enabled_symbols:
                candela = self._prossima_candela(symbol_config.symbol, clock)
                bot.on_candle(candela)
                bot.on_trade(
                    Trade(
                        symbol=symbol_config.symbol,
                        trade_id=self._barra,
                        price=candela.close,
                        quantity=Decimal("0.5"),
                        ts_ms=candela.close_time_ms,
                        is_buyer_maker=self._barra % 2 == 0,
                    )
                )

            try:
                for symbol_config in self.config.enabled_symbols:
                    await bot.evaluate_symbol(symbol_config.symbol)
                await bot.check_protective_exits()
                await bot.check_killswitch()
                bot.snapshot_state()
            except Exception:
                logger.exception("errore non gestito al minuto %d", minuto)
                errori += 1

            decisioni_totali = len(bot.risk.decisions)

            # Il primo guasto arriva dopo il riscaldamento: prima di
            # allora il bot non decide ancora niente, e maltrattarlo
            # misurerebbe solo quanto regge a stare fermo.
            if minuto >= max(prossimo_guasto, self.config.features.warmup_bars + 10):
                guasto = tipi[self._rng.randrange(len(tipi))]
                prima = self._conta_esecuzioni()
                try:
                    await self._inietta(guasto)
                except Exception:
                    logger.exception("errore non gestito durante %s", guasto.value)
                    errori += 1
                guasti[guasto.value] = guasti.get(guasto.value, 0) + 1
                if guasto is Guasto.RIAVVIO:
                    riavvii += 1
                if guasto is Guasto.FILL_RIPETUTO:
                    doppie += max(0, self._conta_esecuzioni() - prima)
                violazioni.extend(self.verifica_invarianti())
                prossimo_guasto = minuto + self._rng.randint(
                    ogni_quanti_guasti // 2, ogni_quanti_guasti
                )

        violazioni.extend(self.verifica_invarianti())
        bot = self.bot
        assert bot is not None
        return Esito(
            minuti_simulati=minuti,
            cicli=minuti,
            riavvii=riavvii,
            guasti_iniettati=sum(guasti.values()),
            guasti_per_tipo=guasti,
            decisioni=decisioni_totali,
            esecuzioni=self._conta_esecuzioni(),
            esecuzioni_doppie=doppie,
            errori_non_gestiti=errori,
            invarianti_violati=sorted(set(violazioni)),
        )

    def _conta_esecuzioni(self) -> int:
        bot = self.bot
        if bot is None or bot.ledger is None:
            return 0
        return sum(1 for e in bot.ledger.events() if e.kind.value == "esecuzione")
