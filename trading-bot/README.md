# Trading bot modulare — testnet first

Bot di trading algoritmico per Binance Spot, costruito per **misurare
onestamente** se una strategia funziona, non per confermare che funzioni.

> Assunzione di partenza: l'edge predittivo **non esiste** finché non è
> dimostrato dai dati, al netto dei costi di transazione.

Stato: tutte e 8 le fasi implementate. 397 test, `mypy --strict` e `ruff`
puliti. Il modello ML resta un segnaposto dichiarato: si implementa solo se
la baseline a regole regge i costi nel backtest.

---

# Guida all'uso, dall'inizio alla fine

## Passo 1 — Installare

Serve Python 3.11 o superiore.

```bash
cd trading-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verifica che tutto funzioni prima di andare avanti:

```bash
pytest                 # 397 test
mypy .                 # strict, zero errori
ruff check .
```

Se i test non passano, fermati qui: non c'è motivo di collegare un exchange
a un codice che non supera i propri controlli.

## Passo 2 — Creare le chiavi della testnet

1. Vai su **https://testnet.binance.vision** e accedi con GitHub.
2. Genera una coppia di chiavi API (HMAC-SHA256).
3. Copia il file di esempio e incollale:

```bash
cp .env.example .env
```

```ini
BINANCE_TESTNET_API_KEY=la-tua-chiave
BINANCE_TESTNET_API_SECRET=il-tuo-segreto
```

Il file `.env` non è versionato (`.gitignore`). Le chiavi non compaiono mai
nel codice né nei log: nei log sono `SecretStr`, e nelle chiamate HTTP il
segreto entra solo nella firma HMAC, mai nella query.

La testnet dà saldi fittizi (di solito qualche migliaio di USDT). Il
`capital_quote: 200` in configurazione è il capitale che il bot si autorizza
a usare, indipendentemente dal saldo disponibile.

## Passo 3 — Leggere e capire la configurazione

Tutto sta in `config/config.yaml`. I valori che contano davvero:

```yaml
mode: testnet            # backtest | testnet | live
capital_quote: 200       # capitale di riferimento
quote_asset: USDT

risk:
  sizing:
    kelly_fraction: 0.25     # tetto non superabile: mai Kelly pieno
    target_order_quote: 5    # ordine nominale ~5 USDT
    max_order_quote: 20
    min_order_quote: 5
  limits:
    max_open_positions: 2
    max_gross_exposure_quote: 100
  killswitch:
    max_daily_drawdown_pct: 0.03    # -3% in giornata -> stop
    max_total_drawdown_pct: 0.10    # -10% complessivo -> stop
    max_consecutive_losses: 5
```

Controlla che sia valida prima di qualunque cosa:

```bash
python -m main --check
```

La validazione è **fail-fast**: un refuso in una chiave (`kely_fraction`)
blocca l'avvio invece di essere ignorato in silenzio, e le incoerenze
vengono rifiutate con il motivo (esposizione oltre il capitale, drawdown
giornaliero oltre quello totale, posizioni massime oltre i simboli
abilitati).

## Passo 4 — Scaricare i dati storici

```bash
python scripts/fetch_history.py --days 30
```

Scarica le candele da Binance e le salva in `data_store/` come Parquet
partizionato per giorno e simbolo. Con `--days 90` ne scarichi di più; per
un walk-forward servono almeno un paio di mesi.

## Passo 5 — Backtest: la strategia regge i costi?

```bash
# modo rapido, senza toccare config.yaml
python -m main --backtest
```

Stampa una riga di sintesi e un verdetto:

```
barre=43200 ordini=128 rifiuti=3477 equity 200 -> 203.41 | periodi=43200
operazioni=61 sharpe=0.84 maxDD=4.12% winrate=0.541 PF=1.18 fee=1.9321
```

Il comando esce con codice `1` e un messaggio esplicito se la strategia non
regge i costi. **Questo è il cancello del progetto**: se non lo si supera,
non si passa né al denaro vero né al modello predittivo.

Cosa aspettarsi, onestamente: sulle sole candele la baseline a regole quasi
non opera. Le sue tre gambe (order flow imbalance, book imbalance,
microprice) richiedono trade e book, che le candele non contengono, quindi
l'edge atteso resta sotto i ~22 bps di costo di un giro completo e il risk
manager rifiuta. Il motivo di ogni rifiuto è scritto nei log:

```bash
jq -r 'select(.event=="decision" and .approved==false) | .rejection_reasons[]' \
  logs/trading_bot.jsonl | sort | uniq -c | sort -rn | head
```

Per dare alla baseline i dati che le servono bisogna raccogliere trade e
book dal vivo (passo 6): il bot li salva in `data_store/` mentre gira.

## Passo 6 — Far girare il bot in testnet

```bash
python -m main
```

Cosa succede all'avvio, in quest'ordine:

1. legge i filtri da `exchangeInfo` (tick size, step size, MIN_NOTIONAL);
2. chiede all'exchange il destino degli ordini rimasti in volo;
3. **ricostruisce lo stato dai saldi reali**, non dal file locale;
4. apre il WebSocket su trade, candele e profondità dei simboli abilitati;
5. entra nel ciclo: feature → segnale → risk manager → ordine.

Mentre gira salva tutto in `data_store/`, così alla sessione successiva il
backtest avrà anche i dati di microstruttura.

Per fermarlo: `Ctrl+C`. Alla chiusura salva lo stato e svuota i buffer.

### Cosa guardare mentre gira

```bash
# le decisioni, una riga JSON ciascuna
tail -f logs/trading_bot.jsonl | jq 'select(.event=="decision")'

# solo gli ordini approvati
tail -f logs/trading_bot.jsonl | jq 'select(.event=="decision" and .approved)'

# la curva di equity
jq -r 'select(.event=="equity") | [.ts_ms, .equity] | @tsv' logs/trading_bot.jsonl

# lo stato corrente
cat state/bot_state.json | jq
```

Ogni decisione porta con sé **tutte le feature che l'hanno generata** e lo
stato di rischio del momento. È quello che rende possibile, settimane dopo,
rispondere a "perché ha comprato alle 03:12".

### Quando il bot si ferma da solo

Il kill switch scatta su: drawdown giornaliero oltre il 3%, drawdown totale
oltre il 10%, 5 perdite consecutive, latenza ordini oltre 2 secondi, feed
dati fermo da più di 10 secondi, più di 10 riconnessioni in un'ora,
divergenza fra stato locale ed exchange.

Allo scatto chiude tutte le posizioni a mercato e **non riparte da solo**:
`auto_resume: false` è voluto. Dopo uno stop deve guardare i log una
persona. Per ripartire: leggi `state/bot_state.json` e i log, capisci cosa
è successo, poi riavvia il processo.

## Passo 7 — Girare 24/7 con Docker

```bash
docker compose up -d --build
docker compose logs -f
```

I volumi montati fanno sopravvivere dati, log, stato e registro ordini al
riavvio del container. Il bot gira come utente non privilegiato e non espone
porte: apre connessioni, non ne accetta.

Per fermarlo: `docker compose down`.

## Passo 8 — Il passaggio a denaro reale

**Non farlo** finché non hai: settimane di testnet documentate, un backtest
walk-forward che regge i costi su finestre diverse, e la certezza di saper
spiegare ogni decisione presa dal bot.

Quando quel momento arriva, servono **due** azioni indipendenti:

1. in `config/config.yaml`: `mode: live`;
2. nell'ambiente: `I_UNDERSTAND_LIVE_TRADING_RISK=yes` (valore esatto:
   `YES`, `true`, `1`, `sì` non valgono);

e le chiavi live in `.env`, in variabili con nomi diversi da quelle testnet:

```ini
BINANCE_LIVE_API_KEY=...
BINANCE_LIVE_API_SECRET=...
```

Se manca anche solo una delle due condizioni il processo non parte e
`LiveTradingNotAuthorizedError` dice perché. Tutto questo sta in un solo
file, `config/live_guard.py`, sotto il banner
`⚠️ PUNTO DI PASSAGGIO A DENARO REALE`. Non ci sono default permissivi, non
ci sono fallback, e la guardia non è mai chiamata dentro un `try/except` che
ne ignori l'eccezione.

Prima del primo avvio reale, abbassa i limiti: `max_gross_exposure_quote` a
una frazione del capitale e `max_daily_drawdown_pct` a una soglia che sei
disposto a perdere davvero.

---

# Come è fatto

```
config/     config.yaml, schema.py (pydantic), live_guard.py, credentials.py
core/       types.py, clock.py, portfolio.py
data/       ingest.py (WebSocket), orderbook.py (L2), buffer.py, store.py
features/   engine.py, microstructure.py, volatility.py, momentum.py
signals/    base.py, rules.py (baseline), ml.py (segnaposto)
risk/       manager.py, sizing.py, limits.py, killswitch.py
execution/  broker.py, binance.py, idempotency.py, reconcile.py
backtest/   engine.py, costs.py, walkforward.py, metrics.py
monitoring/ logger.py, state.py, alerts.py
runner.py   cablaggio    main.py  CLI    scripts/fetch_history.py
```

## I principi, e dove sono applicati nel codice

**1. Il risk manager prima del modello.** `risk/` è stato scritto prima di
`signals/`. Nessun componente costruisce ordini: solo `RiskManager.evaluate`
può approvarne uno, e ogni valutazione produce una `Decision` loggabile.

**2. Nessun look-ahead.** `Candle.decision_time_ms` solleva su una candela
non chiusa; il feature engine rifiuta le candele aperte e quelle fuori
ordine; le finestre di order flow scartano i trade successivi all'istante di
decisione; `SimulatedClock.advance_to` rifiuta un tempo che arretra; nel
backtest la decisione è alla chiusura della barra *i* e l'esecuzione
all'apertura della barra *i+1*. Il test
`tests/test_no_lookahead.py::test_il_futuro_non_cambia_il_passato` perturba
i prezzi dopo un istante di taglio e verifica che ogni decisione precedente
resti identica.

**3. Nessun valore hardcoded.** Tutto in `config.yaml`, validato con
pydantic, `extra="forbid"` e `frozen=True`: un refuso blocca l'avvio e
nessun componente può allentare un limite a runtime.

**4. Ogni decisione è loggata con le sue feature.** `Decision` rifiuta di
esistere se è negativa senza motivazione o positiva senza ordine. Anche i
rifiuti finiscono nel log, e sono i più istruttivi.

**5. Costi sempre simulati.** `Position.apply_fill` sottrae sempre le fee; il
`CostModel` applica fee maker/taker, slippage crescente con la size e
latenza; il risk manager confronta l'edge **al netto** dei costi con una
soglia minima.

## Scelte che vale la pena conoscere

- **Kelly al massimo 1/4**, come tetto di tipo e non come convenzione: una
  stima di probabilità ottimista del 5% con Kelly pieno porta a
  sovrascommettere in modo rovinoso.
- **In dubbio non si apre**: se la correlazione fra due simboli non è
  stimabile per pochi campioni, l'apertura viene rifiutata.
- **Chiudere è sempre permesso**, anche a kill switch scattato e a limiti
  saturi: ridurre il rischio non si blocca mai.
- **Niente scoperto**: sullo spot una vendita oltre il posseduto viene
  rifiutata prima dell'invio. `Position` supporta quantità negative perché
  servirà ai futures.
- **Client order id deterministici**: lo stesso ordine ricalcolato dopo un
  riavvio produce lo stesso id, e l'exchange rifiuta il duplicato. Il
  secondo invio diventa una domanda invece di un secondo ordine.
- **La verità sta sull'exchange**: al riavvio lo stato si ricostruisce dai
  saldi reali. Il prezzo di carico non è deducibile dai saldi, quindi si
  dichiara un PnL non realizzato nullo invece di inventarne uno falso.
- **Un book con un buco è un book sbagliato**: dopo una perdita di sequenza
  il book viene invalidato e le sue feature spariscono, finché un nuovo
  snapshot non lo riallinea.
- **Niente numeri senza dati**: volatilità, ATR, z-score, Sharpe e profit
  factor restituiscono `None` quando i campioni non bastano, invece di un
  numero che sembra valido.

## Quando (e se) implementare il modello

`signals/ml.py` oggi solleva `ModelNotImplementedError`. Contiene però il
contratto che un modello dovrà rispettare e gli strumenti per verificarlo:
`reliability_curve` ed `expected_calibration_error`. La probabilità deve
essere **calibrata** — fra gli eventi a cui il modello assegna 0,60 deve
verificarsene circa il 60% — perché Kelly la prende sul serio, e con
probabilità gonfiate dimensiona posizioni rovinose. Soglia di buon senso:
errore di calibrazione sopra 0,05 significa modello inutilizzabile.

Il momento di scriverlo è quando la baseline a regole, nel backtest con i
costi attivi, supera `is_viable` su più finestre di walk-forward. Non prima.
