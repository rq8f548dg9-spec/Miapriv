"""Broker della modalita' paper: dati veri, esecuzioni simulate.

Due meta' tenute separate apposta.

* Le **letture** vanno su Binance di produzione, agli endpoint pubblici:
  regole dei simboli e profondita' del libro. Inventarle vorrebbe dire
  simulare ordini che nel mondo vero sarebbero stati rifiutati, e una
  prova del genere non dice niente.
* Le **scritture** non esistono. Nessun ordine viene firmato, nessuna
  chiave viene letta: le esecuzioni le calcola ``PaperBroker`` con lo
  stesso modello di costi del backtest.

Chi guarda il codice deve poter verificare in un colpo d'occhio che da
qui non esce nessun ordine: per questo le uniche chiamate di rete stanno
in due metodi, e sono entrambe GET pubbliche.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import SecretStr

from backtest.costs import CostModel
from config.credentials import Credentials
from config.schema import AppConfig, CostsConfig, Mode
from core.clock import Clock
from core.types import SymbolFilters
from execution.binance import AiohttpClient, BinanceBroker, HttpClient
from execution.broker import DepthSnapshot, PaperBroker

__all__ = ["PaperLiveBroker"]


@dataclass
class PaperLiveBroker(PaperBroker):
    """``PaperBroker`` che prende dal mercato vero cio' che si puo' leggere."""

    #: Client di sola lettura verso gli endpoint pubblici. ``None`` lo
    #: rende un PaperBroker normale, utile nei test.
    pubblico: BinanceBroker | None = field(default=None, repr=False)

    @classmethod
    def costruisci(
        cls,
        *,
        config: AppConfig,
        clock: Clock,
        http: HttpClient | None = None,
    ) -> PaperLiveBroker:
        """Costruisce il broker paper con il lettore pubblico collegato.

        Le credenziali passate al lettore sono segnaposto e non vengono
        mai usate: gli endpoint di questa classe sono tutti pubblici, e
        nessuno di essi firma la richiesta. Leggere le chiavi vere per
        non usarle sarebbe un rischio gratuito.
        """
        costs = config.backtest.costs if config.backtest else CostsConfig()
        lettore = BinanceBroker(
            config=config,
            credentials=Credentials(
                api_key=SecretStr("pubblico"),
                api_secret=SecretStr("pubblico"),
                mode=Mode.PAPER,
            ),
            clock=clock,
            http=http or AiohttpClient(),
        )
        return cls(
            clock=clock,
            costs=CostModel(costs),
            filters={},
            balances={config.quote_asset: config.capital_quote},
            quote_asset=config.quote_asset,
            pubblico=lettore,
        )

    async def exchange_filters(self) -> dict[str, SymbolFilters]:
        """Regole dei simboli dal mercato vero, poi tenute in cache.

        Un filtro assente non si inventa: si lascia sollevare, come nel
        bot vero, perche' un MIN_NOTIONAL immaginato fa passare in
        simulazione ordini che l'exchange rifiuterebbe.
        """
        if self.pubblico is None:
            return await super().exchange_filters()
        self.filters = await self.pubblico.exchange_filters()
        return dict(self.filters)

    async def depth_snapshot(self, symbol: str, limit: int = 100) -> DepthSnapshot | None:
        """Libro vero: serve a valutare le posizioni e a riallineare."""
        if self.pubblico is None:
            return await super().depth_snapshot(symbol, limit)
        return await self.pubblico.depth_snapshot(symbol, limit=limit)

    async def close(self) -> None:
        if self.pubblico is not None:
            await self.pubblico.close()
