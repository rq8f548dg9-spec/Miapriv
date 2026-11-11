/* Scarico Pin e Insta - logica del sito. Nessuna libreria esterna. */
'use strict';

const CHIAVE_TOKEN = 'spi.token';
const CHIAVE_STORICO = 'spi.storico';
const MAX_STORICO = 20;

const $ = (sel) => document.querySelector(sel);

const el = {
  vistaLogin: $('#vista-login'),
  vistaApp: $('#vista-app'),
  formLogin: $('#form-login'),
  password: $('#password'),
  btnLogin: $('#btn-login'),
  erroreLogin: $('#errore-login'),
  btnEsci: $('#btn-esci'),
  formLink: $('#form-link'),
  campoLink: $('#campo-link'),
  btnIncolla: $('#btn-incolla'),
  btnTrova: $('#btn-trova'),
  stato: $('#stato'),
  risultati: $('#risultati'),
  tokenScorciatoia: $('#token-scorciatoia'),
  btnCopiaToken: $('#btn-copia-token'),
  sezioneStorico: $('#sezione-storico'),
  storico: $('#storico'),
  btnPulisciStorico: $('#btn-pulisci-storico'),
  infoServer: $('#info-server'),
  dettagliIos: $('#dettagli-ios'),
};

/* ----------------------------------------------------------- memoria locale */
/* Il localStorage puo' mancare (navigazione privata, dati del sito bloccati):
   ogni accesso e' protetto, e il sito funziona comunque senza. */

function leggi(chiave, difetto = null) {
  try {
    const grezzo = localStorage.getItem(chiave);
    return grezzo === null ? difetto : JSON.parse(grezzo);
  } catch (_) {
    return difetto;
  }
}

function scrivi(chiave, valore) {
  try {
    localStorage.setItem(chiave, JSON.stringify(valore));
  } catch (_) {
    /* pazienza: si perde solo la comodita' */
  }
}

function cancella(chiave) {
  try {
    localStorage.removeItem(chiave);
  } catch (_) {}
}

let token = leggi(CHIAVE_TOKEN);

/* ------------------------------------------------------------------- utilita' */

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

function nascondiStato() {
  el.stato.hidden = true;
}

function pesoLeggibile(byte) {
  if (!byte) return null;
  const mb = byte / 1048576;
  return mb >= 1000 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(mb < 10 ? 1 : 0) + ' MB';
}

function durataLeggibile(secondi) {
  if (!secondi) return null;
  const m = Math.floor(secondi / 60);
  const s = Math.round(secondi % 60);
  return m + ':' + String(s).padStart(2, '0');
}

async function chiama(percorso, opzioni = {}) {
  const intestazioni = Object.assign({ 'Content-Type': 'application/json' }, opzioni.headers || {});
  if (token) intestazioni['X-Token'] = token;
  const risposta = await fetch(percorso, Object.assign({}, opzioni, { headers: intestazioni }));
  if (risposta.status === 401) {
    esci(true);
    throw new Error('Sessione scaduta. Rifai il login.');
  }
  let dati = null;
  try {
    dati = await risposta.json();
  } catch (_) {}
  if (!risposta.ok) {
    throw new Error((dati && dati.detail) || 'Errore ' + risposta.status + '.');
  }
  return dati;
}

/* ----------------------------------------------------------------- viste */

function mostraLogin() {
  el.vistaApp.hidden = true;
  el.vistaLogin.hidden = false;
  el.password.focus();
}

function mostraApp() {
  el.vistaLogin.hidden = true;
  el.vistaApp.hidden = false;
  disegnaStorico();
  caricaInfo();
  prendiLinkDallaUrl();
}

function esci(silenzioso = false) {
  token = null;
  cancella(CHIAVE_TOKEN);
  el.risultati.innerHTML = '';
  nascondiStato();
  mostraLogin();
  if (!silenzioso) return;
  el.erroreLogin.textContent = 'Sessione scaduta: rifai il login.';
  el.erroreLogin.hidden = false;
}

/* ----------------------------------------------------------------- login */

el.formLogin.addEventListener('submit', async (evento) => {
  evento.preventDefault();
  el.erroreLogin.hidden = true;
  el.btnLogin.disabled = true;
  el.btnLogin.textContent = 'Un attimo…';
  try {
    const dati = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: el.password.value }),
    }).then(async (r) => {
      const corpo = await r.json().catch(() => null);
      if (!r.ok) throw new Error((corpo && corpo.detail) || 'Errore ' + r.status + '.');
      return corpo;
    });
    token = dati.token;
    scrivi(CHIAVE_TOKEN, token);
    el.password.value = '';
    mostraApp();
  } catch (errore) {
    el.erroreLogin.textContent = errore.message;
    el.erroreLogin.hidden = false;
  } finally {
    el.btnLogin.disabled = false;
    el.btnLogin.textContent = 'Entra';
  }
});

el.btnEsci.addEventListener('click', () => esci());

/* ------------------------------------------------------- info dal server */

async function caricaInfo() {
  try {
    const info = await chiama('/api/me');
    el.tokenScorciatoia.textContent = info.token_scorciatoia;
    const pezzi = ['v' + info.versione];
    if (!info.ffmpeg) pezzi.push('senza ffmpeg');
    pezzi.push(info.cookie_instagram ? 'cookie Instagram attivi' : 'senza cookie Instagram');
    if (info.limite_mb) pezzi.push('max ' + info.limite_mb + ' MB');
    el.infoServer.textContent = pezzi.join(' · ');
  } catch (_) {
    el.infoServer.textContent = '';
  }
}

el.btnCopiaToken.addEventListener('click', async () => {
  const testo = el.tokenScorciatoia.textContent || '';
  try {
    await navigator.clipboard.writeText(testo);
    el.btnCopiaToken.textContent = 'Copiato';
  } catch (_) {
    el.btnCopiaToken.textContent = 'Copia a mano';
  }
  setTimeout(() => {
    el.btnCopiaToken.textContent = 'Copia';
  }, 1800);
});

/* ------------------------------------------------------------ incolla link */

el.btnIncolla.addEventListener('click', async () => {
  try {
    const testo = await navigator.clipboard.readText();
    if (testo && testo.trim()) {
      el.campoLink.value = testo.trim();
      el.formLink.requestSubmit();
      return;
    }
    mostraStato('Gli appunti sono vuoti.', 'guaio');
  } catch (_) {
    /* Su iOS la lettura degli appunti chiede un permesso che l'utente puo'
       negare: in quel caso resta l'incolla a mano. */
    el.campoLink.focus();
    mostraStato('iOS non mi lascia leggere gli appunti: tieni premuto nel campo e scegli «Incolla».', 'guaio');
  }
});

/* ------------------------------------------------------------- risoluzione */

el.formLink.addEventListener('submit', async (evento) => {
  evento.preventDefault();
  const grezzo = el.campoLink.value.trim();
  if (!grezzo) {
    mostraStato('Incolla prima un link.', 'guaio');
    return;
  }
  el.btnTrova.disabled = true;
  el.risultati.innerHTML = '';
  mostraStato('Sto cercando il video…', 'lavorando', true);
  try {
    const dati = await chiama('/api/resolve', { method: 'POST', body: JSON.stringify({ url: grezzo }) });
    const elementi = dati.elementi || [];
    if (!elementi.length) {
      mostraStato('Non ho trovato nulla di scaricabile.', 'guaio');
      return;
    }
    nascondiStato();
    elementi.forEach((elemento) => el.risultati.appendChild(creaScheda(elemento)));
    if (elementi.length > 1) {
      mostraStato('Questo post contiene ' + elementi.length + ' elementi: scaricali uno per uno.', 'fatto');
    }
  } catch (errore) {
    mostraStato(errore.message, 'guaio');
  } finally {
    el.btnTrova.disabled = false;
  }
});

function creaScheda(elemento) {
  const scheda = document.createElement('article');
  scheda.className = 'risultato';

  const anteprima = document.createElement('img');
  anteprima.className = 'anteprima';
  anteprima.alt = '';
  anteprima.loading = 'lazy';
  anteprima.decoding = 'async';
  if (elemento.url_thumb) anteprima.src = elemento.url_thumb;
  scheda.appendChild(anteprima);

  const colonna = document.createElement('div');

  const titolo = document.createElement('h3');
  titolo.textContent = elemento.title || 'senza titolo';
  colonna.appendChild(titolo);

  const pezzi = [elemento.source === 'pinterest' ? 'Pinterest' : 'Instagram', elemento.kind];
  const durata = durataLeggibile(elemento.duration);
  if (durata) pezzi.push(durata);
  const peso = pesoLeggibile(elemento.filesize);
  if (peso) pezzi.push(peso);
  if (elemento.height) pezzi.push(elemento.height + 'p');
  if (elemento.merge) pezzi.push('da ricomporre');
  const meta = document.createElement('p');
  meta.className = 'meta';
  meta.innerHTML = pezzi.map((p) => '<span>' + p + '</span>').join('<span class="pallino">•</span>');
  colonna.appendChild(meta);

  const azioni = document.createElement('div');
  azioni.className = 'azioni';

  const scarica = document.createElement('button');
  scarica.className = 'bottone primario';
  scarica.type = 'button';
  scarica.textContent = 'Scarica';
  scarica.addEventListener('click', () => avviaDownload(elemento, scarica));
  azioni.appendChild(scarica);

  const inSafari = document.createElement('a');
  inSafari.className = 'bottone secondario';
  inSafari.href = elemento.url_file;
  inSafari.target = '_blank';
  inSafari.rel = 'noopener';
  inSafari.textContent = 'Apri in Safari';
  azioni.appendChild(inSafari);

  colonna.appendChild(azioni);
  scheda.appendChild(colonna);
  return scheda;
}

function avviaDownload(elemento, bottone) {
  /* L'ancora con l'attributo download e' il solo modo in cui iOS mostra la
     richiesta «Scarica» e mette il file nella cartella scelta in Impostazioni.
     Il link e' della stessa origine, quindi l'attributo viene rispettato. */
  const ancora = document.createElement('a');
  ancora.href = elemento.url_file;
  ancora.download = elemento.filename || '';
  ancora.rel = 'noopener';
  document.body.appendChild(ancora);
  ancora.click();
  ancora.remove();

  bottone.textContent = elemento.merge ? 'Sto componendo…' : 'Avviato';
  setTimeout(() => {
    bottone.textContent = 'Scarica di nuovo';
  }, 4000);

  mostraStato(
    elemento.merge
      ? 'Il server sta unendo audio e video: può richiedere qualche secondo prima che il download parta.'
      : 'Download avviato. Il file va nella cartella impostata in Safari › Download.',
    'fatto'
  );
  registraNelloStorico(elemento);
}

/* ----------------------------------------------------------------- storico */

function registraNelloStorico(elemento) {
  const voci = leggi(CHIAVE_STORICO, []) || [];
  voci.unshift({ nome: elemento.filename, quando: Date.now(), fonte: elemento.source });
  scrivi(CHIAVE_STORICO, voci.slice(0, MAX_STORICO));
  disegnaStorico();
}

function disegnaStorico() {
  const voci = leggi(CHIAVE_STORICO, []) || [];
  el.sezioneStorico.hidden = voci.length === 0;
  el.storico.innerHTML = '';
  voci.forEach((voce) => {
    const riga = document.createElement('li');
    const nome = document.createElement('span');
    nome.textContent = voce.nome;
    const quando = document.createElement('span');
    quando.className = 'quando';
    quando.textContent = new Date(voce.quando).toLocaleDateString('it-IT', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
    riga.appendChild(nome);
    riga.appendChild(quando);
    el.storico.appendChild(riga);
  });
}

el.btnPulisciStorico.addEventListener('click', () => {
  cancella(CHIAVE_STORICO);
  disegnaStorico();
});

/* ------------------------------------- link passato nell'indirizzo (?u=…) */

function prendiLinkDallaUrl() {
  const parametri = new URLSearchParams(location.search);
  const link = parametri.get('u');
  if (!link) return;
  el.campoLink.value = link;
  /* Pulisco l'indirizzo: se l'utente ricarica non rifa' la stessa ricerca. */
  history.replaceState(null, '', location.pathname);
  el.formLink.requestSubmit();
}

/* ------------------------------------------------------------------- avvio */

if (token) {
  mostraApp();
} else {
  mostraLogin();
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* senza service worker il sito funziona, perde solo l'avvio offline */
    });
  });
}
