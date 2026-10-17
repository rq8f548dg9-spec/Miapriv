# Trading bot modulare — testnet first

Bot di trading algoritmico per Binance Spot, costruito per **misurare
onestamente** se una strategia funziona, non per confermare che funzioni.

> Assunzione di partenza: l'edge predittivo **non esiste** finché non è
> dimostrato dai dati, al netto dei costi di transazione.

Stato: tutte e 8 le fasi implementate, più un pannello di controllo nel
browser. 424 test, `mypy --strict` e `ruff` puliti. Il modello ML resta un
segnaposto dichiarato: si implementa solo se la baseline a regole regge i
costi nel backtest.

---

# Guida all'uso

## La via facile: il pannello nel browser

**Doppio clic su `avvia.sh`** (Mac e Linux) o **`avvia.bat`** (Windows).

Al primo lancio prepara l'ambiente da solo (un minuto), poi apre il pannello
nel browser all'indirizzo `http://127.0.0.1:8777`. Da lì si fa tutto, senza
scrivere un comando:

| Nel pannello | Cosa fa |
|---|---|
| **Passo 1 — Collega la testnet** | incolli chiave e segreto, lui li salva in `.env` e verifica subito la connessione mostrandoti i saldi |
| **Passo 2 — Scarica lo storico** | scegli quanti giorni e premi: i dati finiscono in `data_store/` |
| **Passo 3 — Prova la strategia** | esegue il backtest e ti dice se regge le commissioni, con l'elenco dei motivi per cui non ha operato |
| **Avvia il bot** | lo fa partire sulla testnet; il pulsante resta spento finché mancano le chiavi, e la pagina spiega perché |
| **Situazione** | capitale, guadagno, perdita di giornata, posizioni, stato del kill switch |
| **Andamento del capitale** | grafico aggiornato ogni cinque secondi, con i valori leggibili anche in tabella |
| **Decisioni del bot** | ogni valutazione, apribile per vedere i numeri che l'hanno prodotta. I rifiuti sono i più istruttivi: dicono cosa manca alla strategia |
| **Chiudi tutte le posizioni** | uscita immediata a prezzo di mercato |
| **Impostazioni** | capitale, taglia degli ordini, limiti di rischio e soglie, validati prima di essere salvati |

Dove serve ancora la tastiera: solo per ottenere le chiavi (vai su
**testnet.binance.vision** e accedi con GitHub) e, se un giorno lo vorrai,
per il passaggio a denaro reale.

### Tre cose che il pannello NON può fare, di proposito

- **non si affaccia sulla rete**: ascolta solo su `127.0.0.1`, quindi è
  raggiungibile solo dal tuo computer. Un'interfaccia che può muovere denaro
  non va esposta, nemmeno sulla rete di casa;
- **non può passare a denaro reale**: quel passaggio resta una modifica al
  file di configurazione più una variabile d'ambiente. Un bottone sarebbe
  troppo facile da premere;
- **non mostra mai le chiavi**: le riceve, le scrive in `.env` con permessi
  `600`, e da quel momento risponde soltanto «configurate: sì».

### Se il doppio clic non funziona

Su Mac, la prima volta: tasto destro sul file → *Apri*. Oppure dal terminale:

```bash
cd trading-bot
./avvia.sh
```

Serve Python 3.11 o superiore. Lo script lo verifica e te lo dice.

---

## La via da terminale

Resta disponibile per chi la preferisce, e fa esattamente le stesse cose.

```bash
cd trading-bot
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest && mypy . && ruff check .      # 424 test

cp .env.example .env                   # poi incolla le chiavi testnet
python -m main --check                 # valida la configurazione
python scripts/fetch_history.py --days 30
python -m main --backtest              # esce con codice 1 se non regge i costi
python -m main                         # avvia il bot sulla testnet
python -m main --ui                    # ...oppure apri il pannello
```

Mentre gira, le decisioni si leggono così:

```bash
tail -f logs/trading_bot.jsonl | jq 'select(.event=="decision")'
```

### Girare 24/7 con Docker

```bash
docker compose up -d --build
docker compose logs -f
```

I volumi montati fanno sopravvivere dati, log, stato e registro ordini al
riavvio del container. Il bot gira come utente non privilegiato e non espone
porte.

### Quando il bot si ferma da solo

Il kill switch scatta su: perdita giornaliera oltre il 3%, perdita totale
oltre il 10%, 5 perdite consecutive, latenza ordini oltre 2 secondi, feed
dati fermo da più di 10 secondi, più di 10 riconnessioni in un'ora,
divergenza fra stato locale ed exchange.

Allo scatto chiude tutte le posizioni e **non riparte da solo**:
`auto_resume: false` è voluto. Dopo uno stop deve guardare i log una persona.
Nel pannello lo vedi come badge rosso con il motivo.

### Il passaggio a denaro reale

**Non farlo** finché non hai settimane di testnet documentate e un backtest
walk-forward che regge i costi su finestre diverse.

Quando quel momento arriva servono **due** azioni indipendenti:

1. in `config/config.yaml`: `mode: live`;
2. nell'ambiente: `I_UNDERSTAND_LIVE_TRADING_RISK=yes` (valore esatto:
   `YES`, `true`, `1`, `sì` non valgono);

più le chiavi live in `.env`, in variabili con nomi diversi da quelle testnet
(`BINANCE_LIVE_API_KEY`, `BINANCE_LIVE_API_SECRET`). Se manca anche solo una
delle due condizioni il processo non parte. Tutto questo sta in un solo file,
`config/live_guard.py`, sotto il banner
`⚠️ PUNTO DI PASSAGGIO A DENARO REALE`.

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
webui/      server.py + static/index.html (pannello nel browser)
runner.py   cablaggio    main.py  CLI    scripts/fetch_history.py
avvia.sh / avvia.bat     lancio con doppio clic
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
