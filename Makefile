# LIUHAO — developer convenience targets (additive; does not replace the
# pyproject.toml-based build). Requires GNU make.
#
#   make hooks       Install the local pre-commit doc-lint hook
#   make lint-docs   Run the HC-01/HC-09/HC-10 audit-claim lint over all docs
#
# The hook + lint guard enforce honest audit-subsystem claims in docs
# (HC-01 UNVERIFIED / HC-09·HC-10 VOLATILE). See CONTRIBUTING.md.

PYTHON ?= python

.PHONY: hooks lint-docs

hooks:
	@ROOT=$$(git rev-parse --show-toplevel); ln -sf "$$ROOT/scripts/pre-commit-lint.sh" "$$ROOT/.git/hooks/pre-commit"; chmod +x "$$ROOT/scripts/pre-commit-lint.sh"; echo "Installed LIUHAO pre-commit doc-lint hook (lints staged *.md before each commit)."

lint-docs:
	$(PYTHON) scripts/lint_audit_claims.py --all
