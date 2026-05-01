@echo off
cd /d "%~dp0"
set "TASE_PORT=5050"
set "PYTHON_EXE=C:\Users\danie\AppData\Local\Programs\Python\Python312\python.exe"

echo Starting TASE Screener on http://localhost:%TASE_PORT%
echo.

if exist "%PYTHON_EXE%" (
  "%PYTHON_EXE%" app.py
) else (
  python app.py
)
pause
