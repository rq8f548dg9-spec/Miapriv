"""Limiti di portafoglio: quante posizioni, quanta esposizione, quanto correlate.

Ogni controllo restituisce la lista dei motivi di rifiuto invece di sollevare:
il risk manager li raccoglie tutti e li scrive nella ``Decision``. Sapere che
un ordine e' stato rifiutato per tre motivi diversi vale piu' che sapere il
primo che e' scattato.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from config.schema import LimitsConfig
from core.portfolio import PortfolioSnapshot
from core.types import Side, TimestampMs

__all__ = ["CorrelationTracker", "LimitChecker", "OrderIntent", "OrderProposal", "OrderRateTracker"]


class OrderIntent(StrEnum):
    """Effetto dell'ordine sull'esposizione. Decide quali limiti si applicano."""

    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"

    @property
    def adds_exposure(self) -> bool:
        return self in (OrderIntent.OPEN, OrderIntent.INCREASE)


@dataclass(frozen=True, slots=True)
class OrderProposal:
    """Ordine candidato, prima dell'approvazione."""

    symbol: str
    side: Side
    quantity: Decimal
    reference_price: Decimal
    intent: OrderIntent

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.reference_price


class CorrelationTracker:
    """Correlazione di Pearson fra i rendimenti recenti dei simboli.

    Due posizioni molto correlate sono una sola posizione di taglia doppia: il
    limite sul numero di posizioni aperte non protegge da questo, serve un
    controllo esplicito.

    Con pochi campioni la stima e' rumorosa: sotto ``min_samples`` restituisce
    ``None`` e il chiamante rifiuta. In dubbio non si apre.

    Ogni rendimento viaggia con l'istante della sua barra, e il confronto
    avviene solo sugli istanti presenti in entrambe le serie. Allineare per
    posizione sembra equivalente e non lo e': basta una barra persa su un
    simbolo (un feed che salta, un simbolo abilitato dopo) perche' le due
    code scivolino e la "correlazione" confronti momenti diversi.
    """

    def __init__(self, lookback: int, min_samples: int) -> None:
        if min_samples > lookback:
            raise ValueError("min_samples non puo' superare lookback")
        self._lookback = lookback
        self._min_samples = min_samples
        self._returns: dict[str, deque[tuple[TimestampMs, float]]] = defaultdict(
            lambda: deque(maxlen=lookback)
        )

    def update(self, symbol: str, bar_return: float, ts_ms: TimestampMs) -> None:
        """Registra un rendimento e l'istante della barra che lo ha prodotto.

        Un valore non finito viene scartato, e cosi' una barra ripetuta: la
        stessa chiusura consegnata due volte non e' una nuova osservazione.
        """
        if not math.isfinite(bar_return):
            return
        serie = self._returns[symbol]
        if serie and serie[-1][0] >= ts_ms:
            return
        serie.append((ts_ms, bar_return))

    def samples(self, symbol: str) -> int:
        return len(self._returns.get(symbol, ()))

    def correlation(self, left: str, right: str) -> float | None:
        """Correlazione sulle barre in comune, o ``None`` se non stimabile."""
        if left == right:
            return 1.0
        a_all, b_all = self._returns.get(left), self._returns.get(right)
        if a_all is None or b_all is None:
            return None
        destra = dict(b_all)
        comuni = [(ts, value) for ts, value in a_all if ts in destra]
        size = len(comuni)
        if size < self._min_samples:
            return None
        a = [value for _, value in comuni]
        b = [destra[ts] for ts, _ in comuni]

        mean_a = sum(a) / size
        mean_b = sum(b) / size
        cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
        var_a = sum((x - mean_a) ** 2 for x in a)
        var_b = sum((y - mean_b) ** 2 for y in b)
        if var_a <= 0 or var_b <= 0:
            # serie costante: correlazione indefinita, non zero
            return None
        return cov / math.sqrt(var_a * var_b)


class OrderRateTracker:
    """Frequenza degli ordini per simbolo.

    Protegge da due cose diverse: i rate limit dell'exchange e il bot che
    entra ed esce in loop su un segnale che oscilla intorno alla soglia.
    """

    def __init__(self) -> None:
        self._timestamps: dict[str, deque[TimestampMs]] = defaultdict(deque)

    def record(self, symbol: str, ts_ms: TimestampMs) -> None:
        self._timestamps[symbol].append(ts_ms)

    def count_last_hour(self, symbol: str, now_ms: TimestampMs) -> int:
        stamps = self._timestamps.get(symbol)
        if not stamps:
            return 0
        cutoff = now_ms - 3_600_000
        while stamps and stamps[0] < cutoff:
            stamps.popleft()
        return len(stamps)

    def seconds_since_last(self, symbol: str, now_ms: TimestampMs) -> float | None:
        stamps = self._timestamps.get(symbol)
        if not stamps:
            return None
        return (now_ms - stamps[-1]) / 1000.0


class LimitChecker:
    """Applica i limiti di portafoglio a un ordine candidato.

    ``pending_exposure`` restituisce il controvalore degli ordini inviati e
    non ancora conclusi, per simbolo. E' esposizione in arrivo: senza
    contarla, due decisioni prese prima che la prima venga eseguita vedono
    entrambe un portafoglio vuoto e il limite sul numero di posizioni non
    protegge piu' da niente.
    """

    def __init__(
        self,
        config: LimitsConfig,
        *,
        max_position_quote: dict[str, Decimal],
        correlations: CorrelationTracker | None = None,
        rates: OrderRateTracker | None = None,
        pending_exposure: Callable[[], Mapping[str, Decimal]] | None = None,
    ) -> None:
        self._config = config
        self._max_position_quote = max_position_quote
        self._pending_exposure = pending_exposure or (lambda: {})
        self._correlations = correlations or CorrelationTracker(
            config.correlation_lookback_bars, config.min_correlation_samples
        )
        self._rates = rates or OrderRateTracker()

    @property
    def correlations(self) -> CorrelationTracker:
        return self._correlations

    @property
    def rates(self) -> OrderRateTracker:
        return self._rates

    def pending(self) -> Mapping[str, Decimal]:
        """Esposizione gia' impegnata da ordini in volo, per simbolo."""
        return {
            symbol: notional
            for symbol, notional in self._pending_exposure().items()
            if notional > 0
        }

    def check(
        self, proposal: OrderProposal, snapshot: PortfolioSnapshot, *, now_ms: TimestampMs
    ) -> tuple[str, ...]:
        """Motivi di rifiuto. Tupla vuota significa approvato dai limiti."""
        reasons: list[str] = []
        reasons.extend(self._check_no_short(proposal, snapshot))

        if proposal.intent.adds_exposure:
            # La frequenza limita gli ingressi, non le uscite: una chiusura
            # rifiutata perche' "troppo presto" lascia aperta una posizione
            # che la strategia voleva abbandonare, ed e' esattamente il
            # contrario di un controllo di rischio.
            reasons.extend(self._check_rate(proposal, now_ms))
            reasons.extend(self._check_position_count(proposal, snapshot))
            reasons.extend(self._check_symbol_cap(proposal, snapshot))
            reasons.extend(self._check_gross_exposure(proposal, snapshot))
            reasons.extend(self._check_correlation(proposal, snapshot))

        return tuple(reasons)

    def _check_no_short(
        self, proposal: OrderProposal, snapshot: PortfolioSnapshot
    ) -> list[str]:
        """Sullo spot non si vende cio' che non si possiede.

        Il tipo ``Position`` ammette quantita' negative perche' servira' ai
        futures, ma su spot una vendita oltre il posseduto verrebbe rifiutata
        dall'exchange dopo l'invio.
        """
        if proposal.side is not Side.SELL:
            return []
        held = snapshot.position(proposal.symbol).quantity
        if proposal.quantity > held:
            return [
                f"vendita di {proposal.quantity} oltre il posseduto {held} su "
                f"{proposal.symbol}: lo spot non ammette scoperto"
            ]
        return []

    def _check_rate(self, proposal: OrderProposal, now_ms: TimestampMs) -> list[str]:
        reasons: list[str] = []
        count = self._rates.count_last_hour(proposal.symbol, now_ms)
        if count >= self._config.max_orders_per_symbol_per_hour:
            reasons.append(
                f"{count} ordini su {proposal.symbol} nell'ultima ora, "
                f"limite {self._config.max_orders_per_symbol_per_hour}"
            )
        elapsed = self._rates.seconds_since_last(proposal.symbol, now_ms)
        if elapsed is not None and elapsed < self._config.min_seconds_between_orders:
            reasons.append(
                f"ultimo ordine su {proposal.symbol} {elapsed:.1f}s fa, "
                f"minimo {self._config.min_seconds_between_orders}s"
            )
        return reasons

    def _check_position_count(
        self, proposal: OrderProposal, snapshot: PortfolioSnapshot
    ) -> list[str]:
        open_symbols = set(snapshot.open_positions) | set(self.pending())
        if proposal.symbol in open_symbols:
            return []
        if len(open_symbols) >= self._config.max_open_positions:
            in_volo = sorted(set(self.pending()) - set(snapshot.open_positions))
            dettaglio = f" (di cui in volo: {in_volo})" if in_volo else ""
            return [
                f"{len(open_symbols)} posizioni aperte{dettaglio}, limite "
                f"{self._config.max_open_positions}: nessuno slot per {proposal.symbol}"
            ]
        return []

    def _check_symbol_cap(
        self, proposal: OrderProposal, snapshot: PortfolioSnapshot
    ) -> list[str]:
        cap = self._max_position_quote.get(proposal.symbol)
        if cap is None:
            return [f"{proposal.symbol} non e' fra i simboli configurati"]
        projected = (
            snapshot.exposure_for(proposal.symbol)
            + self.pending().get(proposal.symbol, Decimal(0))
            + proposal.notional
        )
        if projected > cap:
            return [
                f"esposizione su {proposal.symbol} salirebbe a {projected:.2f}, "
                f"cap di simbolo {cap}"
            ]
        return []

    def _check_gross_exposure(
        self, proposal: OrderProposal, snapshot: PortfolioSnapshot
    ) -> list[str]:
        reasons: list[str] = []
        projected = (
            snapshot.gross_exposure
            + sum(self.pending().values(), Decimal(0))
            + proposal.notional
        )
        if projected > self._config.max_gross_exposure_quote:
            reasons.append(
                f"esposizione lorda salirebbe a {projected:.2f}, "
                f"limite {self._config.max_gross_exposure_quote}"
            )
        equity = snapshot.equity
        if equity > 0:
            pct = float(projected / equity)
            if pct > self._config.max_exposure_pct_of_equity:
                reasons.append(
                    f"esposizione salirebbe al {pct:.1%} dell'equity, "
                    f"limite {self._config.max_exposure_pct_of_equity:.1%}"
                )
        return reasons

    def _check_correlation(
        self, proposal: OrderProposal, snapshot: PortfolioSnapshot
    ) -> list[str]:
        reasons: list[str] = []
        # Anche un ordine in volo e' una posizione, ai fini della
        # correlazione: fra poco sara' aperta davvero.
        for symbol in sorted(set(snapshot.open_positions) | set(self.pending())):
            if symbol == proposal.symbol:
                continue
            rho = self._correlations.correlation(proposal.symbol, symbol)
            if rho is None:
                reasons.append(
                    f"correlazione {proposal.symbol}/{symbol} non stimabile "
                    f"(meno di {self._config.min_correlation_samples} campioni): "
                    "in dubbio non si apre"
                )
                continue
            if abs(rho) > self._config.max_pairwise_correlation:
                reasons.append(
                    f"correlazione {proposal.symbol}/{symbol} = {rho:+.2f}, "
                    f"limite {self._config.max_pairwise_correlation:.2f}: "
                    "sarebbe la stessa posizione di taglia doppia"
                )
        return reasons
