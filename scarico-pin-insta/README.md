# Scarico Pin e Insta

Sito privato, pensato per l'iPhone: incolli il link di un pin di Pinterest o di un
reel/post di Instagram, premi un bottone e il video finisce nella cartella
**scarico pin e insta** dentro l'app File.

![icona](web/icons/icon-192.png)

---

## 1. La cosa importante da sapere prima di iniziare

**Un sito fatto solo di pagine (per esempio GitHub Pages) non può fare questo lavoro.**
Il browser dell'iPhone non è autorizzato a leggere le pagine di Pinterest e Instagram
per conto tuo: glielo impedisce la regola CORS, e i link veri dei video non sono
scritti nella pagina ma vanno ricavati. Serve quindi un piccolo programma che gira
**su un computer tuo** e fa quel lavoro: è la cartella `server/` di questo progetto.

Quindi il risultato finale è:

```
 iPhone  ──►  questo sito (che gira sul tuo PC o su un Raspberry)  ──►  Pinterest / Instagram
   ▲                                      │
   └──────────── il file torna indietro ──┘  e Safari lo salva in «scarico pin e insta»
```

Il computer che fa girare il sito deve essere acceso nel momento in cui scarichi.
Niente di tutto questo passa da servizi di terzi: il link, il video e la password
restano tra il tuo telefono e il tuo computer.

**Sulla cartella di destinazione:** nessun sito web, nemmeno questo, può scrivere
da solo dentro l'app File dell'iPhone — iOS non lo permette a nessuna pagina web.
Quello che si fa, ed è il motivo per cui funziona, è dire **una volta sola** a Safari
che tutti i download vanno in quella cartella (punto 5). Da quel momento ogni file
che scarichi da qui ci finisce dentro senza che tu debba scegliere niente.
In alternativa c'è la scorciatoia del punto 7, che scrive direttamente nella cartella.

---

## 2. Cosa ti serve

| | |
|---|---|
| Un computer sempre acceso (o acceso quando ti serve) | Windows, Mac, Linux o Raspberry Pi |
| Python 3.10 o più recente | [python.org/downloads](https://www.python.org/downloads/) — su Windows spunta «Add Python to PATH» |
| ffmpeg | serve per i video che hanno audio e video separati (storie, alcuni reel) |
| Un iPhone con iOS 16 o più recente | per la scelta della cartella dei download |

Installare ffmpeg:

```bash
winget install Gyan.FFmpeg      # Windows
brew install ffmpeg             # macOS
sudo apt install ffmpeg         # Debian, Ubuntu, Raspberry Pi OS
```

---

## 3. Avvio, la prima volta

Scarica questa cartella sul computer, poi:

**Windows** — doppio clic su `avvia.bat`.
**macOS e Linux** — da terminale:

```bash
cd scarico-pin-insta
./avvia.sh
```

Al primo avvio lo script crea il file `.env` e si ferma. Aprilo con un editor di
testo e scrivi la tua password:

```
APP_PASSWORD=una-password-lunga-che-ricordi
```

La `SECRET_KEY` te l'ha già generata lo script: non toccarla. Rilancia `avvia.bat`
(o `./avvia.sh`): ora il sito è acceso su `http://localhost:8080`.

<details>
<summary><strong>In alternativa: con Docker</strong></summary>

```bash
cp .env.example .env            # poi scrivi APP_PASSWORD e SECRET_KEY dentro .env
docker compose up -d --build
```

ffmpeg è già dentro l'immagine. Per vedere cosa succede: `docker compose logs -f`.
</details>

---

## 4. Raggiungerlo dall'iPhone

### Consigliato: Tailscale

[Tailscale](https://tailscale.com/) è gratuito per uso personale e collega il
telefono al computer come se fossero sulla stessa rete, anche quando sei fuori casa.
Installalo sul computer e sull'iPhone con lo stesso account, poi sul computer:

```bash
tailscale serve --bg 8080
```

Ti risponde con un indirizzo tipo `https://ilmiopc.tua-rete.ts.net`. **Usa quello.**
È in `https`, e questo conta: con `https` funzionano anche il bottone «Incolla» e
l'icona sulla schermata Home come app vera e propria.

### Più semplice ma solo in casa: l'indirizzo di rete locale

Trova l'indirizzo del computer (`ipconfig` su Windows, `ifconfig | grep inet` su
Mac/Linux): sarà qualcosa come `192.168.1.50`. Dall'iPhone apri
`http://192.168.1.50:8080`.

Funziona, ma essendo `http` e non `https` iOS tratta il sito come non sicuro:
il bottone «Incolla» non potrà leggere gli appunti (incollerai a mano, tenendo
premuto nel campo) e il sito non resterà disponibile offline. I download invece
funzionano normalmente.

---

## 5. Far finire i file in «scarico pin e insta»

Da fare **una volta sola**, sull'iPhone:

1. Apri l'app **File** → **Su iPhone** (oppure **iCloud Drive**) → in alto a destra
   il simbolo della cartella con il `+` → chiamala esattamente
   **`scarico pin e insta`**.
2. **Impostazioni** → **App** → **Safari** → **Download** → **Altro…** → scegli
   quella cartella. (Su iOS 17 e precedenti: Impostazioni → Safari → Download.)
3. Fine. Ogni file scaricato da questo sito, da ora, finisce lì dentro.

> Se preferisci che i video vadano nel rullino invece che nei File, scaricali
> normalmente e poi dall'app File fai Condividi → Salva video. Oppure usa la
> scorciatoia del punto 7, che può fare le due cose insieme.

---

## 6. L'icona sulla schermata Home

Apri il sito **in Safari** (non in Chrome), premi **Condividi** → **Aggiungi a Home**.
Comparirà l'icona viola con la freccia. Da lì il sito si apre a schermo intero,
senza barra degli indirizzi.

Se aprendolo dall'icona il download non parte, usa il bottone **Apri in Safari**
che trovi accanto a ogni video: certe versioni di iOS permettono i download solo
dentro Safari vero e proprio.

---

## 7. Scorciatoia iOS: scaricare dal foglio di condivisione

Più comoda di tutte: stai guardando un reel nell'app Instagram, premi Condividi,
scegli la scorciatoia, e il video finisce nella cartella senza aprire nessun sito.

Nel sito apri **«Come farlo salvare in scarico pin e insta»** e copia il
**token per l'app Scorciatoie**. Poi, nell'app **Comandi rapidi** (Scorciatoie),
crea una scorciatoia nuova con questi passaggi:

1. **Ricevi** `URL` **da** `Foglio di condivisione`
2. **Testo** →
   `https://IL-TUO-INDIRIZZO/api/shortcut?k=IL-TUO-TOKEN&u=`
   seguito, sulla stessa riga, dalla variabile **Input scorciatoia** —
   usa l'azione *Codifica URL* sull'input prima di accodarlo
3. **Ottieni contenuto di** `Testo` → risponde un JSON
4. **Ottieni valore dizionario** `download` dal risultato
5. **Ottieni contenuto di** (il valore appena ottenuto) → è il video
6. **Salva file** → disattiva «Chiedi dove salvare» → scegli la cartella
   **scarico pin e insta**

Poi, nelle impostazioni della scorciatoia, attiva **Mostra nel foglio di condivisione**.

> Il token della scorciatoia vale finché non cambi `SECRET_KEY`. Chi ha quel token
> può far scaricare video dal tuo server, quindi non metterlo in giro.

---

## 8. Instagram: quando serve il login

Pinterest funziona quasi sempre senza nulla. Instagram, invece, da tempo chiede di
essere loggati per una parte dei contenuti: se il sito ti risponde
*«Instagram chiede di essere loggati»*, servono i tuoi cookie.

1. Sul computer, con Firefox o Chrome, installa un'estensione che esporta i cookie
   in formato Netscape (`cookies.txt`).
2. Vai su instagram.com da loggato ed esporta i cookie di quel sito.
3. Salva il file come `cookies.txt` **dentro questa cartella** e nel file `.env` scrivi:
   `COOKIES_FILE=cookies.txt`
4. Riavvia il sito.

`cookies.txt` è già elencato in `.gitignore`: non finisce su GitHub. Tienilo come
tieni una password, perché di fatto lo è: chi lo legge entra nel tuo account.
Se Instagram ti fa uscire, rifai l'esportazione.

---

## 9. Se qualcosa non va

| Cosa vedi | Cosa è successo |
|---|---|
| «Password sbagliata» | È la `APP_PASSWORD` del file `.env`. Se l'hai cambiata, riavvia il server. |
| «Sessione scaduta» a ogni apertura | Non hai messo `SECRET_KEY` in `.env`: ne viene generata una nuova a ogni riavvio. |
| «Questo link non è di Pinterest o Instagram» | Hai incollato altro, o il link è di un sito accorciato diverso da `pin.it`. |
| «Non ho trovato nessun video» | Il pin è una foto (viene scaricata l'immagine), oppure il post è stato rimosso. |
| «Instagram chiede di essere loggati» | Vedi il punto 8. |
| «serve ffmpeg sul server» | Installa ffmpeg e riavvia. |
| Il download non parte dall'icona della Home | Usa **Apri in Safari** (punto 6). |
| «Questo download non è più in memoria» | Il server è stato riavviato dopo che avevi cercato il video: incolla di nuovo il link. |
| Il bottone «Incolla» non legge gli appunti | Stai usando `http` e non `https`: vedi il punto 4. |
| Tutto lento la prima volta su un reel lungo | Audio e video erano separati: il server li sta unendo con ffmpeg prima di mandarteli. |

Per capire cosa sta succedendo guarda la finestra in cui gira il server: ogni
richiesta scrive una riga.

---

## 10. Com'è fatto

```
scarico-pin-insta/
├── server/
│   ├── main.py        rotte web e consegna dei file
│   ├── extractor.py   yt-dlp: da un link ai file scaricabili
│   ├── urls.py        quali link si accettano + difesa contro le richieste interne
│   ├── auth.py        password e token firmati
│   ├── config.py      lettura del file .env
│   └── store.py       memoria temporanea dei link risolti
├── web/               il sito: una pagina, un css, un js, nessuna libreria
├── tests/             59 test, girano in un secondo
├── tools/make_icons.py genera le icone PNG
├── avvia.bat / avvia.sh
└── Dockerfile, docker-compose.yml
```

Il giro completo di una richiesta:

1. il telefono manda il link a `POST /api/resolve`;
2. il server controlla che sia Pinterest o Instagram e lo passa a yt-dlp;
3. torna la lista degli elementi trovati (un post può averne più d'uno), ognuno con
   un **biglietto firmato** che vale sei ore;
4. premendo «Scarica» il telefono apre `GET /api/file/<id>?t=<biglietto>`;
5. il server scarica dal CDN e rigira i byte al telefono mentre arrivano, con
   l'intestazione che dice a Safari «questo è un file da salvare».

L'indirizzo vero del CDN non arriva mai al telefono, e le miniature passano anch'esse
dal server: Pinterest e Instagram vedono solo il tuo computer.

Far girare i test:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
```

---

## 11. Sicurezza

Quello che il sito fa per proteggersi, visto che prima o poi sarà raggiungibile da
fuori casa:

- **Password obbligatoria.** Senza `APP_PASSWORD` il server si rifiuta di partire,
  invece di restare aperto a chiunque conosca l'indirizzo.
- **Freno ai tentativi.** Dopo 8 password sbagliate quell'indirizzo IP resta fuori
  per un quarto d'ora.
- **Solo Pinterest e Instagram.** Il controllo guarda il dominio registrato, non
  l'inizio del nome: `pinterest.qualcosaltro.com` viene rifiutato. Senza questo
  filtro il sito diventerebbe un proxy con cui scaricare qualsiasi cosa.
- **Niente richieste verso la rete interna.** Prima di aprire un file il server
  verifica che l'indirizzo non sia `127.0.0.1`, `192.168.x.x` o simili.
- **Biglietti separati.** Il token della sessione non apre i file e il biglietto di
  un file non ne apre un altro; i biglietti scadono dopo sei ore.
- **Link del CDN mai esposti.** Non escono dal server, nemmeno nelle miniature.

Quello che il sito **non** fa: non cifra nulla da solo (usa Tailscale o un reverse
proxy con HTTPS), e non è pensato per più di una persona.

---

## 12. Due righe oneste sull'uso

Lo strumento scarica contenuti che sono comunque già visibili dal tuo account. I
video restano di chi li ha fatti: tienili per te, e se li ripubblichi chiedi il
permesso e cita l'autore. Scaricare in massa i contenuti altrui va oltre lo scopo
di questo progetto ed è contro le condizioni d'uso dei due siti.
