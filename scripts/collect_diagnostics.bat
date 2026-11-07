@echo off
setlocal EnableExtensions
rem Offline diagnostics: works even when the gateway is not running.
cd /d "%~dp0.."
set "APP_DIR=%CD%"

if not defined LIUHAO_DATA_DIR if exist "%APP_DIR%\data-dir.txt" (
  for /f "usebackq delims=" %%D in ("%APP_DIR%\data-dir.txt") do set "LIUHAO_DATA_DIR=%%D"
)

set "PY=%APP_DIR%\runtime\python.exe"
if not exist "%PY%" set "PY=%APP_DIR%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "OUT=%USERPROFILE%\Desktop"
if not exist "%OUT%" set "OUT=%TEMP%"
set "TMPOUT=%TEMP%\liuhao_diag_out.txt"

echo.
echo Collecting offline diagnostics (gateway does not need to be running)...
echo.

"%PY%" -m src.desktop diagnose --out "%OUT%" --verify > "%TMPOUT%" 2>&1
if errorlevel 1 goto failed
set "ZIP="
set /p ZIP=<"%TMPOUT%"
if not exist "%ZIP%" goto failed
del "%TMPOUT%" >nul 2>&1

echo Diagnostics package created:
echo   %ZIP%
echo.
echo It contains no keys, tokens or credentials. Send this zip for support.
echo.
start "" explorer /select,"%ZIP%"
echo Press any key to close this window...
pause >nul
exit /b 0

:failed
echo.
echo Failed to create the diagnostics package.
echo Run this command manually to see the error:
echo   "%PY%" -m src.desktop diagnose --out "%OUT%" --verify
echo.
type "%TMPOUT%" 2>nul
del "%TMPOUT%" >nul 2>&1
pause
exit /b 1
