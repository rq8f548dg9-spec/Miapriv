# Trading bot modulare — testnet first

Scheletro di bot di trading algoritmico per Binance, costruito per **misurare
onestamente** se una strategia funziona, non per confermare che funzioni.

> Assunzione di partenza: l'edge predittivo **non esiste** finche' non e'
> dimostrato dai dati, al netto dei costi di transazione.

## Stato di avanzamento

| Fase | Contenuto | Stato |
|-----|-----------|-------|
| 1 | config + schema pydantic + `core/types` + `core/clock` | ✅ fatto |
| 2 | risk manager completo con test (sizing, limits, killswitch) | ⬜ |
| 3 | data layer: ingest WebSocket + orderbook + store Parquet | ⬜ |
| 4 | feature engine in streaming | ⬜ |
| 5 | signals/rules baseline | ⬜ |
| 6 | backtest engine con costi + test no-lookahead | ⬜ |
| 7 | execution su testnet + reconcile + idempotency | ⬜ |
| 8 | monitoring e alerting | ⬜ |

`signals/ml.py` resta un placeholder: si implementa solo se la baseline a
regole regge i costi di transazione nel backtest.

## Modalita'

Tre modalita', impostate da `mode:` in `config/config.yaml`:

- `backtest` — nessuna chiave API, nessuna connessione all'exchange;
- `testnet` — **default**, opera su `testnet.binance.vision`;
- `live` — denaro reale.

### Passaggio a denaro reale

Il passaggio sta in **un solo file**: `config/live_guard.py`, sotto il banner
`⚠️ PUNTO DI PASSAGGIO A DENARO REALE`. Servono due condizioni indipendenti:

1. `mode: live` nella configurazione;
2. la variabile d'ambiente `I_UNDERSTAND_LIVE_TRADING_RISK=yes` (valore esatto,
   `YES` e `true` non valgono).

Se la seconda manca, `load_config()` solleva `LiveTradingNotAuthorizedError` e
il processo non parte. La guardia non ha default permissivi, non ha fallback e
non va mai chiamata dentro un `try/except` che ne ignori l'eccezione.

Le chiavi API stanno in `.env` (mai nel codice, mai nei log) con nomi distinti
fra testnet e live: `BINANCE_TESTNET_API_KEY` / `BINANCE_LIVE_API_KEY`. Una
configurazione sbagliata produce un errore d'avvio, non un ordine reale.

## Avvio rapido

```bash
pip install -e ".[dev]"
cp .env.example .env        # compilare le chiavi testnet
pytest                      # 78 test
mypy config core tests      # strict, zero errori
ruff check .
```

Caricamento della configurazione:

```python
from config.credentials import load_credentials, load_dotenv
from config.schema import load_config

load_dotenv()
config = load_config("config/config.yaml")     # valida, e autorizza se live
creds = load_credentials(config.mode)          # None in backtest
```

## Principi applicati nel codice

1. **Il risk manager prima del modello.** La Fase 2 precede qualunque segnale.
2. **Nessun look-ahead.** `Candle.decision_time_ms` solleva su una candela non
   chiusa; `SimulatedClock.advance_to` rifiuta un tempo che torna indietro.
3. **Nessun valore hardcoded.** Tutto in `config.yaml`, validato con pydantic;
   `extra="forbid"` fa fallire l'avvio su un refuso invece di ignorarlo.
4. **Ogni decisione e' loggabile.** `Decision` rifiuta di esistere se e'
   negativa senza motivazione o positiva senza ordine.
5. **Costi sempre simulati.** `Position.apply_fill` sottrae sempre le fee;
   `CostsConfig` modella maker/taker, spread, slippage e latenza.

## Struttura

```
config/     config.yaml, schema.py (pydantic), live_guard.py, credentials.py
core/       types.py (Order, Fill, Position, Signal, Candle...), clock.py
data/       ingest, orderbook, buffer, store        (fase 3)
features/   engine, microstructure, volatility, momentum   (fase 4)
signals/    base, rules, ml (placeholder)           (fase 5)
risk/       manager, sizing, limits, killswitch     (fase 2)
execution/  broker, binance, idempotency, reconcile (fase 7)
backtest/   engine, costs, walkforward, metrics     (fase 6)
monitoring/ logger, state, alerts                   (fase 8)
tests/
```
