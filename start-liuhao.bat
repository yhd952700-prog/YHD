@echo off
rem LIUHAO X launcher - double click to start gateway (8080) + console (5173).
rem This file is ASCII-only on purpose (Windows batch mangles non-ASCII paths).
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\start_liuhao.py
echo.
echo LiuHao stopped. Press any key to close.
pause >nul
