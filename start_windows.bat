@echo off
cd /d "%~dp0"
if not exist .venv (
  echo First run: setting up Python environment...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
)
rem Re-install packages whenever requirements.txt changed (new versions add packages)
fc /b requirements.txt .venv\installed_requirements.txt >nul 2>&1
if errorlevel 1 (
  echo Installing / updating packages...
  .venv\Scripts\python -m pip install -r requirements.txt && copy /y requirements.txt .venv\installed_requirements.txt >nul
)
.venv\Scripts\python app\server.py
pause
