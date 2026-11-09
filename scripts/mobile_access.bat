@echo off
chcp 65001 >nul
setlocal
title LiuHao Mobile Access
set "RC=0"
set "APP_ROOT=%~dp0.."
set "PYTHONPATH=%APP_ROOT%;%PYTHONPATH%"
set "PY=%~dp0..\runtime\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" goto usage

echo.
echo [LiuHao] 手机访问开关
echo   开启后会监听局域网中继端口；只有配对成功（一次性配对码，10 分钟有效）的手机能进入。
echo   请勿在公共 Wi-Fi 开启；不会开机自启，关闭请用「手机访问（关闭）」或 stop。
echo.
"%PY%" "%~dp0mobile_access.py" %*
set "RC=%ERRORLEVEL%"
goto end

:usage
echo 用法：mobile_access.bat start [--open-firewall] ^| stop ^| status ^| devices ^| pair ^| kick ^| kick-all
echo   首次开启建议：手机访问（开启并放行防火墙）只放行 TCP 8090（专用网络/本子网）。
set "RC=2"

:end
echo.
pause
exit /b %RC%
