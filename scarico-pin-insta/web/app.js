/* Scarico - logica del sito. Nessuna libreria.

   Il punto delicato di tutta l'app e' il salvataggio in galleria.
   Su iOS nessuna pagina web puo' scrivere in Foto da sola: l'unica via e'
   il foglio di condivisione (navigator.share con un file), dove compare
   «Salva video» o «Salva immagine». Quindi la pagina scarica il file in
   memoria e poi lo passa al foglio. */
'use strict';

const CHIAVE_SALVATA = 'scarico.k';
const GUIDA_VISTA = 'scarico.guida';
const PROMEMORIA_VISTO = 'scarico.home';
const LIMITE_MEMORIA = 400 * 1024 * 1024; // oltre, il telefono rischia di non farcela

const $ = (sel) => document.querySelector(sel);

const el = {
  vistaChiave: $('#vista-chiave'),
  vistaApp: $('#vista-app'),
  form: $('#form-link'),
  campo: $('#campo-link'),
  btnPrincipale: $('#btn-principale'),
  stato: $('#stato'),
  risultati: $('#risultati'),
  guida: $('#guida'),
  btnChiudiGuida: $('#btn-chiudi-guida'),
  promemoria: $('#aggiungi-home'),
  btnChiudiPromemoria: $('#btn-chiudi-promemoria'),
  infoServer: $('#info-server'),
  spia: $('#spia'),
  toast: $('#toast'),
  manifesto: $('#manifesto'),
};

/* ----------------------------------------------------------- memoria locale */

function leggi(chiave, difetto = null) {
  try {
    const grezzo = localStorage.getItem(chiave);
    return grezzo === null ? difetto : grezzo;
  } catch (_) {
    return difetto;
  }
}
function scrivi(chiave, valore) {
  try { localStorage.setItem(chiave, valore); } catch (_) {}
}

/* -------------------------------------------------------- chiave d'accesso */

function trovaChiave() {
  const daUrl = new URLSearchParams(location.search).get('k');
  if (daUrl) {
    scrivi(CHIAVE_SALVATA, daUrl);
    return daUrl;
  }
  /* In Safari e nell'app aggiunta alla Home lo spazio di memoria e' separato,
     per questo la chiave deve restare dentro l'indirizzo: qui il salvataggio
     locale e' solo una rete di sicurezza. */
  return leggi(CHIAVE_SALVATA, '');
}

const chiave = trovaChiave();

/* Il manifest deve portarsi dietro la chiave, altrimenti l'icona sulla Home
   rischia di aprire l'indirizzo nudo e di non entrare piu'. */
if (chiave && el.manifesto) {
  el.manifesto.href = '/manifest.webmanifest?k=' + encodeURIComponent(chiave);
}

async function chiama(percorso, opzioni = {}) {
  const intestazioni = Object.assign({ 'Content-Type': 'application/json' }, opzioni.headers || {});
  if (chiave) intestazioni['X-Chiave'] = chiave;
  const risposta = await fetch(percorso, Object.assign({}, opzioni, { headers: intestazioni }));
  let dati = null;
  try { dati = await risposta.json(); } catch (_) {}
  if (!risposta.ok) {
    const errore = new Error((dati && dati.detail) || 'Errore ' + risposta.status + '.');
    errore.stato = risposta.status;
    throw errore;
  }
  return dati;
}

/* ------------------------------------------------------------------ aiuti */

function toast(testo, tipo = '') {
  el.toast.textContent = testo;
  el.toast.className = 'toast visibile' + (tipo ? ' ' + tipo : '');
  el.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    el.toast.classList.remove('visibile');
    setTimeout(() => { el.toast.hidden = true; }, 250);
  }, 3600);
}

function mostraStato(testo, classe = '', conFilatoio = false) {
  el.stato.className = 'stato' + (classe ? ' ' + classe : '');
  el.stato.innerHTML = '';
  if (conFilatoio) {
    const s = document.createElement('span');
    s.className = 'filatoio';
    el.stato.appendChild(s);
  }
  el.stato.appendChild(document.createTextNode(testo));
  el.stato.hidden = false;
}
function nascondiStato() { el.stato.hidden = true; }

function peso(byte) {
  if (!byte) return null;
  if (byte < 1048576) return Math.max(1, Math.round(byte / 1024)) + ' kB';
  const mb = byte / 1048576;
  if (mb >= 1000) return (mb / 1024).toFixed(1) + ' GB';
  return mb.toFixed(mb < 10 ? 1 : 0) + ' MB';
}
function durata(secondi) {
  if (!secondi) return null;
  return Math.floor(secondi / 60) + ':' + String(Math.round(secondi % 60)).padStart(2, '0');
}

/* ------------------------------------------------------------ avvio vista */

async function avvia() {
  if (!chiave) {
    el.vistaChiave.hidden = false;
    return;
  }
  try {
    const info = await chiama('/api/me');
    el.vistaApp.hidden = false;
    const pezzi = ['v' + info.versione];
    if (!info.ffmpeg) pezzi.push('senza ffmpeg');
    if (!info.cookie_instagram) pezzi.push('senza cookie Instagram');
    el.infoServer.textContent = pezzi.join(' · ');
    if (!leggi(GUIDA_VISTA)) el.guida.hidden = false;
    mostraPromemoriaHome();
    prendiLinkDallaUrl();
  } catch (errore) {
    if (errore.stato === 401) {
      el.vistaChiave.hidden = false;
      return;
    }
    el.vistaApp.hidden = false;
    toast(errore.message, 'no');
  }
}

function inApp() {
  return window.navigator.standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches;
}

function mostraPromemoriaHome() {
  if (inApp() || leggi(PROMEMORIA_VISTO)) return;
  const iOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  if (!iOS) return;
  el.promemoria.hidden = false;
}

el.btnChiudiGuida.addEventListener('click', () => {
  el.guida.hidden = true;
  scrivi(GUIDA_VISTA, '1');
});
el.btnChiudiPromemoria.addEventListener('click', () => {
  el.promemoria.hidden = true;
  scrivi(PROMEMORIA_VISTO, '1');
});

/* --------------------------------------------------- incolla, cerca, mostra */

el.campo.addEventListener('input', () => {
  const pieno = el.campo.value.trim().length > 0;
  el.btnPrincipale.querySelector('.etichetta-bottone').textContent = pieno ? 'Trova' : 'Incolla e trova';
});

/* Un bottone solo: se il campo e' vuoto legge gli appunti, se c'e' gia' un
   link cerca quello. Due bottoni che fanno la stessa cosa confondono. */
el.form.addEventListener('submit', async (evento) => {
  evento.preventDefault();
  if (el.campo.value.trim()) {
    cerca(el.campo.value.trim());
    return;
  }
  try {
    const testo = await navigator.clipboard.readText();
    if (!testo || !testo.trim()) {
      toast('Negli appunti non c’è niente.', 'no');
      el.campo.focus();
      return;
    }
    el.campo.value = testo.trim();
    cerca(testo.trim());
  } catch (_) {
    /* iOS chiede il permesso e si puo' dire di no: resta l'incolla a mano. */
    el.campo.focus();
    toast('Tieni premuto nel campo e scegli «Incolla».');
  }
});

async function cerca(testo) {
  el.btnPrincipale.disabled = true;
  el.risultati.innerHTML = '';
  memoria.clear();
  mostraStato('Sto cercando…', 'lavorando', true);
  try {
    const dati = await chiama('/api/resolve', { method: 'POST', body: JSON.stringify({ url: testo }) });
    const elementi = dati.elementi || [];
    if (!elementi.length) {
      mostraStato('Non ho trovato niente da scaricare.', 'guaio');
      return;
    }
    nascondiStato();
    elementi.forEach((elemento) => el.risultati.appendChild(creaScheda(elemento)));
    if (elementi.length > 1) {
      mostraStato('Questo post ha ' + elementi.length + ' elementi.', 'fatto');
    }
    el.risultati.firstElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (errore) {
    mostraStato(errore.message, errore.stato === 403 ? 'attenzione' : 'guaio');
  } finally {
    el.btnPrincipale.disabled = false;
  }
}

function creaScheda(elemento) {
  const scheda = document.createElement('article');
  scheda.className = 'risultato';

  const cornice = document.createElement('div');
  cornice.className = 'cornice';

  const anteprima = document.createElement('img');
  anteprima.alt = '';
  anteprima.decoding = 'async';
  if (elemento.url_thumb) anteprima.src = elemento.url_thumb;
  cornice.appendChild(anteprima);

  const sfumatura = document.createElement('div');
  sfumatura.className = 'sfumatura';
  cornice.appendChild(sfumatura);

  const bollo = document.createElement('span');
  bollo.className = 'bollo';
  bollo.textContent = (elemento.source === 'pinterest' ? 'Pinterest' : 'Instagram') +
    ' · ' + (elemento.kind === 'video' ? 'video' : 'foto');
  cornice.appendChild(bollo);

  const didascalia = document.createElement('div');
  didascalia.className = 'didascalia';
  const titolo = document.createElement('h3');
  titolo.textContent = elemento.title || 'Senza titolo';
  didascalia.appendChild(titolo);

  const pezzi = [];
  const d = durata(elemento.duration);
  if (d) pezzi.push(d);
  const p = peso(elemento.filesize);
  if (p) pezzi.push(p);
  if (elemento.height) pezzi.push(elemento.height + 'p');
  if (elemento.merge) pezzi.push('da comporre');
  if (pezzi.length) {
    const meta = document.createElement('p');
    meta.className = 'meta';
    pezzi.forEach((valore, indice) => {
      if (indice) {
        const punto = document.createElement('span');
        punto.className = 'punto';
        punto.textContent = '\u00b7';
        meta.appendChild(punto);
      }
      const pezzo = document.createElement('span');
      pezzo.textContent = valore;
      meta.appendChild(pezzo);
    });
    didascalia.appendChild(meta);
  }
  cornice.appendChild(didascalia);
  scheda.appendChild(cornice);

  const azioni = document.createElement('div');
  azioni.className = 'azioni';

  const salva = document.createElement('button');
  salva.className = 'bottone primario grande';
  salva.type = 'button';
  const riempimento = document.createElement('span');
  riempimento.className = 'riempimento';
  const etichetta = document.createElement('span');
  etichetta.className = 'etichetta-bottone';
  etichetta.textContent = 'Salva in galleria';
  salva.appendChild(riempimento);
  salva.appendChild(etichetta);
  salva.addEventListener('click', () => salvaInGalleria(elemento, salva));
  azioni.appendChild(salva);

  const neiFile = document.createElement('a');
  neiFile.className = 'bottone secondario';
  neiFile.href = elemento.url_file;
  neiFile.setAttribute('download', elemento.filename || '');
  neiFile.textContent = 'Salva nei File';
  neiFile.addEventListener('click', () => toast('Lo trovi nei Download di Safari.'));
  azioni.appendChild(neiFile);

  scheda.appendChild(azioni);
  return scheda;
}

/* ------------------------------------------------- salvataggio in galleria */

const memoria = new Map(); // id -> File gia' scaricato

function testo(bottone, valore) {
  bottone.querySelector('.etichetta-bottone').textContent = valore;
}
function avanzamento(bottone, frazione) {
  bottone.querySelector('.riempimento').style.width = Math.round(frazione * 100) + '%';
}

async function scaricaInMemoria(elemento, bottone) {
  const risposta = await fetch(elemento.url_file + '&apri=1', {
    headers: chiave ? { 'X-Chiave': chiave } : {},
  });
  if (!risposta.ok) {
    let dettaglio = 'Errore ' + risposta.status + '.';
    try { dettaglio = (await risposta.json()).detail || dettaglio; } catch (_) {}
    throw new Error(dettaglio);
  }

  const totale = Number(risposta.headers.get('content-length')) || 0;
  if (totale > LIMITE_MEMORIA) throw new Error('Questo file e’ troppo grande per la galleria: usa «Salva nei File».');

  let blob;
  if (risposta.body && risposta.body.getReader) {
    const lettore = risposta.body.getReader();
    const pezzi = [];
    let presi = 0;
    for (;;) {
      const { done, value } = await lettore.read();
      if (done) break;
      pezzi.push(value);
      presi += value.length;
      if (totale) avanzamento(bottone, presi / totale);
      testo(bottone, totale ? 'Scarico ' + Math.round((presi / totale) * 100) + '%' : 'Scarico…');
    }
    blob = new Blob(pezzi, { type: elemento.mime });
  } else {
    blob = await risposta.blob();
  }
  avanzamento(bottone, 1);
  return new File([blob], elemento.filename, { type: elemento.mime });
}

function scaricaNeiFile(elemento) {
  const ancora = document.createElement('a');
  ancora.href = elemento.url_file;
  ancora.download = elemento.filename || '';
  document.body.appendChild(ancora);
  ancora.click();
  ancora.remove();
}

async function salvaInGalleria(elemento, bottone) {
  if (bottone.dataset.occupato === '1') return;
  bottone.dataset.occupato = '1';
  const etichettaIniziale = bottone.querySelector('.etichetta-bottone').textContent;
  try {
    let file = memoria.get(elemento.id);
    if (!file) {
      testo(bottone, 'Preparo…');
      file = await scaricaInMemoria(elemento, bottone);
      memoria.set(elemento.id, file);
    }

    if (!(navigator.canShare && navigator.canShare({ files: [file] }))) {
      /* Browser senza condivisione di file (o desktop): meglio il download
         normale che un messaggio di errore. */
      scaricaNeiFile(elemento);
      testo(bottone, 'Scaricato');
      toast('Questo browser non sa passare i file alla galleria: l’ho scaricato.', '');
      return;
    }

    testo(bottone, 'Apro…');
    await navigator.share({ files: [file] });
    bottone.classList.remove('pronto');
    testo(bottone, 'Salvato');
    avanzamento(bottone, 0);
    toast(elemento.kind === 'video' ? 'Scegli «Salva video»' : 'Scegli «Salva immagine»', 'ok');
  } catch (errore) {
    avanzamento(bottone, 0);
    if (errore.name === 'AbortError') {
      testo(bottone, etichettaIniziale);
      return;
    }
    if (errore.name === 'NotAllowedError') {
      /* iOS pretende che il foglio parta da un tocco: dopo un download lungo
         l'autorizzazione scade. Il file pero' e' gia' in memoria, quindi il
         prossimo tocco lo apre all'istante. */
      bottone.classList.add('pronto');
      testo(bottone, 'Tocca per salvare');
      return;
    }
    testo(bottone, 'Riprova');
    toast(errore.message, 'no');
  } finally {
    bottone.dataset.occupato = '0';
  }
}

/* ------------------------------------------ link passato nell'indirizzo */

function prendiLinkDallaUrl() {
  const link = new URLSearchParams(location.search).get('u');
  if (!link) return;
  el.campo.value = link;
  /* Tolgo solo "u" e tengo "k": senza la chiave il prossimo avvio non entra. */
  const parametri = new URLSearchParams(location.search);
  parametri.delete('u');
  const coda = parametri.toString();
  history.replaceState(null, '', location.pathname + (coda ? '?' + coda : ''));
  cerca(link);
}

/* ------------------------------------------------------------------ avvio */

avvia();

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}
