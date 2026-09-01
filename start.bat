@echo off
rem Vazhno: tekst tolko latinicej. cmd.exe chitaet .bat pobajtno, i kirillica vnutri
rem lomaet razbor strok (stroki nachinajut vypolnjatsja s serediny). Vse russkie
rem podskazki - na samom sajte i v README.md.
cd /d "%~dp0"

set "PY=C:\Users\Arena\AppData\Local\Python\pythoncore-3.12-64\python.exe"
if not exist "%PY%" set "PY=python"

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating environment, takes about a minute...
  "%PY%" -m venv .venv
  if errorlevel 1 goto fail
)

.venv\Scripts\python.exe -c "import fastapi, uvicorn, openpyxl, httpx, anthropic, multipart, dotenv" 2>nul
if errorlevel 1 (
  echo Installing libraries...
  .venv\Scripts\python.exe -m pip install --disable-pip-version-check -q -r requirements.txt
  if errorlevel 1 goto fail
)

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo   PBA Gedeon Richter
echo   Site: http://127.0.0.1:8765
echo   Close this window when you are done.
echo.
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
echo.
echo Server stopped. If there is an error above, copy it and show it to Claude.
pause
goto end

:fail
echo.
echo Setup failed. Copy the text above and show it to Claude.
pause

:end
