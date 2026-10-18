# Checkpoint corrente — 17 ottobre 2026

Ripresa da Claude `926822e753d9d6662e05be0a404ee7f354d8af54`, successivo a `df3951d`. Questo documento sostituisce le sezioni operative del precedente DEVELOPMENT_HANDOFF.md, conservato come storico.

## Incremento completato

- Integrati senza sovrascritture i commit Claude su ripristino chiusure, deduplica durevole, identificativi decisioni, filtri market e leadership.
- Le vecchie modifiche Codex interrotte sono conservate nello stash locale `preserve interrupted Codex increment before Claude 926822e`. Non applicarlo integralmente: include implementazioni alternative ormai superate da Claude.
- Cancellazioni: recupero stato finale e fill anche se l'esecuzione precede la cancellazione. Stato sconosciuto resta pending. Il kill switch non dichiara completata la chiusura con ordini irrisolti.
- Recupero Binance: GET order non contiene necessariamente i fill. Per quantità eseguita positiva si recupera myTrades filtrato per orderId; quantità cumulative e trade ID devono risultare coerenti. Errori/storico incompleto generano AmbiguousRequestError, non un falso rifiuto dell'ordine. Limite attuale 1000 fill: se incompleti blocca, non inventa; paginazione da implementare.
- Auto-resume non permette aperture finché una chiusura protettiva è pendente.

Fonte del contratto REST verificata: https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/account . Nessuna chiamata autenticata effettuata; trasporto simulato nei test.

## Evidenze

Windows/Python 3.13, ambiente audit esistente:
- Suite completa: **652 passed, 1 skipped, 27 warnings**, 10,02 s.
- Ruff: PASS. Mypy: PASS, 74 file.
- Skip POSIX su Windows; warning aiohttp preesistenti.
- Nuove regressioni prima fallite, poi superate. Revisione indipendente ha rilevato l'assenza dei fill nel payload reale di GET order: corretto con test del trasporto e test per storico incompleto/errore.
- Build: non rieseguita; precedente blocco setuptools non risolto.
- Nessun test live/testnet autenticato, soak 72h o prova di profitto effettuato.

## Simulazione realmente eseguita

Replay offline BTCUSDT e ETHUSDT, candele 1 minuto, dal 1 gennaio 2026 incluso all'8 gennaio escluso UTC. 20.160 candele; circa 3,19 secondi di motore. Archivi locali mensili preesistenti, hash nel risultato JSON. Timestamp microsecondi convertiti in millisecondi. Nessuna ottimizzazione dei parametri.

Capitale 200 → 200 USDT; zero ordini/fill; 9.896 decisioni rifiutate. Tra le ragioni frequenti: edge stimato inferiore ai costi e probabilità inferiore alla soglia. Esito economico **INSUFFICIENTE**, non strategia validata né rendimento garantito. OHLC, filtri generici e costi configurati sono approssimazioni; non validano fill reali o commissioni del conto.

Il backtest storico è già operativo. Non esiste una modalità dedicata PAPER live: le modalità attuali sono backtest/testnet/live. Testnet richiede credenziali e una prova autenticata separata; non è stata avviata.

## Prossimi passi per il paper continuo

1. Modalità paper esplicita: dati di mercato pubblici correnti + broker simulato, impossibilità di inviare ordini autenticati, pannello coerente.
2. Persistenza atomica di contabilità/ordini/protezioni e riavvio paper verificato; controlli di freshness, disconnessione e prenotazione fondi.
3. Soak 72 ore reali con guasti controllati e metriche. Le 72 ore sono solo la durata del collaudo, non una stima del lavoro di sviluppo.

Prima di dichiarare pronto il live: revisione multiprocesso reale del lease di Claude (le letture seguite da unlink/replace non sono una prova di compare-and-swap atomico), crash fra persistenza/fill, fee terzo asset, reinvestimento/flussi esterni, recupero storico >1000 fill. Non assegnare PASS alla leadership basandosi solo su test sequenziali.

Poi: confronto strategie su dati separati e costi realistici, modelli/scenari, apprendimento controllato, MCP shadow, matrice A–J. Nessun punteggio 95/100 assegnato. Non forzare operazioni abbassando le protezioni per rendere positivo un esempio.

Limite noto aggiuntivo: main.run_backtest stampa ancora una frase di bocciatura quando is_viable è falso anche per campione insufficiente; usare assess(metrics) come verdetto e correggere la presentazione CLI.

---

## Incremento Claude successivo (17 ottobre 2026, dopo `0369c2d`)

Merge del lavoro Codex `0369c2d` nel ramo, senza sovrascritture. Un solo
conflitto semantico: il test del timeout percorreva la nuova catena
`GET order` -> `myTrades` con due sole risposte simulate, quindi il parser
iterava su un dizionario. Allineato alla sequenza reale.

Chiuso in questo incremento:

- **Book stantio (D1).** `OrderBookState.last_event_ms` si scriveva e non
  si leggeva mai. Il libro pesa per 1.0 su 2.3 del punteggio assoluto
  della baseline: con il flusso depth fermo e i trade che continuano, il
  bot decideva su uno spessore inesistente. Nuovo `features.max_book_age_ms`
  (default 5s): oltre la soglia le feature di book spariscono dal vettore,
  e `book_age_ms` resta sempre nel vettore per il log della decisione.
- **Timeout del trasporto.** `TransportTimeoutError` distinto: ritentabile
  in lettura, `AmbiguousRequestError` sugli invii. Prima arrivava come
  errore qualunque e il runner marcava l'ordine come rifiutato.
- **Sei parametri orfani (J3).** `data.ws_ping_interval_s`,
  `data.orderbook_resync_s` (riallineamento preventivo, non solo dopo una
  rottura), `data.retention_days` (potatura giornaliera dello storico),
  `data.buffer_capacity` (tetto ai trade in memoria; quando taglia, l'OFI
  sulle finestre non piu' coperte vale `None` invece di un numero su mezza
  finestra), `exchange.max_orders_per_10s` / `max_orders_per_day`
  (contatore degli ordini, diverso dal peso: la finestra breve si aspetta,
  quella giornaliera ferma gli invii), `signals.cooldown_s` (raffreddamento
  per simbolo dopo un segnale). Verificato che nessuno dei sei resti
  dichiarato e non letto.
- **F5.** `main --backtest` non collassa piu' i tre esiti in due:
  POSITIVA esce 0, NEGATIVA esce 1, INSUFFICIENTE esce 3 e dichiara che
  non e' una bocciatura. README aggiornato.

Evidenze su questo container (Linux, Python 3.13): **673 passed**,
`mypy --strict` pulito su 75 file, `ruff check` pulito. Ogni correzione ha
un test scritto prima e visto rosso sul codice precedente; book stantio,
riallineamento e potatura dimostrati portanti anche per mutazione con
ripristino da copia.

Prova di installazione da pacchetto pulito, eseguita davvero: archivio
estratto in una cartella vuota, venv creato, `pip install -e .` riuscito
(il blocco setuptools segnalato prima non si ripresenta qui),
`python -m main --check` valido, pannello su 127.0.0.1:8777 che risponde
200 senza errori nel log. Backtest su 14.400 candele sintetiche: 0 ordini,
7.010 rifiuti per edge inferiore ai costi, esito INSUFFICIENTE. Nessuna
chiamata autenticata, nessun ordine, nessun test live o testnet.

Resta aperto, invariato rispetto all'elenco sopra: ledger a eventi con
conferimenti e prelievi distinti dal PnL, user data stream come fonte
autorevole dei fill (API nuova, non listenKey), modalita' paper continua,
modello e motore scenari, apprendimento, bridge MCP, soak 72 ore,
paginazione oltre 1000 fill, revisione multiprocesso vera del lease.
Nessun punteggio della matrice A-J assegnato.

---

## Incremento Claude del 18 ottobre 2026 (dopo `557a209`)

Chiusi in questo blocco, tutti con test scritti prima e suite verde a
ogni commit (823 test, mypy --strict e ruff puliti):

1. **Registro a eventi del patrimonio** (`core/ledger.py`): conferimenti,
   prelievi ed esecuzioni distinti; il tetto della riconciliazione segue
   il capitale in gestione, non il primo deposito. Era il punto in cui il
   reinvestimento si perdeva a ogni riavvio.
2. **Preriscaldamento dallo storico** e riquadro "Cosa sta succedendo":
   il bot non aspetta piu' quattro ore prima di decidere, e il pannello
   dice a che punto e'.
3. **Backtest con il flusso ordini**: prima girava con meta' degli
   ingressi spenti. Aggiunti `read_trades`, `agg_trades` (pubblico,
   paginato per id) e lo scarico dal pannello.
4. **Tariffe realistiche**: il maker era 1 bp contro i 10 veri. Aggiunto
   `fee_discount_pct` (BNB), spento di default.
5. **Esploratore della frequenza**: operazioni/giorno, costo giornaliero
   e vincolo che le ferma, per impostazione e per durata della barra.
6. **Modalita' paper**: mercato vero, ordini simulati, nessuna chiave.
   `touches_exchange` falso. E' il default del file versionato.
7. **Motore scenari** con ricampionamento a blocchi.
8. **Apprendimento**: esperimenti con promozione per coerenza fra
   finestre, registro con impronta dei dati, nessuna applicazione
   automatica, protezioni escluse per costruzione.
9. **Modello predittivo**: dataset con etichetta che tiene conto dei
   costi, logistica scritta a mano (nessuna dipendenza nuova), guardie su
   calibrazione e AUC.
10. **Prova di resistenza** a tempo accelerato con cinque guasti.
11. **Ponte verso l'assistente**: su file, spento, non puo' aprire.
12. **Flusso account** su `userDataStream.subscribe.signature`.

Evidenze eseguite in questa sessione: addestramento su 14.400 candele
sintetiche (calibrazione 0,017, AUC 0,46 — calibrato e senza capacita'
predittiva, che su una passeggiata casuale e' corretto); resistenza 24
ore simulate con 7 guasti, 458 decisioni, 12 esecuzioni, nessun
invariante violato; esploratore che mostra 6,4 bps di movimento atteso
contro 22,2 di costo su barre da un minuto, 16,4 su quindici.

Vedi `STATO.md` per il quadro gruppo per gruppo. Nessun punteggio
assegnato. Restano aperti: 72 ore reali, testnet autenticata, flusso
account provato contro l'endpoint vero, lease con due processi veri,
paginazione oltre 1000 fill, equity in euro, dati di mercato veri per
qualunque affermazione economica.
