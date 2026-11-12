#!/usr/bin/env bash
# Linux/macOS 启动器：与 Windows 的 start-liuhao.bat / `make run` 等价。
# 用法：./start-liuhao.sh [--backend-only|--vite|--no-browser|--https|--diagnose]
# 参数原样透传给 scripts/start_liuhao.py（入口与参数见该文件 argparse）。
# 需要先建好 .venv（仓库根）；脚本用仓库内 .venv/bin/python，不用系统 python。
cd "$(dirname "$0")"
exec "$(dirname "$0")/.venv/bin/python" scripts/start_liuhao.py "$@"
