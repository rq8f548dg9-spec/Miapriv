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
