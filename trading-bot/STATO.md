# Stato del lavoro — 18 ottobre 2026

Questo documento dice cosa c'è, cosa è stato verificato **eseguendolo**, e
cosa manca. È organizzato sui dieci gruppi della matrice di accettazione.

**Non assegno un punteggio.** Per due motivi, entrambi concreti: non ho
davanti il testo dei cinque requisiti di ciascun gruppo (serve il
documento originale per contarli uno per uno), e soprattutto diverse voci
richiedono prove che oggi non esistono — un conto autenticato, giorni
veri di mercato, operazioni reali. Un numero costruito senza quelle prove
sarebbe esattamente l'autocertificazione che il progetto vieta.

Le quattro cose restano separate, come richiesto:

| | Stato |
|---|---|
| **Conformità tecnica** | buona parte implementata e coperta da test eseguiti |
| **Robustezza osservata** | 24 ore **simulate** con guasti, nessun invariante violato; 72 ore reali **non fatte** |
| **Evidenza economica** | **INSUFFICIENTE** — nessuna operazione reale, nessun campione valido |
| **Autorizzazione operativa** | **nessuna** — live disabilitato, testnet mai contattata, nessun ordine autenticato inviato |

---

## A — Patrimonio e reinvestimento

**Fatto.** Registro a eventi in sola aggiunta (`core/ledger.py`):
conferimenti, prelievi ed esecuzioni sono movimenti distinti. Il
patrimonio corrente non è un numero salvato, è il risultato di rileggerli
tutti. Il tetto della riconciliazione segue il capitale in gestione, non
il primo deposito: cento che diventano centotrenta restano centotrenta
anche dopo un riavvio. Un versamento non cancella un calo in corso. Il
pannello mostra i tre numeri separati e permette di registrare movimenti.

**Verificato:** test di rilettura, troncamento dell'ultima riga dopo un
crash, rifiuto di una riga rotta in mezzo, continuità del guadagno
attraverso un riavvio.

**Manca:** equity in euro (oggi tutto in valuta di conto); nessuna prova
con denaro vero.

## B — Ordini ed esecuzione

**Fatto.** Idempotenza con client order id derivato dal contenuto;
registro ordini persistente scritto **prima** dell'invio; timeout trattato
come esito sconosciuto (si interroga, non si rimanda); recupero dei fill
da `myTrades` quando la risposta non li contiene; unicità dell'emittente
con lease e fencing token; contatore degli ordini sui 10 secondi e sulle
24 ore; flusso dati dell'account sulla WebSocket API firmata.

**Verificato:** test su tutti i punti sopra, compresi lo scenario
invio-crash-riavvio-recupero e la deduplica di un fill ripetuto.

**Manca, ed è importante:** la stretta di mano del flusso account con
l'endpoint vero non è stata provata (richiede credenziali). Il lease è
provato in sequenza, non con due processi veri in corsa. Paginazione oltre
i 1000 fill non implementata (oggi blocca invece di inventare).

## C — Rischio

**Fatto.** Kelly frazionario con tetti su ordine, esposizione e posizioni;
kill switch su perdita giornaliera, totale e perdite consecutive, con
chiusura forzata che si riarma dopo un crash; stop e target fissati sul
prezzo di carico effettivo; nessuna leva, nessuno scoperto, nessun
aumento automatico dei limiti in nessun punto del codice.

**Verificato:** test, più una verifica strutturale: gli esperimenti sui
parametri rifiutano per costruzione qualunque percorso sotto `risk.`,
`mode` o `exchange.`.

**Manca:** nessuna prova del comportamento delle protezioni su mercato
reale in movimento.

## D — Dati

**Fatto.** Candele e flusso ordini scaricabili dagli endpoint pubblici;
libro differenziale con riallineamento sia dopo un buco sia periodico;
book stantio escluso dalle feature; tetto ai trade in memoria che, quando
taglia, rende indisponibili le finestre non più coperte invece di
pubblicare numeri su mezza finestra; potatura giornaliera dello storico.

**Verificato:** test su ciascun punto; backtest ora alimentato anche dal
flusso ordini, con prova anti look-ahead.

**Manca:** il libro storico non è recuperabile da endpoint pubblici,
quindi il backtest resta un limite inferiore. È dichiarato nel codice e
nel pannello.

## E — Modelli e scenari

**Fatto.** Dataset etichettato tenendo conto dei costi; regressione
logistica con normalizzazione salvata nel modello e regolarizzazione
sempre attiva; misura di calibrazione **e** di capacità di distinguere
(AUC), perché un modello può essere calibrato e inutile; rifiuto di
salvare un modello scalibrato. Motore degli scenari con ricampionamento a
blocchi, percentili, probabilità di perdita e di stop, distribuzione del
calo peggiore; rifiuto di produrre percentili sotto venti operazioni.

**Verificato eseguendolo:** addestramento su 14.400 candele sintetiche →
13.912 campioni, 10,8% positivi, errore di calibrazione 0,017, **AUC
0,46**. Cioè: calibrato e senza alcuna capacità predittiva, che su una
passeggiata casuale è il risultato corretto. Lo script lo dichiara.

**Manca:** un modello addestrato su dati veri. Nessuno è acceso.

## F — Validazione economica

**Fatto.** Tre esiti tenuti separati (positivo / negativo /
insufficiente), con codici di uscita distinti; walk-forward con purge ed
embargo; esploratore della frequenza che misura operazioni al giorno,
costo giornaliero e vincolo che le ferma, su impostazioni e su orizzonti
diversi.

**Verificato eseguendolo:** su dati sintetici, movimento atteso 6,4 punti
base contro 22,2 di costo per giro; su barre più lunghe 12,6 e 16,4. Zero
operazioni a ogni impostazione, e il motivo è scritto con i numeri.

**Evidenza economica: INSUFFICIENTE.** Nessuna operazione reale è mai
stata eseguita. Non esiste alcun dato che suggerisca che questa strategia
guadagni.

## G — Apprendimento

**Fatto.** Esperimenti con regola di promozione scritta **prima**, basata
sulla coerenza fra finestre separate e non sulla media; registro in sola
aggiunta con impronta dei dati, che sa dire se una prova è già stata
fatta; nessuna applicazione automatica.

**Verificato:** test, incluso uno che legge il modulo e controlla che non
scriva configurazione.

**Manca:** esperimenti su dati veri.

## H — Operatività

**Fatto.** Modalità paper (mercato vero, ordini simulati, nessuna
chiave); preriscaldamento dallo storico che toglie le quattro ore di
attesa; riquadro «Cosa sta succedendo» con stato in parole, barre
raccolte ed età dell'ultimo dato; prova di resistenza a tempo accelerato
con cinque tipi di guasto e invarianti verificati dopo ognuno.

**Verificato eseguendolo:** 24 ore simulate, 7 guasti, 458 decisioni, 12
esecuzioni, nessun invariante violato, nessun errore non gestito.
Installazione da archivio pulito verificata (ambiente, dipendenze,
configurazione valida, pannello che risponde).

**Manca, e non è sostituibile:** le 72 ore reali. Il tempo simulato non
dice niente su perdite di memoria, descrittori accumulati, certificati
scaduti o limiti dell'API raggiunti dopo ore vere.

## I — Collegamento con l'assistente

**Fatto.** Ponte su file verso l'assistente già autorizzato sul computer.
Nessuna chiave di nessun fornitore di modelli nel bot, nessuna chiamata
di rete. Spento di default; acceso, può solo **fermare** un'apertura, mai
aprirne una. I campi che somigliano a un segreto non escono dal processo.

**Verificato:** test, fra cui due strutturali — il modulo non importa
l'esecuzione e non chiama `submit`, e le modalità sono esattamente
`{ombra, veto}`.

**Manca:** uso vero con l'assistente collegato (richiede l'autorizzazione
esplicita prevista dal progetto).

## J — Riproducibilità

**Fatto.** 823 test, `mypy --strict` e `ruff` puliti a ogni commit;
nessun parametro dichiarato e mai letto (verificato); seme fisso negli
scenari; configurazione validata prima di qualunque scrittura; registri
in sola aggiunta per patrimonio ed esperimenti.

**Manca:** build del pacchetto non rieseguita in questa sessione oltre
all'installazione da archivio; nessuna pipeline di integrazione continua.

---

## Cosa servirebbe per dire qualcosa sui soldi

In ordine, e nessuno di questi passi è saltabile:

1. **Giorni veri di dati** (prezzi + flusso ordini) scaricati dal
   pannello. Senza, ogni misura di questa sessione resta su dati
   sintetici.
2. **Paper continuo** per giorni, guardando le decisioni rifiutate: dicono
   cosa manca alla strategia molto più di quelle approvate.
3. **Esploratore della frequenza e esperimenti** su quei dati veri, per
   scegliere impostazioni con i numeri davanti invece che a sensazione.
4. **Testnet autenticata**, con autorizzazione esplicita, per provare il
   percorso degli ordini veri (flusso account compreso).
5. **72 ore reali** di paper o testnet senza interruzioni.
6. Solo a quel punto ha senso parlare di denaro vero, e solo con una
   decisione separata.
