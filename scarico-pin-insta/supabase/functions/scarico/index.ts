// Scarico - funzione per Supabase Edge Functions.
//
// ATTENZIONE: file GENERATO da tools/build_supabase.py. Non modificarlo a
// mano: si cambia deno/scarico.ts e web/, poi si rigenera.

const RISORSE: Record<string, string> = {
  "index.html": "<!DOCTYPE html>\n<html lang=\"it\" data-base=\"__BASE__\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n<meta name=\"robots\" content=\"noindex, nofollow\">\n<title>Scarico</title>\n\n<meta name=\"apple-mobile-web-app-capable\" content=\"yes\">\n<meta name=\"apple-mobile-web-app-status-bar-style\" content=\"black-translucent\">\n<meta name=\"apple-mobile-web-app-title\" content=\"Scarico\">\n<meta name=\"theme-color\" content=\"#0c0a12\" media=\"(prefers-color-scheme: dark)\">\n<meta name=\"theme-color\" content=\"#f7f4fb\" media=\"(prefers-color-scheme: light)\">\n<link id=\"manifesto\" rel=\"manifest\" href=\"__BASE__/manifest.webmanifest\">\n<link rel=\"apple-touch-icon\" href=\"__BASE__/icons/icon-180.png\">\n<link rel=\"icon\" href=\"__BASE__/icons/icon-192.png\" sizes=\"192x192\" type=\"image/png\">\n<link rel=\"stylesheet\" href=\"__BASE__/style.css\">\n</head>\n<body>\n\n<div class=\"aurora\" aria-hidden=\"true\"><span class=\"blob blob-a\"></span><span class=\"blob blob-b\"></span></div>\n\n<header class=\"testata\">\n  <div class=\"logo\">\n    <img src=\"__BASE__/icons/icon-192.png\" alt=\"\" width=\"28\" height=\"28\">\n    <span>Scarico</span>\n  </div>\n  <span id=\"spia\" class=\"spia\" hidden></span>\n</header>\n\n<main class=\"foglio\">\n\n  <!-- ------------------------------------------------ serve il link completo -->\n  <section id=\"vista-chiave\" class=\"scheda avviso\" hidden>\n    <h1 class=\"titolo-avviso\">Serve il tuo link personale</h1>\n    <p>\n      Questo indirizzo non ha la chiave. Apri il link completo, quello che finisce\n      con <code>?k=\u2026</code>, e da l\u00ec premi <strong>Condividi \u2192 Aggiungi a Home</strong>:\n      l'icona se la ricorda per sempre.\n    </p>\n  </section>\n\n  <!-- ----------------------------------------------------------- schermata -->\n  <div id=\"vista-app\" hidden>\n\n    <section class=\"eroe\">\n      <h1>Incolla il link.<br><em>Finisce in galleria.</em></h1>\n      <p class=\"occhiello\">Pinterest e Instagram \u00b7 video, reel, storie e foto</p>\n    </section>\n\n    <section class=\"scheda incolla\">\n      <form id=\"form-link\">\n        <textarea id=\"campo-link\" rows=\"2\" inputmode=\"url\" enterkeyhint=\"search\"\n                  autocapitalize=\"off\" autocorrect=\"off\" spellcheck=\"false\"\n                  placeholder=\"Incolla qui il link del post\"></textarea>\n        <button id=\"btn-principale\" class=\"bottone primario grande\" type=\"submit\">\n          <span class=\"etichetta-bottone\">Incolla e trova</span>\n        </button>\n      </form>\n      <p id=\"stato\" class=\"stato\" role=\"status\" aria-live=\"polite\" hidden></p>\n    </section>\n\n    <section id=\"risultati\" class=\"risultati\" aria-label=\"Trovati\"></section>\n\n    <section id=\"guida\" class=\"scheda guida\" hidden>\n      <h2>Come funziona</h2>\n      <ol class=\"passi\">\n        <li>\n          <span class=\"numero\">1</span>\n          <span>Copia il link del post da Pinterest o Instagram.</span>\n        </li>\n        <li>\n          <span class=\"numero\">2</span>\n          <span>Torna qui e tocca <strong>Incolla e trova</strong>.</span>\n        </li>\n        <li>\n          <span class=\"numero\">3</span>\n          <span>Tocca <strong>Salva in galleria</strong>, poi <strong>Salva video</strong>\n                nel foglio che si apre.</span>\n        </li>\n      </ol>\n      <button id=\"btn-chiudi-guida\" class=\"bottone fantasma piccolo\" type=\"button\">Ho capito</button>\n    </section>\n\n    <section id=\"aggiungi-home\" class=\"scheda promemoria\" hidden>\n      <p><strong>Mettilo sulla schermata Home.</strong> Premi\n         <svg class=\"glifo-condividi\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" width=\"17\" height=\"17\">\n           <path d=\"M12 3v12M12 3l-3.2 3.2M12 3l3.2 3.2\" fill=\"none\" stroke=\"currentColor\"\n                 stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n           <path d=\"M6 11H4.6v8.4h14.8V11H18\" fill=\"none\" stroke=\"currentColor\"\n                 stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n         </svg>\n         <strong>Condividi</strong> in Safari, poi <strong>Aggiungi a Home</strong>. Si apre come un'app.</p>\n      <button id=\"btn-chiudi-promemoria\" class=\"bottone fantasma piccolo\" type=\"button\">Chiudi</button>\n    </section>\n\n    <footer class=\"piede\"><span id=\"info-server\"></span></footer>\n  </div>\n</main>\n\n<div id=\"toast\" class=\"toast\" role=\"status\" aria-live=\"polite\" hidden></div>\n\n<script src=\"__BASE__/app.js\"></script>\n</body>\n</html>\n",
  "style.css": "/* Scarico - foglio di stile unico, mobile-first.\n\n   Colori: un primario viola (gli accenti, la minoranza dei pixel), una scala di\n   grigi tinta di viola (quasi tutta la superficie) e tre colori semantici usati\n   solo per dire com'e' andata. Il rosa compare solo dentro il gradiente del\n   bottone principale, per dare un punto caldo in una pagina fredda. */\n\n:root {\n  color-scheme: dark;\n\n  /* primario */\n  --p-300: #d8b4fe;\n  --p-400: #c084fc;\n  --p-500: #a855f7;\n  --p-600: #9333ea;\n  --rosa-600: #db2777;\n\n  /* neutri tinti di viola */\n  --n-950: #0c0a12;\n  --n-900: #141019;\n  --n-850: #1a1522;\n  --n-800: #221b2d;\n  --n-700: #312841;\n  --n-500: #6f6683;\n  --n-400: #9b91ad;\n  --n-200: #d9d3e3;\n  --n-50:  #f7f4fb;\n\n  --fondo: var(--n-950);\n  --superficie: rgba(255, 255, 255, 0.045);\n  --superficie-alta: rgba(255, 255, 255, 0.075);\n  --bordo: rgba(255, 255, 255, 0.10);\n  --bordo-forte: rgba(255, 255, 255, 0.18);\n  --testo: var(--n-50);\n  --testo-tenue: var(--n-400);\n\n  /* semantici: verde fatto, rosso guaio, ambra attenzione */\n  --ok-fondo: rgba(34, 197, 94, 0.16);\n  --ok-testo: #6ee7a0;\n  --no-fondo: rgba(244, 63, 94, 0.16);\n  --no-testo: #fb8da0;\n  --forse-fondo: rgba(245, 158, 11, 0.16);\n  --forse-testo: #fbbf4a;\n\n  --r-grande: 24px;\n  --r: 18px;\n  --r-piccolo: 12px;\n  --gutter: 18px;\n  --ombra: 0 18px 40px -12px rgba(0, 0, 0, 0.6);\n}\n\n@media (prefers-color-scheme: light) {\n  :root {\n    color-scheme: light;\n    --fondo: var(--n-50);\n    --superficie: #ffffff;\n    --superficie-alta: #ffffff;\n    --bordo: #e6dff1;\n    --bordo-forte: #d3c8e5;\n    --testo: #17121f;\n    --testo-tenue: #5d5570;\n    --ok-fondo: rgba(22, 163, 74, 0.12);\n    --ok-testo: #15803d;\n    --no-fondo: rgba(225, 29, 72, 0.10);\n    --no-testo: #be123c;\n    --forse-fondo: rgba(217, 119, 6, 0.12);\n    --forse-testo: #b45309;\n    --ombra: 0 16px 36px -14px rgba(60, 30, 90, 0.22);\n  }\n}\n\n* { box-sizing: border-box; }\nhtml { -webkit-text-size-adjust: 100%; }\n\nbody {\n  margin: 0;\n  min-height: 100svh;\n  background: var(--fondo);\n  color: var(--testo);\n  font: 17px/1.5 -apple-system, BlinkMacSystemFont, \"SF Pro Text\", \"Segoe UI\", Roboto, system-ui, sans-serif;\n  -webkit-font-smoothing: antialiased;\n  overflow-wrap: break-word;\n  -webkit-tap-highlight-color: transparent;\n}\n\n[hidden] { display: none !important; }\n\n/* --------------------------------------------------------------- sfondo */\n\n.aurora { position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 0; }\n.blob { position: absolute; border-radius: 50%; filter: blur(90px); opacity: 0.55; }\n.blob-a {\n  width: 420px; height: 420px; top: -170px; right: -140px;\n  background: radial-gradient(circle, var(--p-500), transparent 68%);\n}\n.blob-b {\n  width: 380px; height: 380px; top: 180px; left: -180px;\n  background: radial-gradient(circle, var(--rosa-600), transparent 68%);\n  opacity: 0.38;\n}\n@media (prefers-color-scheme: light) {\n  .blob-a { opacity: 0.30; }\n  .blob-b { opacity: 0.22; }\n}\n\n/* -------------------------------------------------------------- testata */\n\n.testata {\n  position: relative;\n  z-index: 1;\n  display: flex;\n  align-items: center;\n  justify-content: space-between;\n  gap: 12px;\n  padding: calc(14px + env(safe-area-inset-top)) var(--gutter) 6px;\n  max-width: 560px;\n  margin: 0 auto;\n}\n.logo { display: flex; align-items: center; gap: 9px; font-weight: 650; letter-spacing: -0.01em; }\n.logo img { border-radius: 8px; }\n.spia { font-size: 0.74rem; color: var(--testo-tenue); }\n\n.foglio {\n  position: relative;\n  z-index: 1;\n  max-width: 560px;\n  margin: 0 auto;\n  padding: 10px var(--gutter) calc(48px + env(safe-area-inset-bottom));\n  display: grid;\n  gap: 16px;\n}\n\n/* ----------------------------------------------------------------- eroe */\n\n.eroe { padding: 18px 2px 2px; }\n.eroe h1 {\n  margin: 0;\n  font-size: clamp(2rem, 9vw, 2.6rem);\n  line-height: 1.08;\n  letter-spacing: -0.035em;\n  font-weight: 700;\n}\n.eroe em {\n  font-style: normal;\n  background: linear-gradient(100deg, var(--p-400), var(--rosa-600));\n  -webkit-background-clip: text;\n  background-clip: text;\n  color: transparent;\n}\n/* L'occhiello sta sotto e piu' piccolo del titolo: non deve competerci. */\n.occhiello { margin: 12px 0 0; color: var(--testo-tenue); font-size: 0.95rem; }\n\n/* --------------------------------------------------------------- schede */\n\n.scheda {\n  background: var(--superficie);\n  border: 1px solid var(--bordo);\n  border-radius: var(--r-grande);\n  padding: 16px;\n  backdrop-filter: blur(16px);\n  -webkit-backdrop-filter: blur(16px);\n}\n\n.incolla form { display: grid; gap: 12px; }\n\ntextarea {\n  width: 100%;\n  font: inherit;\n  font-size: 1rem;              /* sotto i 16px iOS ingrandisce la pagina da solo */\n  color: var(--testo);\n  background: var(--superficie-alta);\n  border: 1px solid var(--bordo);\n  border-radius: var(--r);\n  padding: 15px 16px;\n  resize: none;\n  transition: border-color 0.15s ease, box-shadow 0.15s ease;\n}\ntextarea::placeholder { color: var(--n-500); }\ntextarea:focus {\n  outline: none;\n  border-color: var(--p-500);\n  box-shadow: 0 0 0 4px rgba(168, 85, 247, 0.18);\n}\n\n/* -------------------------------------------------------------- bottoni */\n\n.bottone {\n  position: relative;\n  overflow: hidden;\n  display: inline-flex;\n  align-items: center;\n  justify-content: center;\n  gap: 8px;\n  font: inherit;\n  font-weight: 650;\n  text-align: center;\n  text-decoration: none;\n  border: 1px solid transparent;\n  border-radius: var(--r);\n  padding: 15px 18px;\n  min-height: 52px;\n  cursor: pointer;\n  transition: transform 0.08s ease, opacity 0.15s ease, background-color 0.15s ease;\n}\n.bottone:active { transform: scale(0.985); }\n.bottone[disabled] { opacity: 0.6; cursor: default; }\n.grande { min-height: 56px; font-size: 1.05rem; }\n\n.primario {\n  /* Bianco su questi due viola/rosa supera 4.5:1: leggibile anche al sole. */\n  background: linear-gradient(100deg, var(--p-600), var(--rosa-600));\n  color: #ffffff;\n  box-shadow: 0 10px 26px -10px rgba(147, 51, 234, 0.75);\n}\n.secondario { background: var(--superficie-alta); color: var(--testo); border-color: var(--bordo-forte); }\n.fantasma { background: transparent; color: var(--testo-tenue); border-color: var(--bordo); min-height: 46px; }\n.piccolo { min-height: 40px; padding: 9px 14px; font-size: 0.88rem; border-radius: var(--r-piccolo); }\n\n.etichetta-bottone { position: relative; z-index: 1; }\n\n/* Barra di avanzamento dentro al bottone: il progresso si legge dove si tocca. */\n.riempimento {\n  position: absolute;\n  inset: 0 auto 0 0;\n  width: 0;\n  background: rgba(255, 255, 255, 0.26);\n  transition: width 0.18s ease;\n  z-index: 0;\n}\n.bottone.pronto {\n  background: linear-gradient(100deg, #16a34a, #0f9d6b);\n  box-shadow: 0 10px 26px -10px rgba(22, 163, 74, 0.7);\n}\n\n/* ------------------------------------------------------------- messaggi */\n\n.stato {\n  margin: 12px 0 0;\n  font-size: 0.92rem;\n  padding: 12px 14px;\n  border-radius: var(--r-piccolo);\n  background: var(--superficie-alta);\n  border: 1px solid var(--bordo);\n  color: var(--testo-tenue);\n}\n.stato.lavorando { border-color: rgba(168, 85, 247, 0.5); color: var(--testo); }\n.stato.fatto { background: var(--ok-fondo); border-color: transparent; color: var(--ok-testo); }\n.stato.guaio { background: var(--no-fondo); border-color: transparent; color: var(--no-testo); }\n.stato.attenzione { background: var(--forse-fondo); border-color: transparent; color: var(--forse-testo); }\n\n.filatoio {\n  display: inline-block;\n  width: 13px; height: 13px;\n  margin-right: 8px;\n  vertical-align: -1px;\n  border: 2px solid rgba(168, 85, 247, 0.3);\n  border-top-color: var(--p-400);\n  border-radius: 50%;\n  animation: gira 0.7s linear infinite;\n}\n@keyframes gira { to { transform: rotate(360deg); } }\n\n/* ----------------------------------------------------------- risultati */\n\n.risultati { display: grid; gap: 16px; }\n\n.risultato {\n  background: var(--superficie);\n  border: 1px solid var(--bordo);\n  border-radius: var(--r-grande);\n  overflow: hidden;\n  box-shadow: var(--ombra);\n  animation: entra 0.28s ease both;\n}\n@keyframes entra { from { opacity: 0; transform: translateY(10px); } }\n\n.cornice {\n  position: relative;\n  display: block;\n  width: 100%;\n  aspect-ratio: 4 / 5;\n  background: var(--superficie-alta);\n}\n.cornice img { width: 100%; height: 100%; object-fit: cover; display: block; }\n.cornice .sfumatura {\n  position: absolute; inset: 0;\n  background: linear-gradient(to top, rgba(8, 6, 12, 0.88) 0%, rgba(8, 6, 12, 0) 52%);\n}\n.bollo {\n  position: absolute;\n  top: 12px; left: 12px;\n  display: inline-flex;\n  align-items: center;\n  gap: 6px;\n  padding: 6px 11px;\n  border-radius: 999px;\n  font-size: 0.76rem;\n  font-weight: 650;\n  color: #fff;\n  background: rgba(10, 8, 16, 0.6);\n  backdrop-filter: blur(10px);\n  border: 1px solid rgba(255, 255, 255, 0.16);\n}\n.didascalia { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px; color: #fff; }\n.didascalia h3 {\n  margin: 0 0 6px;\n  font-size: 1.02rem;\n  line-height: 1.28;\n  font-weight: 650;\n  display: -webkit-box;\n  -webkit-line-clamp: 2;\n  -webkit-box-orient: vertical;\n  overflow: hidden;\n  text-shadow: 0 1px 10px rgba(0, 0, 0, 0.5);\n}\n.didascalia .meta { margin: 0; font-size: 0.82rem; color: rgba(255, 255, 255, 0.82); }\n.meta .punto { opacity: 0.55; margin: 0 6px; }\n\n.azioni { display: grid; gap: 9px; padding: 14px; }\n\n/* ----------------------------------------------------- guida e avvisi */\n\n.guida h2 { margin: 0 0 14px; font-size: 1.05rem; }\n.passi { list-style: none; margin: 0 0 14px; padding: 0; display: grid; gap: 12px; font-size: 0.95rem; }\n/* Due celle e basta: il numero e il testo. Se il testo del passo finisse\n   spezzato in piu' figli, ogni pezzo diventerebbe una cella a se' e andrebbe\n   a capo in colonna. */\n.passi li { display: grid; grid-template-columns: 26px minmax(0, 1fr); gap: 11px; align-items: start; }\n.numero {\n  display: grid;\n  place-items: center;\n  flex: none;\n  width: 26px; height: 26px;\n  border-radius: 50%;\n  background: linear-gradient(135deg, var(--p-600), var(--rosa-600));\n  color: #fff;\n  font-size: 0.8rem;\n  font-weight: 700;\n}\n.avviso { border-color: rgba(245, 158, 11, 0.45); }\n.titolo-avviso { font-size: 1.2rem; margin: 0 0 10px; }\n.avviso code {\n  background: var(--superficie-alta);\n  padding: 2px 6px;\n  border-radius: 6px;\n  font-size: 0.88em;\n}\n.promemoria { display: grid; gap: 10px; }\n.promemoria p { margin: 0; font-size: 0.93rem; color: var(--testo-tenue); }\n.promemoria .bottone { justify-self: start; }\n.glifo-condividi { vertical-align: -3px; color: var(--p-400); }\n.guida .bottone { justify-self: start; }\n\n.piede { text-align: center; color: var(--n-500); font-size: 0.74rem; }\n\n/* ---------------------------------------------------------------- toast */\n\n.toast {\n  position: fixed;\n  left: 50%;\n  bottom: calc(22px + env(safe-area-inset-bottom));\n  transform: translate(-50%, 16px);\n  z-index: 50;\n  max-width: min(440px, calc(100vw - 32px));\n  padding: 13px 18px;\n  border-radius: 999px;\n  font-size: 0.92rem;\n  font-weight: 550;\n  text-align: center;\n  color: #fff;\n  background: rgba(18, 14, 26, 0.94);\n  border: 1px solid var(--bordo-forte);\n  box-shadow: var(--ombra);\n  backdrop-filter: blur(14px);\n  opacity: 0;\n  transition: opacity 0.22s ease, transform 0.22s ease;\n}\n.toast.visibile { opacity: 1; transform: translate(-50%, 0); }\n.toast.ok { background: #15803d; border-color: transparent; }\n.toast.no { background: #be123c; border-color: transparent; }\n\n@media (prefers-reduced-motion: reduce) {\n  .filatoio { animation-duration: 2.4s; }\n  .risultato { animation: none; }\n  .bottone:active { transform: none; }\n  .toast, .riempimento { transition: none; }\n}\n",
  "app.js": "/* Scarico - logica del sito. Nessuna libreria.\n\n   Il punto delicato di tutta l'app e' il salvataggio in galleria.\n   Su iOS nessuna pagina web puo' scrivere in Foto da sola: l'unica via e'\n   il foglio di condivisione (navigator.share con un file), dove compare\n   \u00abSalva video\u00bb o \u00abSalva immagine\u00bb. Quindi la pagina scarica il file in\n   memoria e poi lo passa al foglio. */\n'use strict';\n\nconst CHIAVE_SALVATA = 'scarico.k';\nconst GUIDA_VISTA = 'scarico.guida';\nconst PROMEMORIA_VISTO = 'scarico.home';\nconst LIMITE_MEMORIA = 400 * 1024 * 1024; // oltre, il telefono rischia di non farcela\n\n/* Il sito puo' stare alla radice di un dominio oppure dentro un percorso\n   (su Supabase vive sotto /functions/v1/scarico). Il prefisso lo scrive il\n   server nell'attributo data-base, cosi' il resto del codice non se ne cura. */\nconst BASE = document.documentElement.dataset.base || '';\n\nconst $ = (sel) => document.querySelector(sel);\n\nconst el = {\n  vistaChiave: $('#vista-chiave'),\n  vistaApp: $('#vista-app'),\n  form: $('#form-link'),\n  campo: $('#campo-link'),\n  btnPrincipale: $('#btn-principale'),\n  stato: $('#stato'),\n  risultati: $('#risultati'),\n  guida: $('#guida'),\n  btnChiudiGuida: $('#btn-chiudi-guida'),\n  promemoria: $('#aggiungi-home'),\n  btnChiudiPromemoria: $('#btn-chiudi-promemoria'),\n  infoServer: $('#info-server'),\n  spia: $('#spia'),\n  toast: $('#toast'),\n  manifesto: $('#manifesto'),\n};\n\n/* ----------------------------------------------------------- memoria locale */\n\nfunction leggi(chiave, difetto = null) {\n  try {\n    const grezzo = localStorage.getItem(chiave);\n    return grezzo === null ? difetto : grezzo;\n  } catch (_) {\n    return difetto;\n  }\n}\nfunction scrivi(chiave, valore) {\n  try { localStorage.setItem(chiave, valore); } catch (_) {}\n}\n\n/* -------------------------------------------------------- chiave d'accesso */\n\nfunction trovaChiave() {\n  const daUrl = new URLSearchParams(location.search).get('k');\n  if (daUrl) {\n    scrivi(CHIAVE_SALVATA, daUrl);\n    return daUrl;\n  }\n  /* In Safari e nell'app aggiunta alla Home lo spazio di memoria e' separato,\n     per questo la chiave deve restare dentro l'indirizzo: qui il salvataggio\n     locale e' solo una rete di sicurezza. */\n  return leggi(CHIAVE_SALVATA, '');\n}\n\nconst chiave = trovaChiave();\n\n/* Il manifest deve portarsi dietro la chiave, altrimenti l'icona sulla Home\n   rischia di aprire l'indirizzo nudo e di non entrare piu'. */\nif (chiave && el.manifesto) {\n  el.manifesto.href = BASE + '/manifest.webmanifest?k=' + encodeURIComponent(chiave);\n}\n\nasync function chiama(percorso, opzioni = {}) {\n  const intestazioni = Object.assign({ 'Content-Type': 'application/json' }, opzioni.headers || {});\n  if (chiave) intestazioni['X-Chiave'] = chiave;\n  const risposta = await fetch(BASE + percorso, Object.assign({}, opzioni, { headers: intestazioni }));\n  let dati = null;\n  try { dati = await risposta.json(); } catch (_) {}\n  if (!risposta.ok) {\n    const errore = new Error((dati && dati.detail) || 'Errore ' + risposta.status + '.');\n    errore.stato = risposta.status;\n    throw errore;\n  }\n  return dati;\n}\n\n/* ------------------------------------------------------------------ aiuti */\n\nfunction toast(testo, tipo = '') {\n  el.toast.textContent = testo;\n  el.toast.className = 'toast visibile' + (tipo ? ' ' + tipo : '');\n  el.toast.hidden = false;\n  clearTimeout(toast._t);\n  toast._t = setTimeout(() => {\n    el.toast.classList.remove('visibile');\n    setTimeout(() => { el.toast.hidden = true; }, 250);\n  }, 3600);\n}\n\nfunction mostraStato(testo, classe = '', conFilatoio = false) {\n  el.stato.className = 'stato' + (classe ? ' ' + classe : '');\n  el.stato.innerHTML = '';\n  if (conFilatoio) {\n    const s = document.createElement('span');\n    s.className = 'filatoio';\n    el.stato.appendChild(s);\n  }\n  el.stato.appendChild(document.createTextNode(testo));\n  el.stato.hidden = false;\n}\nfunction nascondiStato() { el.stato.hidden = true; }\n\nfunction peso(byte) {\n  if (!byte) return null;\n  if (byte < 1048576) return Math.max(1, Math.round(byte / 1024)) + ' kB';\n  const mb = byte / 1048576;\n  if (mb >= 1000) return (mb / 1024).toFixed(1) + ' GB';\n  return mb.toFixed(mb < 10 ? 1 : 0) + ' MB';\n}\nfunction durata(secondi) {\n  if (!secondi) return null;\n  return Math.floor(secondi / 60) + ':' + String(Math.round(secondi % 60)).padStart(2, '0');\n}\n\n/* ------------------------------------------------------------ avvio vista */\n\nasync function avvia() {\n  if (!chiave) {\n    el.vistaChiave.hidden = false;\n    return;\n  }\n  try {\n    const info = await chiama('/api/me');\n    el.vistaApp.hidden = false;\n    const pezzi = ['v' + info.versione];\n    if (!info.ffmpeg) pezzi.push('senza ffmpeg');\n    if (!info.cookie_instagram) pezzi.push('senza cookie Instagram');\n    el.infoServer.textContent = pezzi.join(' \u00b7 ');\n    if (!leggi(GUIDA_VISTA)) el.guida.hidden = false;\n    mostraPromemoriaHome();\n    prendiLinkDallaUrl();\n  } catch (errore) {\n    if (errore.stato === 401) {\n      el.vistaChiave.hidden = false;\n      return;\n    }\n    el.vistaApp.hidden = false;\n    toast(errore.message, 'no');\n  }\n}\n\nfunction inApp() {\n  return window.navigator.standalone === true ||\n    window.matchMedia('(display-mode: standalone)').matches;\n}\n\nfunction mostraPromemoriaHome() {\n  if (inApp() || leggi(PROMEMORIA_VISTO)) return;\n  const iOS = /iPad|iPhone|iPod/.test(navigator.userAgent);\n  if (!iOS) return;\n  el.promemoria.hidden = false;\n}\n\nel.btnChiudiGuida.addEventListener('click', () => {\n  el.guida.hidden = true;\n  scrivi(GUIDA_VISTA, '1');\n});\nel.btnChiudiPromemoria.addEventListener('click', () => {\n  el.promemoria.hidden = true;\n  scrivi(PROMEMORIA_VISTO, '1');\n});\n\n/* --------------------------------------------------- incolla, cerca, mostra */\n\nel.campo.addEventListener('input', () => {\n  const pieno = el.campo.value.trim().length > 0;\n  el.btnPrincipale.querySelector('.etichetta-bottone').textContent = pieno ? 'Trova' : 'Incolla e trova';\n});\n\n/* Un bottone solo: se il campo e' vuoto legge gli appunti, se c'e' gia' un\n   link cerca quello. Due bottoni che fanno la stessa cosa confondono. */\nel.form.addEventListener('submit', async (evento) => {\n  evento.preventDefault();\n  if (el.campo.value.trim()) {\n    cerca(el.campo.value.trim());\n    return;\n  }\n  try {\n    const testo = await navigator.clipboard.readText();\n    if (!testo || !testo.trim()) {\n      toast('Negli appunti non c\u2019\u00e8 niente.', 'no');\n      el.campo.focus();\n      return;\n    }\n    el.campo.value = testo.trim();\n    cerca(testo.trim());\n  } catch (_) {\n    /* iOS chiede il permesso e si puo' dire di no: resta l'incolla a mano. */\n    el.campo.focus();\n    toast('Tieni premuto nel campo e scegli \u00abIncolla\u00bb.');\n  }\n});\n\nasync function cerca(testo) {\n  el.btnPrincipale.disabled = true;\n  el.risultati.innerHTML = '';\n  memoria.clear();\n  mostraStato('Sto cercando\u2026', 'lavorando', true);\n  try {\n    const dati = await chiama('/api/resolve', { method: 'POST', body: JSON.stringify({ url: testo }) });\n    const elementi = dati.elementi || [];\n    if (!elementi.length) {\n      mostraStato('Non ho trovato niente da scaricare.', 'guaio');\n      return;\n    }\n    nascondiStato();\n    elementi.forEach((elemento) => el.risultati.appendChild(creaScheda(elemento)));\n    if (elementi.length > 1) {\n      mostraStato('Questo post ha ' + elementi.length + ' elementi.', 'fatto');\n    }\n    el.risultati.firstElementChild.scrollIntoView({ behavior: 'smooth', block: 'nearest' });\n  } catch (errore) {\n    mostraStato(errore.message, errore.stato === 403 ? 'attenzione' : 'guaio');\n  } finally {\n    el.btnPrincipale.disabled = false;\n  }\n}\n\nfunction creaScheda(elemento) {\n  const scheda = document.createElement('article');\n  scheda.className = 'risultato';\n\n  const cornice = document.createElement('div');\n  cornice.className = 'cornice';\n\n  const anteprima = document.createElement('img');\n  anteprima.alt = '';\n  anteprima.decoding = 'async';\n  if (elemento.url_thumb) anteprima.src = BASE + elemento.url_thumb;\n  cornice.appendChild(anteprima);\n\n  const sfumatura = document.createElement('div');\n  sfumatura.className = 'sfumatura';\n  cornice.appendChild(sfumatura);\n\n  const bollo = document.createElement('span');\n  bollo.className = 'bollo';\n  bollo.textContent = (elemento.source === 'pinterest' ? 'Pinterest' : 'Instagram') +\n    ' \u00b7 ' + (elemento.kind === 'video' ? 'video' : 'foto');\n  cornice.appendChild(bollo);\n\n  const didascalia = document.createElement('div');\n  didascalia.className = 'didascalia';\n  const titolo = document.createElement('h3');\n  titolo.textContent = elemento.title || 'Senza titolo';\n  didascalia.appendChild(titolo);\n\n  const pezzi = [];\n  const d = durata(elemento.duration);\n  if (d) pezzi.push(d);\n  const p = peso(elemento.filesize);\n  if (p) pezzi.push(p);\n  if (elemento.height) pezzi.push(elemento.height + 'p');\n  if (elemento.merge) pezzi.push('da comporre');\n  if (pezzi.length) {\n    const meta = document.createElement('p');\n    meta.className = 'meta';\n    pezzi.forEach((valore, indice) => {\n      if (indice) {\n        const punto = document.createElement('span');\n        punto.className = 'punto';\n        punto.textContent = '\\u00b7';\n        meta.appendChild(punto);\n      }\n      const pezzo = document.createElement('span');\n      pezzo.textContent = valore;\n      meta.appendChild(pezzo);\n    });\n    didascalia.appendChild(meta);\n  }\n  cornice.appendChild(didascalia);\n  scheda.appendChild(cornice);\n\n  const azioni = document.createElement('div');\n  azioni.className = 'azioni';\n\n  const salva = document.createElement('button');\n  salva.className = 'bottone primario grande';\n  salva.type = 'button';\n  const riempimento = document.createElement('span');\n  riempimento.className = 'riempimento';\n  const etichetta = document.createElement('span');\n  etichetta.className = 'etichetta-bottone';\n  etichetta.textContent = 'Salva in galleria';\n  salva.appendChild(riempimento);\n  salva.appendChild(etichetta);\n  salva.addEventListener('click', () => salvaInGalleria(elemento, salva));\n  azioni.appendChild(salva);\n\n  const neiFile = document.createElement('a');\n  neiFile.className = 'bottone secondario';\n  neiFile.href = BASE + elemento.url_file;\n  neiFile.setAttribute('download', elemento.filename || '');\n  neiFile.textContent = 'Salva nei File';\n  neiFile.addEventListener('click', () => toast('Lo trovi nei Download di Safari.'));\n  azioni.appendChild(neiFile);\n\n  scheda.appendChild(azioni);\n  return scheda;\n}\n\n/* ------------------------------------------------- salvataggio in galleria */\n\nconst memoria = new Map(); // id -> File gia' scaricato\n\nfunction testo(bottone, valore) {\n  bottone.querySelector('.etichetta-bottone').textContent = valore;\n}\nfunction avanzamento(bottone, frazione) {\n  bottone.querySelector('.riempimento').style.width = Math.round(frazione * 100) + '%';\n}\n\nasync function scaricaInMemoria(elemento, bottone) {\n  const risposta = await fetch(BASE + elemento.url_file + '&apri=1', {\n    headers: chiave ? { 'X-Chiave': chiave } : {},\n  });\n  if (!risposta.ok) {\n    let dettaglio = 'Errore ' + risposta.status + '.';\n    try { dettaglio = (await risposta.json()).detail || dettaglio; } catch (_) {}\n    throw new Error(dettaglio);\n  }\n\n  const totale = Number(risposta.headers.get('content-length')) || 0;\n  if (totale > LIMITE_MEMORIA) throw new Error('Questo file e\u2019 troppo grande per la galleria: usa \u00abSalva nei File\u00bb.');\n\n  let blob;\n  if (risposta.body && risposta.body.getReader) {\n    const lettore = risposta.body.getReader();\n    const pezzi = [];\n    let presi = 0;\n    for (;;) {\n      const { done, value } = await lettore.read();\n      if (done) break;\n      pezzi.push(value);\n      presi += value.length;\n      if (totale) avanzamento(bottone, presi / totale);\n      testo(bottone, totale ? 'Scarico ' + Math.round((presi / totale) * 100) + '%' : 'Scarico\u2026');\n    }\n    blob = new Blob(pezzi, { type: elemento.mime });\n  } else {\n    blob = await risposta.blob();\n  }\n  avanzamento(bottone, 1);\n  return new File([blob], elemento.filename, { type: elemento.mime });\n}\n\nfunction scaricaNeiFile(elemento) {\n  const ancora = document.createElement('a');\n  ancora.href = BASE + elemento.url_file;\n  ancora.download = elemento.filename || '';\n  document.body.appendChild(ancora);\n  ancora.click();\n  ancora.remove();\n}\n\nasync function salvaInGalleria(elemento, bottone) {\n  if (bottone.dataset.occupato === '1') return;\n  bottone.dataset.occupato = '1';\n  const etichettaIniziale = bottone.querySelector('.etichetta-bottone').textContent;\n  try {\n    let file = memoria.get(elemento.id);\n    if (!file) {\n      testo(bottone, 'Preparo\u2026');\n      file = await scaricaInMemoria(elemento, bottone);\n      memoria.set(elemento.id, file);\n    }\n\n    if (!(navigator.canShare && navigator.canShare({ files: [file] }))) {\n      /* Browser senza condivisione di file (o desktop): meglio il download\n         normale che un messaggio di errore. */\n      scaricaNeiFile(elemento);\n      testo(bottone, 'Scaricato');\n      toast('Questo browser non sa passare i file alla galleria: l\u2019ho scaricato.', '');\n      return;\n    }\n\n    testo(bottone, 'Apro\u2026');\n    await navigator.share({ files: [file] });\n    bottone.classList.remove('pronto');\n    testo(bottone, 'Salvato');\n    avanzamento(bottone, 0);\n    toast(elemento.kind === 'video' ? 'Scegli \u00abSalva video\u00bb' : 'Scegli \u00abSalva immagine\u00bb', 'ok');\n  } catch (errore) {\n    avanzamento(bottone, 0);\n    if (errore.name === 'AbortError') {\n      testo(bottone, etichettaIniziale);\n      return;\n    }\n    if (errore.name === 'NotAllowedError') {\n      /* iOS pretende che il foglio parta da un tocco: dopo un download lungo\n         l'autorizzazione scade. Il file pero' e' gia' in memoria, quindi il\n         prossimo tocco lo apre all'istante. */\n      bottone.classList.add('pronto');\n      testo(bottone, 'Tocca per salvare');\n      return;\n    }\n    testo(bottone, 'Riprova');\n    toast(errore.message, 'no');\n  } finally {\n    bottone.dataset.occupato = '0';\n  }\n}\n\n/* ------------------------------------------ link passato nell'indirizzo */\n\nfunction prendiLinkDallaUrl() {\n  const link = new URLSearchParams(location.search).get('u');\n  if (!link) return;\n  el.campo.value = link;\n  /* Tolgo solo \"u\" e tengo \"k\": senza la chiave il prossimo avvio non entra. */\n  const parametri = new URLSearchParams(location.search);\n  parametri.delete('u');\n  const coda = parametri.toString();\n  history.replaceState(null, '', location.pathname + (coda ? '?' + coda : ''));\n  cerca(link);\n}\n\n/* ------------------------------------------------------------------ avvio */\n\navvia();\n",
};

const ICONE: Record<string, string> = {
  "icon-180.png": "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",
  "icon-192.png": "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",
  "icon-512.png": "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",
};

/* Scarico - versione per Supabase Edge Functions (Deno).
 *
 * Differenze rispetto alla versione Python, e perche':
 *
 * - Niente yt-dlp: qui gira Deno, non Python. I link dei video si ricavano
 *   leggendo la pagina del post. Per Pinterest e' affidabile, per Instagram
 *   molto meno (vedi risolviInstagram).
 * - Niente ffmpeg: i contenuti con audio e video separati non si possono
 *   ricomporre. In pratica riguarda alcune storie.
 * - Nessuna memoria fra una richiesta e l'altra: le funzioni sono effimere e
 *   due richieste possono finire su istanze diverse. Per questo il biglietto
 *   di download non e' una chiave verso una tabella: e' l'indirizzo del file
 *   cifrato e autenticato dentro al biglietto stesso. Cosi' non serve stato,
 *   e l'indirizzo del CDN resta comunque illeggibile per chi lo riceve.
 */


const CHIAVE_ACCESSO = Deno.env.get("ACCESS_KEY") ?? "";
const COOKIE_IG = (Deno.env.get("IG_COOKIES") ?? "").trim();
const MAX_MB = Number(Deno.env.get("MAX_FILESIZE_MB") ?? "2048");
const VERSIONE = "2.0.0";

const UA_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " +
  "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

const SCADENZA_BIGLIETTO = 6 * 60 * 60 * 1000; // sei ore

/* ------------------------------------------------------------------ tipi */

interface Elemento {
  id: string;
  source: "pinterest" | "instagram";
  kind: "video" | "immagine";
  title: string;
  filename: string;
  ext: string;
  mime: string;
  width?: number;
  height?: number;
  duration?: number;
  filesize?: number;
  merge: boolean;
  diretto: string;          // resta sul server: finisce cifrato nel biglietto
  riferimento: string;      // Referer da usare col CDN
  miniatura?: string;
}

/* --------------------------------------------------- biglietti cifrati */

const codificatore = new TextEncoder();
const decodificatore = new TextDecoder();

let chiaveAes: CryptoKey | null = null;

async function chiaveDiCifratura(): Promise<CryptoKey> {
  if (chiaveAes) return chiaveAes;
  // La chiave di cifratura e' derivata da quella d'accesso: un solo segreto
  // da impostare, ma le due non coincidono mai.
  const seme = await crypto.subtle.digest(
    "SHA-256",
    codificatore.encode("biglietti:" + (CHIAVE_ACCESSO || "aperto")),
  );
  chiaveAes = await crypto.subtle.importKey("raw", seme, "AES-GCM", false, ["encrypt", "decrypt"]);
  return chiaveAes;
}

function base64urlDa(dati: Uint8Array): string {
  let testo = "";
  for (const byte of dati) testo += String.fromCharCode(byte);
  return btoa(testo).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64urlA(testo: string): Uint8Array {
  return base64A(testo.replace(/-/g, "+").replace(/_/g, "/"));
}

function base64A(testo: string): Uint8Array {
  const grezzo = atob(testo + "=".repeat((4 - (testo.length % 4)) % 4));
  return Uint8Array.from(grezzo, (c) => c.charCodeAt(0));
}

export async function sigilla(contenuto: unknown): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cifrato = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await chiaveDiCifratura(),
    codificatore.encode(JSON.stringify(contenuto)),
  );
  const insieme = new Uint8Array(iv.length + cifrato.byteLength);
  insieme.set(iv, 0);
  insieme.set(new Uint8Array(cifrato), iv.length);
  return base64urlDa(insieme);
}

export async function apri<T>(biglietto: string): Promise<T | null> {
  try {
    const dati = base64urlA(biglietto);
    const aperto = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: dati.slice(0, 12) },
      await chiaveDiCifratura(),
      dati.slice(12),
    );
    const contenuto = JSON.parse(decodificatore.decode(aperto));
    if (typeof contenuto?.scade === "number" && contenuto.scade < Date.now()) return null;
    return contenuto as T;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------- indirizzi */

const SUFFISSI_DOPPI = new Set(["co", "com", "net", "org", "ne", "or"]);
const HOST_ESATTI: Record<string, "pinterest" | "instagram"> = {
  "pin.it": "pinterest",
  "ig.me": "instagram",
  "instagr.am": "instagram",
};
const NOMI: Record<string, "pinterest" | "instagram"> = {
  pinterest: "pinterest",
  instagram: "instagram",
};

/** Guarda il dominio registrato, non l'inizio del nome: senza questo,
 *  pinterest.qualcosaltro.com passerebbe. */
export function riconosci(host: string | null): "pinterest" | "instagram" | null {
  if (!host) return null;
  let h = host.toLowerCase().replace(/^\.+|\.+$/g, "");
  if (h.startsWith("www.")) h = h.slice(4);
  if (HOST_ESATTI[h]) return HOST_ESATTI[h];
  const parti = h.split(".");
  if (parti.length < 2) return null;
  const nome = parti.length >= 3 && SUFFISSI_DOPPI.has(parti[parti.length - 2]) &&
      parti[parti.length - 1].length === 2
    ? parti[parti.length - 3]
    : parti[parti.length - 2];
  return NOMI[nome] ?? null;
}

/** Il foglio di condivisione di iOS incolla spesso titolo e link insieme. */
export function primoLinkUtile(testo: string): { url: string; fonte: "pinterest" | "instagram" } {
  const candidati = testo.match(/https?:\/\/[^\s<>"']+/gi) ??
    (testo.trim() ? ["https://" + testo.trim().split(/\s+/)[0]] : []);
  for (const grezzo of candidati) {
    const pulito = grezzo.replace(/[).,;'"]+$/, "");
    try {
      const u = new URL(pulito);
      const fonte = riconosci(u.hostname);
      if (fonte) return { url: u.origin + u.pathname, fonte };
    } catch { /* candidato non valido, passo al prossimo */ }
  }
  throw new ErroreUtente(
    "Questo link non e’ di Pinterest o Instagram. Copia l’indirizzo di un pin, di un reel o di un post.",
    400,
  );
}

/** Da dove accettiamo di scaricare i file veri. */
const CDN_AMMESSI = [".pinimg.com", ".cdninstagram.com", ".fbcdn.net", ".pinterest.com", ".instagram.com"];

export function cdnAmmesso(indirizzo: string): boolean {
  try {
    const u = new URL(indirizzo);
    if (u.protocol !== "https:") return false;
    return CDN_AMMESSI.some((suffisso) => u.hostname.endsWith(suffisso));
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------- utilita' */

class ErroreUtente extends Error {
  stato: number;
  constructor(messaggio: string, stato = 400) {
    super(messaggio);
    this.stato = stato;
  }
}

const MIME_PER_ESTENSIONE: Record<string, string> = {
  mp4: "video/mp4", m4v: "video/x-m4v", mov: "video/quicktime", webm: "video/webm",
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif",
};

export function nomePulito(titolo: string): string {
  const pulito = (titolo || "").replace(/[\\/:*?"<>|\x00-\x1f]+/g, " ").replace(/\s+/g, " ").trim();
  return (pulito.length > 70 ? pulito.slice(0, 70).trim() : pulito).replace(/^[.\-_ ]+|[.\-_ ]+$/g, "") ||
    "senza titolo";
}

export function componiNome(fonte: string, titolo: string, ext: string): string {
  const coda = Math.random().toString(36).slice(2, 8);
  return `${fonte} - ${nomePulito(titolo)} - ${coda}.${ext}`;
}

export function estensioneDa(indirizzo: string, difetto: string): string {
  const pezzo = indirizzo.split("?")[0].split("#")[0];
  const ext = pezzo.includes(".") ? pezzo.split(".").pop()!.toLowerCase() : "";
  return MIME_PER_ESTENSIONE[ext] ? ext : difetto;
}

export function decodificaHtml(testo: string): string {
  return testo
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&#x27;/gi, "'")
    .replace(/&nbsp;/g, " ");
}

export function meta(html: string, proprieta: string): string | null {
  const diretto = new RegExp(
    `<meta[^>]+(?:property|name)=["']${proprieta}["'][^>]*content=["']([^"']+)["']`, "i",
  ).exec(html);
  if (diretto) return decodificaHtml(diretto[1]);
  const rovescio = new RegExp(
    `<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["']${proprieta}["']`, "i",
  ).exec(html);
  return rovescio ? decodificaHtml(rovescio[1]) : null;
}

async function scaricaPagina(indirizzo: string, cookie?: string): Promise<{ html: string; finale: string }> {
  const risposta = await fetch(indirizzo, {
    headers: {
      "User-Agent": UA_IPHONE,
      "Accept": "text/html,application/xhtml+xml",
      "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
      ...(cookie ? { Cookie: cookie } : {}),
    },
    redirect: "follow",
  });
  if (!risposta.ok) {
    throw new ErroreUtente(
      `La pagina non risponde (codice ${risposta.status}). Il post potrebbe essere stato rimosso o essere privato.`,
      502,
    );
  }
  return { html: await risposta.text(), finale: risposta.url || indirizzo };
}

/* ------------------------------------------------------------ Pinterest */

async function risolviPinterest(indirizzo: string): Promise<Elemento[]> {
  const { html, finale } = await scaricaPagina(indirizzo);
  const titolo = meta(html, "og:title") ?? "pin";

  // I video dei pin stanno su v1.pinimg.com. Nei meta tag c'e' quello
  // principale, ma nel JSON della pagina ci sono tutte le qualita'.
  const candidati = new Set<string>();
  for (const chiave of ["og:video:secure_url", "og:video:url", "og:video"]) {
    const trovato = meta(html, chiave);
    if (trovato) candidati.add(trovato);
  }
  for (const trovato of html.matchAll(/https:\\?\/\\?\/v1\.pinimg\.com\\?\/videos\\?\/[^"'\\\s]+\.mp4/gi)) {
    candidati.add(trovato[0].replace(/\\\//g, "/"));
  }

  const video = [...candidati].filter(cdnAmmesso).sort((a, b) => qualita(b) - qualita(a))[0];
  const immagine = meta(html, "og:image");

  if (video) {
    const ext = estensioneDa(video, "mp4");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "pinterest",
      kind: "video",
      title: titolo,
      filename: componiNome("pinterest", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "video/mp4",
      merge: false,
      diretto: video,
      riferimento: finale,
      miniatura: immagine ?? undefined,
    }];
  }

  if (immagine && cdnAmmesso(immagine)) {
    const piena = immagine.replace(/\/\d+x\d*\//, "/originals/");
    const ext = estensioneDa(piena, "jpg");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "pinterest",
      kind: "immagine",
      title: titolo,
      filename: componiNome("pinterest", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "image/jpeg",
      merge: false,
      diretto: piena,
      riferimento: finale,
      miniatura: immagine,
    }];
  }

  throw new ErroreUtente("In questo pin non ho trovato ne’ un video ne’ una foto.", 404);
}

/** Le qualita' stanno nel percorso: .../720p/..., .../480p/... */
export function qualita(indirizzo: string): number {
  const trovato = /\/(\d{3,4})p\//.exec(indirizzo);
  return trovato ? Number(trovato[1]) : 0;
}

/* ------------------------------------------------------------ Instagram */

async function risolviInstagram(indirizzo: string): Promise<Elemento[]> {
  const codice = /\/(?:reel|reels|p|tv)\/([A-Za-z0-9_-]+)/.exec(indirizzo)?.[1];
  if (!codice) {
    throw new ErroreUtente(
      "Di Instagram so leggere i link di un post, di un reel o di un video (quelli con /p/, /reel/ o /tv/).",
      400,
    );
  }

  // 1. L'API interna: a volte risponde anche senza essere loggati, e quando
  //    risponde da' tutti gli elementi di un carosello.
  const daApi = await provaApiInstagram(codice).catch(() => null);
  if (daApi && daApi.length) return daApi;

  // 2. Altrimenti la pagina. Senza cookie Instagram la nega quasi sempre.
  const { html, finale } = await scaricaPagina(
    `https://www.instagram.com/p/${codice}/`,
    COOKIE_IG || undefined,
  );
  const titolo = meta(html, "og:title") ?? "instagram";
  const video = meta(html, "og:video:secure_url") ?? meta(html, "og:video") ??
    html.match(/"video_url":"([^"]+)"/)?.[1]?.replace(/\\u0026/g, "&").replace(/\\\//g, "/");
  const immagine = meta(html, "og:image") ??
    html.match(/"display_url":"([^"]+)"/)?.[1]?.replace(/\\u0026/g, "&").replace(/\\\//g, "/");

  if (video && cdnAmmesso(video)) {
    const ext = estensioneDa(video, "mp4");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "instagram",
      kind: "video",
      title: titolo,
      filename: componiNome("instagram", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "video/mp4",
      merge: false,
      diretto: video,
      riferimento: finale,
      miniatura: immagine ?? undefined,
    }];
  }
  if (immagine && cdnAmmesso(immagine)) {
    const ext = estensioneDa(immagine, "jpg");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "instagram",
      kind: "immagine",
      title: titolo,
      filename: componiNome("instagram", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "image/jpeg",
      merge: false,
      diretto: immagine,
      riferimento: finale,
      miniatura: immagine,
    }];
  }

  throw new ErroreUtente(
    COOKIE_IG
      ? "Instagram non mi ha dato il contenuto: il post potrebbe essere privato, oppure i cookie sono scaduti."
      : "Instagram chiede di essere loggato per questo contenuto. Aggiungi il segreto IG_COOKIES alla funzione.",
    403,
  );
}

/** Lo shortcode di Instagram e' il numero del contenuto in base64. */
export function idDaCodice(codice: string): string | null {
  const alfabeto = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  let numero = 0n;
  for (const carattere of codice.slice(0, 11)) {
    const posizione = alfabeto.indexOf(carattere);
    if (posizione < 0) return null;
    numero = numero * 64n + BigInt(posizione);
  }
  return numero > 0n ? numero.toString() : null;
}

async function provaApiInstagram(codice: string): Promise<Elemento[] | null> {
  const id = idDaCodice(codice);
  if (!id) return null;
  const risposta = await fetch(`https://www.instagram.com/api/v1/media/${id}/info/`, {
    headers: {
      "User-Agent": UA_IPHONE,
      "X-IG-App-ID": "936619743392459",
      "Accept": "application/json",
      ...(COOKIE_IG ? { Cookie: COOKIE_IG } : {}),
    },
  });
  if (!risposta.ok) return null;
  const dati = await risposta.json().catch(() => null);
  const posts = dati?.items;
  if (!Array.isArray(posts) || !posts.length) return null;

  const pezzi: Elemento[] = [];
  for (const post of posts) {
    const figli = post.carousel_media ?? [post];
    const didascalia = post.caption?.text ?? "instagram";
    for (const figlio of figli) {
      const video = figlio.video_versions?.[0]?.url;
      const foto = figlio.image_versions2?.candidates?.[0]?.url;
      const scelto = video ?? foto;
      if (!scelto || !cdnAmmesso(scelto)) continue;
      const ext = estensioneDa(scelto, video ? "mp4" : "jpg");
      pezzi.push({
        id: crypto.randomUUID().slice(0, 8),
        source: "instagram",
        kind: video ? "video" : "immagine",
        title: didascalia,
        filename: componiNome("instagram", didascalia, ext),
        ext,
        mime: MIME_PER_ESTENSIONE[ext] ?? (video ? "video/mp4" : "image/jpeg"),
        width: figlio.original_width,
        height: figlio.original_height,
        duration: figlio.video_duration,
        merge: false,
        diretto: scelto,
        riferimento: `https://www.instagram.com/p/${codice}/`,
        miniatura: foto ?? undefined,
      });
    }
  }
  return pezzi.length ? pezzi : null;
}

/* --------------------------------------------------------------- risposte */

const NIENTE_CACHE = { "Cache-Control": "no-store, max-age=0" };

function json(corpo: unknown, stato = 200, intestazioni: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(corpo), {
    status: stato,
    headers: { "Content-Type": "application/json; charset=utf-8", ...NIENTE_CACHE, ...intestazioni },
  });
}

function testo(contenuto: string, tipo: string, base: string): Response {
  return new Response(contenuto.replaceAll("__BASE__", base), {
    headers: { "Content-Type": tipo, ...NIENTE_CACHE },
  });
}

export function disposizione(nome: string): string {
  const ascii = nome.normalize("NFKD").replace(/[^\x20-\x7e]/g, "").replace(/"/g, "") || "file";
  return `attachment; filename="${ascii}"; filename*=UTF-8''${encodeURIComponent(nome)}`;
}

function chiaveValida(url: URL, intestazioni: Headers): boolean {
  if (!CHIAVE_ACCESSO) return true; // nessuna chiave impostata: sito aperto
  const fornita = intestazioni.get("x-chiave") ?? url.searchParams.get("k") ?? "";
  if (fornita.length !== CHIAVE_ACCESSO.length) return false;
  // Confronto a tempo costante, cosi' la chiave non si indovina a tentativi misurati.
  let differenza = 0;
  for (let i = 0; i < fornita.length; i++) {
    differenza |= fornita.charCodeAt(i) ^ CHIAVE_ACCESSO.charCodeAt(i);
  }
  return differenza === 0;
}

/* ------------------------------------------------- consegna dei file */

interface Biglietto {
  u: string;      // indirizzo sul CDN
  n: string;      // nome del file
  m: string;      // tipo
  r: string;      // riferimento
  scade: number;
}

async function consegna(biglietto: string, richiesta: Request, daSalvare: boolean, tettoByte?: number) {
  const dati = await apri<Biglietto>(biglietto);
  if (!dati || !cdnAmmesso(dati.u)) {
    return json({ detail: "Questo link di download e’ scaduto. Cerca di nuovo il post." }, 403);
  }

  const intestazioni: Record<string, string> = { "User-Agent": UA_IPHONE, Referer: dati.r };
  const intervallo = richiesta.headers.get("range");
  if (intervallo) intestazioni["Range"] = intervallo;

  const sorgente = await fetch(dati.u, { headers: intestazioni }).catch(() => null);
  if (!sorgente) return json({ detail: "Il server di Pinterest/Instagram non risponde." }, 504);
  if (!sorgente.ok && sorgente.status !== 206) {
    return json({
      detail: `Il file non e’ piu’ disponibile (il CDN ha risposto ${sorgente.status}). Cerca di nuovo il post.`,
    }, 502);
  }

  const lunghezza = Number(sorgente.headers.get("content-length") ?? "0");
  const tetto = tettoByte ?? (MAX_MB > 0 ? MAX_MB * 1024 * 1024 : 0);
  if (tetto && lunghezza > tetto) {
    await sorgente.body?.cancel();
    return json({ detail: `Il file pesa ${Math.round(lunghezza / 1048576)} MB e supera il limite.` }, 413);
  }

  const uscita = new Headers(NIENTE_CACHE);
  for (const nome of ["content-length", "content-range", "accept-ranges"]) {
    const valore = sorgente.headers.get(nome);
    if (valore) uscita.set(nome, valore);
  }
  uscita.set("Content-Type", sorgente.headers.get("content-type") ?? dati.m);
  if (daSalvare) uscita.set("Content-Disposition", disposizione(dati.n));

  // Il corpo passa in streaming: niente resta in memoria sulla funzione.
  return new Response(sorgente.body, { status: sorgente.status, headers: uscita });
}

/* ------------------------------------------------------------- manifest */

function manifest(base: string, chiave: string | null): Response {
  // iOS puo' usare start_url quando aggiunge il sito alla schermata Home:
  // se li' dentro la chiave non ci fosse, l'icona aprirebbe un indirizzo nudo.
  const partenza = chiave ? `${base}/?k=${encodeURIComponent(chiave)}` : `${base}/`;
  return json({
    name: "Scarico Pin e Insta",
    short_name: "Scarico",
    description: "Salva in galleria i video e le foto di Pinterest e Instagram.",
    lang: "it",
    start_url: partenza,
    scope: base + "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#0c0a12",
    theme_color: "#0c0a12",
    icons: [
      { src: `${base}/icons/icon-180.png`, sizes: "180x180", type: "image/png" },
      { src: `${base}/icons/icon-192.png`, sizes: "192x192", type: "image/png", purpose: "any" },
      { src: `${base}/icons/icon-512.png`, sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  }, 200, { "Content-Type": "application/manifest+json" });
}

/* ------------------------------------------------------------- risoluzione */

async function risolvi(grezzo: string): Promise<Elemento[]> {
  const { url, fonte } = primoLinkUtile(grezzo);
  const pezzi = fonte === "pinterest" ? await risolviPinterest(url) : await risolviInstagram(url);
  if (!pezzi.length) throw new ErroreUtente("Non ho trovato niente da scaricare.", 404);
  return pezzi;
}

async function conBiglietti(pezzi: Elemento[], base: string) {
  const fuori = [];
  for (const pezzo of pezzi) {
    const scade = Date.now() + SCADENZA_BIGLIETTO;
    const biglietto = await sigilla(
      { u: pezzo.diretto, n: pezzo.filename, m: pezzo.mime, r: pezzo.riferimento, scade } as Biglietto,
    );
    const voce: Record<string, unknown> = {
      id: pezzo.id,
      source: pezzo.source,
      kind: pezzo.kind,
      title: pezzo.title,
      filename: pezzo.filename,
      ext: pezzo.ext,
      mime: pezzo.mime,
      width: pezzo.width ?? null,
      height: pezzo.height ?? null,
      duration: pezzo.duration ?? null,
      filesize: pezzo.filesize ?? null,
      merge: pezzo.merge,
      url_file: `/api/file?t=${encodeURIComponent(biglietto)}`,
    };
    if (pezzo.miniatura && cdnAmmesso(pezzo.miniatura)) {
      const bigliettoThumb = await sigilla(
        { u: pezzo.miniatura, n: "anteprima.jpg", m: "image/jpeg", r: pezzo.riferimento, scade } as Biglietto,
      );
      voce.url_thumb = `/api/thumb?t=${encodeURIComponent(bigliettoThumb)}`;
    }
    fuori.push(voce);
  }
  return fuori;
}

/* ------------------------------------------------------------------ server */

Deno.serve(async (richiesta: Request) => {
  const url = new URL(richiesta.url);

  // Su Supabase la funzione vive sotto /functions/v1/<nome>: tutto il sito
  // deve sapere questo prefisso, altrimenti i suoi link cadono nel vuoto.
  const prefisso = url.pathname.match(/^\/functions\/v1\/[^/]+/)?.[0] ?? "";
  const percorso = url.pathname.slice(prefisso.length) || "/";
  const base = prefisso;

  try {
    if (percorso === "/" || percorso === "") {
      return testo(RISORSE["index.html"], "text/html; charset=utf-8", base);
    }
    if (percorso === "/style.css") return testo(RISORSE["style.css"], "text/css; charset=utf-8", base);
    if (percorso === "/app.js") {
      return testo(RISORSE["app.js"], "application/javascript; charset=utf-8", base);
    }
    if (percorso === "/manifest.webmanifest") {
      const k = url.searchParams.get("k");
      return manifest(base, k && chiaveValida(url, richiesta.headers) ? k : null);
    }
    if (percorso.startsWith("/icons/")) {
      const nome = percorso.slice("/icons/".length);
      const dati = ICONE[nome];
      if (!dati) return new Response("non trovata", { status: 404 });
      // Le icone viaggiano dentro al programma in base64: il PNG si ricompone qui.
      const byte = base64A(dati);
      return new Response(byte.buffer as ArrayBuffer, {
        headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" },
      });
    }
    if (percorso === "/api/health") {
      return json({ ok: true, version: VERSIONE, motore: "deno" });
    }

    // Da qui in poi serve la chiave.
    if (!chiaveValida(url, richiesta.headers)) {
      return json({
        detail: "Questo indirizzo non e’ completo. Apri il tuo link personale, quello che finisce con ?k=...",
      }, 401);
    }

    if (percorso === "/api/me") {
      return json({
        versione: VERSIONE,
        ffmpeg: false,
        cookie_instagram: Boolean(COOKIE_IG),
        limite_mb: MAX_MB,
      });
    }

    if (percorso === "/api/resolve" && richiesta.method === "POST") {
      const corpo = await richiesta.json().catch(() => ({}));
      const link = String(corpo?.url ?? "").trim();
      if (!link) return json({ detail: "Manca il link." }, 400);
      const pezzi = await risolvi(link);
      return json({ elementi: await conBiglietti(pezzi, base) });
    }

    if (percorso === "/api/file") {
      const biglietto = url.searchParams.get("t") ?? "";
      return await consegna(biglietto, richiesta, url.searchParams.get("apri") !== "1");
    }

    if (percorso === "/api/thumb") {
      const biglietto = url.searchParams.get("t") ?? "";
      return await consegna(biglietto, richiesta, false, 8 * 1024 * 1024);
    }

    if (percorso === "/api/shortcut") {
      const link = url.searchParams.get("u") ?? "";
      if (!link) return json({ detail: "Manca il link." }, 400);
      const pezzi = await conBiglietti(await risolvi(link), base);
      const primo = pezzi[0];
      return json({
        ok: true,
        download: url.origin + base + String(primo.url_file),
        nome_file: primo.filename,
        tipo: primo.mime,
        quanti: pezzi.length,
        tutti: pezzi.map((p) => ({
          download: url.origin + base + String(p.url_file),
          nome_file: p.filename,
          tipo: p.mime,
        })),
      });
    }

    return json({ detail: "Non esiste niente a questo indirizzo." }, 404);
  } catch (errore) {
    if (errore instanceof ErroreUtente) return json({ detail: errore.message }, errore.stato);
    console.error("errore non previsto:", errore);
    return json({ detail: "Qualcosa e’ andato storto sul server." }, 500);
  }
});
