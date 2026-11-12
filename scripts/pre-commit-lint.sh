#!/usr/bin/env bash
# scripts/pre-commit-lint.sh
#
# Optional local pre-commit hook for LIUHAO. Lints STAGED markdown docs for
# HC-01 / HC-09 / HC-10 audit claim caveats before a commit is created.
#
# Install (one-time, local only — never committed to the repo):
#   ln -s "$(git rev-parse --show-toplevel)/scripts/pre-commit-lint.sh" \
#        "$(git rev-parse --show-toplevel)/.git/hooks/pre-commit"
#   chmod +x "$(git rev-parse --show-toplevel)/scripts/pre-commit-lint.sh"
#
# Behaviour:
#   * If no markdown docs are staged, exits 0 (skip).
#   * Otherwise runs scripts/lint_audit_claims.py on the staged .md files.
#   * Propagates the linter's exit code (1 = un-caveated claim found -> block).
#
# This is the SAME guard that runs in CI (.github/workflows/doc-lint.yml); the
# hook just lets authors catch the violation before pushing.

set -euo pipefail

# Resolve repo root even when invoked from a subdir.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  echo "pre-commit-lint: not inside a git work tree; skipping." >&2
  exit 0
fi
cd "$REPO_ROOT"

# Collect staged markdown docs (Added/Copied/Modified/Renamed).
mapfile -t FILES < <(git diff --cached --name-only --diff-filter=ACMR -- '*.md' 2>/dev/null || true)

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "pre-commit-lint: no staged markdown docs; skipping."
  exit 0
fi

echo "pre-commit-lint: scanning ${#FILES[@]} staged markdown file(s) for HC-01/HC-09/HC-10 claim caveats..."
# Quoting "${FILES[@]}" preserves spaces in paths.
python scripts/lint_audit_claims.py "${FILES[@]}"
# Exit code is propagated by `set -e`.
