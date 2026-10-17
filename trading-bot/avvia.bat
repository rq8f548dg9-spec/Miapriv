@echo off
REM Avvia il pannello di controllo. Al primo lancio prepara tutto da solo.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python non trovato. Installalo da https://www.python.org/downloads/ e riprova.
  pause
  exit /b 1
)

if not exist .venv (
  echo Prima configurazione: preparo l'ambiente ^(un minuto circa^)...
  python -m venv .venv
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -e .
)

if not exist .env copy .env.example .env >nul

echo Apro il pannello nel browser...
.venv\Scripts\python -m main --ui
pause
