@echo off
rem LiuHao AI OS / 鎏灏智能中枢 —— 一键信任自签名代码签名证书
rem 双击运行即可（只信任当前 Windows 用户，不需要管理员权限）。
rem 如需信任所有用户：右键此文件 →「以管理员身份运行」后，再执行
rem   powershell -ExecutionPolicy Bypass -File "%~dp0trust-liuhao-signing.ps1" -AllUsers

chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0trust-liuhao-signing.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo 完成：证书已导入信任。
) else (
  echo 未完成：退出码 %RC%，请查看上方中文原因。
)
echo.
pause
exit /b %RC%
