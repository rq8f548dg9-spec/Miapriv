"""Guardia unica per il passaggio a denaro reale.

Questo modulo esiste perche' il salto da testnet a live sia UNA sola riga di
codice in UN solo file, verificabile a colpo d'occhio. Nessun altro punto del
sistema deve decidere se si sta operando con denaro vero: tutti chiedono qui.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

__all__ = [
    "LIVE_TRADING_ENV_VALUE",
    "LIVE_TRADING_ENV_VAR",
    "LiveTradingNotAuthorizedError",
    "enforce_live_trading_guard",
    "is_live_trading_authorized",
]

logger = logging.getLogger("trading_bot.live_guard")

# ============================================================
# ⚠️  PUNTO DI PASSAGGIO A DENARO REALE
# Modificare SOLO dopo risultati documentati su testnet.
#
# Per operare con denaro reale servono DUE condizioni indipendenti:
#   1. mode: live  nel file di configurazione;
#   2. la variabile d'ambiente qui sotto impostata al valore esatto.
#
# Questa funzione non ha fallback, non ha default permissivi e non va mai
# chiamata dentro un try/except che ne ignori l'eccezione. Se solleva, il
# processo deve morire.
# ============================================================

LIVE_TRADING_ENV_VAR = "I_UNDERSTAND_LIVE_TRADING_RISK"
LIVE_TRADING_ENV_VALUE = "yes"


class LiveTradingNotAuthorizedError(RuntimeError):
    """Sollevata quando ``mode: live`` non e' accompagnato dall'autorizzazione esplicita."""


def is_live_trading_authorized() -> bool:
    """Vero solo se la variabile d'ambiente vale esattamente ``yes``.

    Nessuna normalizzazione tollerante (``YES``, ``true``, ``1`` non valgono):
    il valore deve essere stato scritto da qualcuno che sapeva cosa scriveva.
    """
    return os.environ.get(LIVE_TRADING_ENV_VAR) == LIVE_TRADING_ENV_VALUE


def enforce_live_trading_guard(
    *,
    capital_at_risk: Decimal,
    quote_asset: str,
    active_limits: dict[str, object],
) -> None:
    """Autorizza l'avvio in modalita' live, o impedisce l'avvio.

    Viene chiamata una sola volta, al caricamento della configurazione.

    Raises:
        LiveTradingNotAuthorizedError: se l'autorizzazione esplicita manca.
    """
    if not is_live_trading_authorized():
        raise LiveTradingNotAuthorizedError(
            "mode='live' richiesto ma autorizzazione assente. "
            f"Per operare con denaro reale esporta {LIVE_TRADING_ENV_VAR}="
            f"{LIVE_TRADING_ENV_VALUE} prima di avviare il bot. "
            "Se non sai perche' stai leggendo questo messaggio, resta su mode='testnet'."
        )

    limits_desc = ", ".join(f"{key}={value}" for key, value in sorted(active_limits.items()))
    logger.warning(
        "LIVE TRADING ATTIVO - denaro reale. Capitale a rischio: %s %s. Limiti attivi: %s",
        capital_at_risk,
        quote_asset,
        limits_desc or "nessuno",
    )
