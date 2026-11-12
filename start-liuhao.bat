@echo off
rem LIUHAO X launcher - double click to start the gateway (single-port).
rem Paths and variable names in this file are ASCII-only on purpose
rem (Windows batch mangles non-ASCII paths). Chinese text appears only
rem in echo messages, same as the original file.

setlocal
rem Keep the program directory clean: no Python bytecode cache.
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"

rem 1) Locate a Python interpreter: project venv first, then system python.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if defined PY goto :have_py
where python >nul 2>nul
if errorlevel 1 goto :no_python
set "PY=python"
:have_py

rem 2) Create the project venv on first run, then install Python deps.
rem    If .venv already exists, skip this step so every start stays fast.
if exist ".venv\Scripts\python.exe" goto :have_venv
echo.
echo [INFO] 第一次运行，正在准备运行环境（只需要等这一次）...
echo        正在创建 .venv ...
"%PY%" -m venv .venv
if errorlevel 1 goto :venv_failed
set "PY=.venv\Scripts\python.exe"
echo        正在安装运行需要的组件（需要联网，请稍等几分钟）...
"%PY%" -m pip install -e .
if errorlevel 1 goto :pip_failed
:have_venv

rem 3) Check the console page (dist). If it is missing, try to build it.
if exist "apps\console\console\dist\index.html" goto :have_dist
echo.
echo [INFO] 控制台页面还没生成，正在自动生成（只需要等这一次）...
where node >nul 2>nul
if errorlevel 1 goto :no_node
where npm >nul 2>nul
if errorlevel 1 goto :no_node
cd /d "%~dp0apps\console\console"
call npm ci
if errorlevel 1 goto :build_failed
call npm run build
if errorlevel 1 goto :build_failed
cd /d "%~dp0"
if not exist "apps\console\console\dist\index.html" goto :build_failed
:have_dist

rem 4) First-run wizard: creates an admin account / picks a model when
rem    missing; exits at once when everything is already configured.
rem    Run it only on interactive starts (CI: call start_liuhao.py
rem    directly, or pass --skip to the wizard).
"%PY%" scripts\first_run_wizard.py
if errorlevel 1 (
    echo.
    echo [FAIL] 首次运行向导未通过，请按上方提示处理后重试。
    echo.
    pause
    exit /b 1
)

rem 5) Forward all arguments (e.g. --https, --backend-only) to the launcher.
"%PY%" scripts\start_liuhao.py %*
set "LH_CODE=%ERRORLEVEL%"
if not "%LH_CODE%"=="0" goto :lh_failed
rem Exit code 0 also covers "another instance is already running" (a second
rem double-click). Closing straight away is intended: do not leave a window
rem hanging behind a "press any key" prompt after a no-op start.
exit /b 0

:no_python
echo.
echo [FAIL] 没找到 Python，请先安装 Python 再双击一次。
echo.
echo        1. 打开浏览器，去 python.org 下载 Python 3.12。
echo        2. 安装时一定要勾选 Add to PATH（加到系统路径）。
echo        3. 装完后重新双击 start-liuhao.bat。
echo.
echo        （已经装过 Python 但还报这个错：重装时勾选 Add to PATH，
echo         或先把电脑重启一次再双击。）
echo.
pause
exit /b 1

:venv_failed
echo.
echo [FAIL] 创建运行环境失败（.venv）。
echo        请确认磁盘还有空间、文件夹可以写入，然后重新双击一次。
echo.
pause
exit /b 1

:pip_failed
echo.
echo [FAIL] 联网安装失败，请检查网络后重新双击一次。
echo        （比如：Wi-Fi 是否连上、公司网络是否要先登录认证。）
echo        已经装好的部分不用重装，重新双击会从断点继续。
echo.
pause
exit /b 1

:no_node
echo.
echo [FAIL] 没找到 Node.js，请先安装 Node.js 再双击一次。
echo.
echo        1. 打开浏览器，去 nodejs.org 下载 Node.js LTS（长期支持版）。
echo        2. 一路点“下一步”装完。
echo        3. 装完后重新双击 start-liuhao.bat。
echo.
echo        （Node.js 只在第一次生成控制台页面时用到，平时启动不需要它。）
echo.
pause
exit /b 1

:build_failed
cd /d "%~dp0"
echo.
echo [FAIL] 控制台页面生成失败，启动已停止（没有页面就没法打开控制台）。
echo        请检查上方 npm 输出的最后几行：
echo          - 如果是网络问题：检查网络后重新双击一次。
echo          - 如果是磁盘空间不足：清理出空间后重新双击一次。
echo        还是不行：把黑窗口里的最后一段文字发给技术支持。
echo.
pause
exit /b 1

:lh_failed
rem Keep the window open only on real failures so the reason stays readable.
echo.
echo [FAIL] Jarvis failed to start (exit code %LH_CODE%).
echo        See the output above / logs for details.
echo.
echo Press any key to close this window.
pause >nul
endlocal
exit /b %LH_CODE%
