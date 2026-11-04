@echo off
rem LIUHAO X launcher - double click to start the gateway (single-port).
rem This file is ASCII-only on purpose (Windows batch mangles non-ASCII paths).

setlocal
cd /d "%~dp0"

rem 1) Locate a Python interpreter: project venv first, then system python.
set "PY="
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set "PY=python"
    )
)

if not defined PY (
    echo.
    echo [FAIL] 没找到 Python。
    echo        请安装 Python 3.11+ 并确保它在 PATH 里，或在仓库根执行：
    echo          python -m venv .venv
    echo          .venv\Scripts\python.exe -m pip install -e .
    echo.
    echo        安装完成后双击本脚本即可。
    echo.
    pause
    exit /b 1
)

rem 2) 首次运行向导：检测到无管理员身份/无模型时引导补齐；已配置则秒退。
rem    只在交互启动时跑（CI 请直接调 start_liuhao.py 或传 --skip）。
"%PY%" scripts\first_run_wizard.py
if errorlevel 1 (
    echo.
    echo [FAIL] 首次运行向导未通过，请按上方提示处理后重试。
    echo.
    pause
    exit /b 1
)

rem 3) Forward all arguments (e.g. --https, --backend-only) to the launcher.
"%PY%" scripts\start_liuhao.py %*

rem 4) Keep the window open so the user can read startup output / errors.
echo.
echo LiuHao stopped. Press any key to close.
pause >nul
endlocal
