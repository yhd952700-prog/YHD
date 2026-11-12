# LIUHAO — developer convenience targets (additive; does not replace the
# pyproject.toml-based build). Requires GNU make.
#
#   make hooks       Install the local pre-commit doc-lint hook
#   make lint-docs   Run the HC-01/HC-09/HC-10 audit-claim lint over all docs
#   make run         Start the gateway + console (OS picks its launcher)
#   make run-backend Start the gateway only (no console)
#   make test        Run the pytest suite with the repo venv if present
#
# The hook + lint guard enforce honest audit-subsystem claims in docs
# (HC-01 UNVERIFIED / HC-09·HC-10 VOLATILE). See CONTRIBUTING.md.

PYTHON ?= python

# --- OS selection (B组第二轮修复：此前 run/run-backend 把 Windows 路径
# `.venv/Scripts/python.exe` 写死，Linux/macOS 下 make run 直接报错）---
# Windows (nmake 不适用；Git Bash / MSYS2 下的 GNU make 会置 OS=Windows_NT)
# 走 start-liuhao.bat；其它系统走已有的 start-liuhao.sh（不另造启动器）。
# 两个启动器都用仓库内 .venv 的 Python，不用系统 Python。
ifeq ($(OS),Windows_NT)
LAUNCHER := start-liuhao.bat
VENV_PY := .venv/Scripts/python.exe
else
LAUNCHER := ./start-liuhao.sh
VENV_PY := .venv/bin/python
endif

# 测试用解释器：仓库 .venv 优先（依赖齐全），没有则回退到 python3。
PY := $(if $(wildcard $(VENV_PY)),$(VENV_PY),python3)

.PHONY: hooks lint-docs run run-backend test

hooks:
	@ROOT=$$(git rev-parse --show-toplevel); ln -sf "$$ROOT/scripts/pre-commit-lint.sh" "$$ROOT/.git/hooks/pre-commit"; chmod +x "$$ROOT/scripts/pre-commit-lint.sh"; echo "Installed LIUHAO pre-commit doc-lint hook (lints staged *.md before each commit)."

lint-docs:
	$(PYTHON) scripts/lint_audit_claims.py --all

# --- Local, non-Docker run (see docs/product/LOCAL-RUN.md) ---
# Boots the real FastAPI gateway via uvicorn; no Docker required.
run:
	@ROOT=$$(git rev-parse --show-toplevel); cd "$$ROOT" && $(LAUNCHER)

run-backend:
	@ROOT=$$(git rev-parse --show-toplevel); cd "$$ROOT" && $(LAUNCHER) --backend-only

test:
	$(PY) -m pytest tests/ -q
