"""Dimensionamento della posizione: Kelly frazionario e filtri di exchange.

Kelly pieno massimizza la crescita logaritmica solo se le probabilita' sono
esatte. Non lo sono mai: una stima di ``p`` ottimista del 5% con Kelly pieno
porta a sovrascommettere in modo rovinoso. Per questo il tetto di config e'
1/4 di Kelly e qui non e' superabile.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from config.schema import SizingConfig
from core.types import SymbolFilters

__all__ = ["PositionSizer", "SizingResult", "kelly_fraction"]


def kelly_fraction(probability: float, payoff_ratio: float) -> float:
    """Frazione di Kelly piena per una scommessa binaria.

    ``f* = p - (1 - p) / b`` con ``b`` = rapporto fra vincita e perdita attese.
    Restituisce 0 quando il valore atteso non e' positivo: nessun edge,
    nessuna scommessa.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"probabilita' {probability} fuori da [0, 1]")
    if payoff_ratio <= 0:
        raise ValueError(f"payoff_ratio {payoff_ratio} non positivo")
    full = probability - (1.0 - probability) / payoff_ratio
    return max(0.0, full)


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Esito del dimensionamento. ``quantity`` e' zero quando non si opera."""

    quantity: Decimal
    notional: Decimal
    kelly_full: float
    kelly_applied: float
    reasons: tuple[str, ...]

    @property
    def is_tradeable(self) -> bool:
        return self.quantity > 0 and not self.reasons


class PositionSizer:
    """Traduce un edge probabilistico in una quantita' inviabile all'exchange.

    L'ordine dei vincoli e' deliberato: prima Kelly (quanto vorremmo
    rischiare), poi i tetti di configurazione (quanto ci permettiamo), infine
    i filtri dell'exchange (quanto e' tecnicamente inviabile). Un ordine che
    non supera l'ultimo passo viene rifiutato qui, prima della rete.

    ``target_order_quote`` e' la misura nominale dell'ordine, non un
    suggerimento: il risultato non la supera mai. Kelly la riduce quando
    l'edge e' debole, e se la riduce sotto ``min_order_quote`` l'ordine non
    si fa, perche' le fee se lo mangerebbero.
    """

    def __init__(self, config: SizingConfig) -> None:
        self._config = config

    def size(
        self,
        *,
        probability: float,
        payoff_ratio: float,
        price: Decimal,
        equity: Decimal,
        filters: SymbolFilters,
        available_quote: Decimal | None = None,
    ) -> SizingResult:
        """Calcola la quantita' per un nuovo ingresso.

        Args:
            probability: probabilita' calibrata di successo del segnale.
            payoff_ratio: vincita attesa / perdita attesa (da take profit e stop).
            price: prezzo di riferimento per convertire notional in quantita'.
            equity: equity corrente, base dei tetti percentuali.
            filters: filtri live di ``exchangeInfo``, mai valori ipotizzati.
            available_quote: liquidita' effettivamente spendibile, se inferiore
                all'equity (parte del capitale e' gia' impegnata).
        """
        if price <= 0:
            return self._reject(f"prezzo di riferimento {price} non positivo")
        if equity <= 0:
            return self._reject(f"equity {equity} non positiva")

        config = self._config
        full = kelly_fraction(probability, payoff_ratio)
        if full <= 0:
            return self._reject(
                f"edge non positivo: p={probability:.4f}, payoff={payoff_ratio:.2f}",
                kelly_full=full,
            )

        applied = min(full * config.kelly_fraction, config.kelly_fraction)
        kelly_notional = equity * Decimal(str(applied))

        # La misura dichiarata e' quella: chi scrive "ordine da 5" si aspetta
        # ordini da 5. Kelly puo' solo ridurla, mai gonfiarla. Senza questo
        # vincolo il valore in configurazione serviva solo alla stima dei
        # costi, e un saldo di prova da migliaia di unita' faceva inviare
        # ordini che il conto vero non potrebbe mai permettersi.
        notional = min(kelly_notional, config.target_order_quote)

        # Tetti: percentuale di equity, massimo assoluto, liquidita' disponibile.
        notional = min(notional, equity * Decimal(str(config.max_order_pct_of_equity)))
        notional = min(notional, config.max_order_quote)
        if available_quote is not None:
            notional = min(notional, available_quote)

        if notional < config.min_order_quote:
            return self._reject(
                f"notional {notional:.4f} sotto il minimo operativo "
                f"{config.min_order_quote}: le fee mangerebbero l'edge",
                kelly_full=full,
                kelly_applied=applied,
            )

        # Soglia dell'exchange con margine: un MIN_NOTIONAL sfiorato viene
        # rifiutato dopo l'invio se il prezzo si muove di un tick.
        required = filters.min_notional * Decimal(str(config.min_notional_safety_factor))
        if notional < required:
            return self._reject(
                f"notional {notional:.4f} sotto MIN_NOTIONAL {filters.min_notional} "
                f"maggiorato del margine di sicurezza ({required:.4f})",
                kelly_full=full,
                kelly_applied=applied,
            )

        quantity = filters.round_quantity(notional / price)
        if quantity <= 0:
            return self._reject(
                f"quantita' azzerata dall'arrotondamento a stepSize {filters.step_size}",
                kelly_full=full,
                kelly_applied=applied,
            )

        # Troncare allo step puo' far scendere l'ordine sotto il minimo
        # dell'exchange: inviarlo cosi' vuol dire farselo rifiutare, e su un
        # conto piccolo e' il caso normale, non l'eccezione. Si prova un passo
        # in piu', che resta comunque sotto il tetto per ordine.
        if quantity * price < required:
            maggiorata = quantity + filters.step_size
            if maggiorata * price <= config.max_order_quote:
                quantity = maggiorata
            else:
                return self._reject(
                    f"quantita' {quantity} vale {quantity * price:.4f}, sotto il "
                    f"minimo con margine ({required:.4f}); il passo successivo "
                    f"({filters.step_size}) sfonderebbe il tetto per ordine "
                    f"{config.max_order_quote}",
                    kelly_full=full,
                    kelly_applied=applied,
                )

        final_notional = quantity * price
        violations = filters.quantity_violations(quantity, price)
        if violations:
            return self._reject(
                *violations, kelly_full=full, kelly_applied=applied
            )

        return SizingResult(
            quantity=quantity,
            notional=final_notional,
            kelly_full=full,
            kelly_applied=applied,
            reasons=(),
        )

    def size_to_close(
        self, *, quantity_to_close: Decimal, price: Decimal, filters: SymbolFilters
    ) -> SizingResult:
        """Quantita' per chiudere una posizione esistente.

        Non passa da Kelly: chiudere e' sempre permesso. Resta pero' il
        vincolo dei filtri, perche' un residuo sotto MIN_NOTIONAL non e'
        liquidabile e va segnalato invece di essere ritentato all'infinito.
        """
        quantity = filters.round_quantity(abs(quantity_to_close))
        if quantity <= 0:
            return self._reject(
                f"residuo {quantity_to_close} inferiore a stepSize {filters.step_size}: "
                "posizione non liquidabile, richiede intervento manuale"
            )
        notional = quantity * price
        if notional < filters.min_notional:
            return self._reject(
                f"residuo di valore {notional:.4f} sotto MIN_NOTIONAL "
                f"{filters.min_notional}: posizione non liquidabile dall'exchange"
            )
        return SizingResult(
            quantity=quantity, notional=notional, kelly_full=0.0, kelly_applied=0.0, reasons=()
        )

    @staticmethod
    def _reject(*reasons: str, kelly_full: float = 0.0, kelly_applied: float = 0.0) -> SizingResult:
        return SizingResult(
            quantity=Decimal(0),
            notional=Decimal(0),
            kelly_full=kelly_full,
            kelly_applied=kelly_applied,
            reasons=tuple(reasons),
        )
