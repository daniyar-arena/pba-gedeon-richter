@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ПБА Gedeon Richter — сервер отчётов

set "PY=C:\Users\Arena\AppData\Local\Python\pythoncore-3.12-64\python.exe"
if not exist "%PY%" set "PY=python"

if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: создаю окружение, это займёт минуту...
  "%PY%" -m venv .venv || goto :fail
)

.venv\Scripts\python.exe -c "import fastapi, uvicorn, openpyxl, httpx, anthropic, multipart, dotenv" 2>nul
if errorlevel 1 (
  echo Доустанавливаю библиотеки...
  .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
)

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo Сайт открывается по адресу http://127.0.0.1:8765
echo Чтобы закрыть — просто закройте это окно.
echo.
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
goto :eof

:fail
echo.
echo Не получилось подготовить окружение. Скопируйте текст выше и покажите Клоду.
pause
