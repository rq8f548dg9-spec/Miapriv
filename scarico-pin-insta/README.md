# Scarico

Sito privato per iPhone: incolli il link di un pin o di un reel, tocchi un bottone,
il video o la foto finisce **nella tua galleria**.

Nessuna password da digitare. Si apre in Safari, si aggiunge alla schermata Home
e da lì si comporta come un'app.

---

## Com'è fatto il giro

```
 iPhone  ──►  il tuo sito  ──►  Pinterest / Instagram
   ▲               │
   └── il file ◄───┘   →  foglio di condivisione  →  Salva video  →  Galleria
```

Due cose da sapere, perché spiegano tutto il resto:

**Serve un server.** Il browser dell'iPhone non è autorizzato a leggere le pagine
di Pinterest e Instagram per conto tuo, e il link vero del video non è scritto
nella pagina. Deve farlo un programma, che è la cartella `server/` di questo
progetto. Un sito di sole pagine (GitHub Pages, Lovable, Vercel statico) non può
fare questo lavoro: si aprirebbe benissimo e non scaricherebbe niente.

**L'ultimo tocco è tuo.** Su iOS nessuna pagina web può scrivere dentro Foto da
sola: non è un limite di questo sito, è iOS. L'unica via è il foglio di
condivisione. Quindi il sito scarica il file, apre il foglio, e tu tocchi
**Salva video**. Un tocco, e sta in galleria. Funziona così anche nei siti che
fanno questo mestiere.

---

## 1. Metterlo online, dal telefono

Serve un account gratuito su [huggingface.co](https://huggingface.co) (niente carta
di credito).

**a) Crea lo Space**

1. huggingface.co → **New** → **Space**
2. Nome: `scarico` · SDK: **Docker** (template *Blank*) · Visibilità: **Public**
3. **Create Space**

**b) Mettici dentro i quattro file**

Sono nella cartella `hf-space/` di questo progetto. Nello Space:
**Files** → **＋ Add file** → **Create a new file**, e per ognuno incolla il
contenuto e premi **Commit**:

| File | Cos'è |
|---|---|
| `requirements.txt` | quattro righe |
| `Dockerfile` | come si costruisce |
| `README.md` | **sostituisci** quello che HF ha già creato |
| `app.py` | tutto il sito in un file solo (~80 kB) |

> `app.py` è lungo: aprilo su GitHub, tocca **Raw**, seleziona tutto, copia,
> incolla. Se hai un computer a portata di mano fai prima a clonare lo Space e
> copiarci i quattro file.

**c) La chiave**

**Settings** → **Variables and secrets** → **New secret**

- Nome: `ACCESS_KEY`
- Valore: una frase lunga e tua, almeno 16 caratteri, senza spazi.
  Per esempio `fragola-cemento-42-nuvola-blu`

Lo Space si ricostruisce da solo. Quando la spia in alto diventa **Running**, ci sei.

---

## 2. Il tuo link, una volta sola

```
https://IL-TUO-NOME-scarico.hf.space/?k=LA-TUA-CHIAVE
```

Aprilo **in Safari** (l'indirizzo esatto lo trovi nello Space, menu ⋮ →
*Embed this Space* → *Direct URL*).

Poi: **Condividi** → **Aggiungi a Home**.

L'icona si ricorda la chiave per sempre. Da quel momento non devi più digitare
niente: tocchi l'icona e sei dentro. Se apri l'indirizzo senza la parte `?k=…`,
il sito ti dice che manca la chiave — è voluto: è quello che tiene fuori gli altri.

---

## 3. Usarlo

1. Su Pinterest o Instagram: **Condividi → Copia link**
2. Apri **Scarico** dalla schermata Home
3. **Incolla e trova**
4. **Salva in galleria** → nel foglio che si apre, **Salva video** (o *Salva immagine*)

Se il post è un carosello con più foto o video, escono tutte in fila e le salvi
una per una.

C'è anche **Salva nei File** se preferisci il file al posto della galleria.

---

## 4. Instagram: quando serve il login

Pinterest funziona quasi sempre senza niente. Instagram no: da un server in cloud
mostra poco agli sconosciuti, e per molti post chiede di essere loggati. Se vedi
*«Instagram chiede di essere loggati»*, servono i tuoi cookie:

1. Da un computer, con un'estensione che esporta i cookie in formato Netscape,
   esporta quelli di `instagram.com` mentre sei loggato.
2. Nello Space: **Settings** → **Variables and secrets** → **New secret**
   - Nome: `IG_COOKIES`
   - Valore: tutto il contenuto del file, così com'è.

Tienilo come tieni una password, perché di fatto lo è: chi lo legge entra nel tuo
account. Non va mai dentro `app.py` né in un file dello Space — solo nei secret,
che restano privati anche se lo Space è pubblico.

---

## 5. Se qualcosa non va

| Cosa vedi | Cosa è successo |
|---|---|
| «Serve il tuo link personale» | Hai aperto l'indirizzo senza `?k=…`. Usa l'icona sulla Home. |
| La prima ricerca ci mette mezzo minuto | Lo Space gratuito si addormenta se non lo usi per giorni. Si risveglia da solo. |
| «Instagram chiede di essere loggati» | Punto 4. |
| «Non ho trovato niente da scaricare» | Il post è stato rimosso, o è privato. |
| «Questo link non è di Pinterest o Instagram» | Hai incollato altro. Serve il link del post. |
| «Tocca per salvare» sul bottone | iOS ha chiuso il permesso durante il download. Il file è già pronto: tocca di nuovo e si apre subito. |
| Il foglio non mostra «Salva video» | Stai usando Chrome o un browser che non condivide file. Apri il sito in Safari. |
| «Il server si è riavviato» | Lo Space è ripartito. Incolla di nuovo il link. |

Per capire cosa sta succedendo: nello Space, scheda **Logs**.

---

## 6. Com'è fatto

```
scarico-pin-insta/
├── server/            il programma vero
│   ├── main.py        rotte web, chiave d'accesso, consegna dei file
│   ├── extractor.py   yt-dlp: da un link ai file scaricabili
│   ├── urls.py        quali link si accettano + difesa contro le richieste interne
│   ├── auth.py        biglietti firmati
│   ├── config.py      le impostazioni
│   └── store.py       memoria temporanea dei link risolti
├── web/               il sito: una pagina, un css, un js, nessuna libreria
├── hf-space/          GENERATO: la stessa cosa in un file solo, per Hugging Face
├── tools/build_hf.py  genera hf-space/
├── tools/make_icons.py disegna le icone
└── tests/             92 test
```

**Il bundle è generato.** Si modifica `server/` e `web/`, poi si rigenera:

```bash
python3 tools/build_hf.py
python3 -m pytest tests/ -q      # un test fallisce apposta se te ne dimentichi
```

**Farlo girare altrove.** Nella cartella ci sono anche `Dockerfile`,
`docker-compose.yml` e `avvia.sh`: servono per tenerlo su un computer tuo, un
Raspberry o un altro host. Lì Instagram funziona meglio, perché le richieste non
partono da un indirizzo di datacenter.

Cosa succede quando tocchi *Salva in galleria*: la pagina chiede il file al
server, lo legge in memoria mostrando la percentuale sul bottone, ne fa un
`File` con il tipo giusto (`video/mp4`, `image/jpeg` — se il tipo è sbagliato
iOS non offre «Salva video»), e lo passa al foglio di condivisione. Se iOS nel
frattempo ha ritirato il permesso, il bottone diventa *Tocca per salvare*: il
file è già in memoria, il secondo tocco apre il foglio all'istante senza
riscaricare niente.

---

## 7. Sicurezza

- **La chiave è l'unica porta.** Sta nel link, non la digiti mai. Senza
  `ACCESS_KEY` il server non parte proprio, invece di restare aperto a chiunque.
- **Freno ai tentativi:** dopo 20 chiavi sbagliate quell'indirizzo IP resta fuori
  un quarto d'ora.
- **Solo Pinterest e Instagram.** Il controllo guarda il dominio registrato, non
  l'inizio del nome: `pinterest.qualcosaltro.com` viene rifiutato. Senza questo
  filtro il sito sarebbe un proxy con cui scaricare qualunque cosa.
- **Niente richieste verso la rete interna:** prima di aprire un file il server
  verifica che l'indirizzo non sia `127.0.0.1`, `192.168.x.x` o simili.
- **Biglietti separati:** la chiave d'accesso non apre i file, e il biglietto di
  un file non ne apre un altro. Scadono dopo sei ore.
- **Gli indirizzi dei CDN non arrivano mai al telefono**, miniature comprese.

---

## 8. Due righe oneste

Scarichi roba che il tuo account già vede. I video restano di chi li ha fatti:
tienili per te, e se li ripubblichi chiedi il permesso e cita l'autore. Scaricare
in massa i contenuti altrui va oltre lo scopo di questo progetto ed è contro le
condizioni d'uso dei due siti.
