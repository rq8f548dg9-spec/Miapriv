@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Bot trading - pannello di controllo

echo.
echo   ===================================
echo    BOT TRADING - pannello di controllo
echo   ===================================
echo.

REM Cerca Python: prima il launcher "py", poi "python".
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"

if not defined PY (
  echo   Python non risulta installato su questo computer.
  echo.
  echo   Scaricalo da https://www.python.org/downloads/
  echo   IMPORTANTE: durante l'installazione metti la spunta su
  echo   "Add python.exe to PATH", altrimenti Windows non lo trova.
  echo.
  echo   Poi chiudi questa finestra e fai di nuovo doppio clic qui.
  echo.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo   Prima configurazione: preparo l'ambiente, ci vuole un minuto.
  %PY% -m venv .venv || goto :errore
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :errore
  ".venv\Scripts\python.exe" -m pip install --quiet -e . || goto :errore
  echo   Ambiente pronto.
  echo.
)

if not exist ".env" copy ".env.example" ".env" >nul

echo   Apro il pannello nel browser: http://127.0.0.1:8777
echo   Per fermare tutto, chiudi questa finestra o premi Ctrl+C.
echo.
".venv\Scripts\python.exe" -m main --ui
if errorlevel 1 goto :errore
exit /b 0

:errore
echo.
echo   Qualcosa non ha funzionato: il messaggio qui sopra dice cosa.
echo   Se non si capisce, copialo e mandamelo.
echo.
pause
exit /b 1
