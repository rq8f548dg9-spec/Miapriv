@echo off
REM Avvio su Windows. Prepara tutto la prima volta, poi parte e basta.
setlocal
cd /d "%~dp0"

if not exist .env (
  echo Primo avvio: creo il file .env
  copy .env.example .env >nul
  python -c "import pathlib,secrets;p=pathlib.Path('.env');p.write_text(p.read_text(encoding='utf-8').replace('SECRET_KEY=','SECRET_KEY='+secrets.token_urlsafe(48),1),encoding='utf-8')"
  echo.
  echo   Apri .env con il Blocco note, scrivi la tua APP_PASSWORD e rilancia questo file.
  echo.
  pause
  exit /b 1
)

if not exist .venv (
  echo Creo l'ambiente Python e installo le dipendenze ^(solo la prima volta^)...
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
)

where ffmpeg >nul 2>nul
if errorlevel 1 echo Attenzione: ffmpeg non trovato. I video con audio separato non funzioneranno. Installalo con: winget install Gyan.FFmpeg

REM Leggo .env: eol=# salta i commenti, delims== divide nome e valore.
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if not "%%b"=="" set "%%a=%%b"

if not defined PORT set PORT=8080
if not defined HOST set HOST=0.0.0.0

echo.
echo   Sito acceso. Sul PC:       http://localhost:%PORT%
echo   Dall'iPhone serve l'indirizzo di questo computer (vedi README).
echo.
.venv\Scripts\python.exe -m uvicorn server.main:app --host %HOST% --port %PORT% --proxy-headers --forwarded-allow-ips=*
pause
