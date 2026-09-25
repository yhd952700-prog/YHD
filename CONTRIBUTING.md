# Contributing to LIUHAO

## Pre-commit hook (recommended)

LIUHAO enforces honest audit-subsystem claims in documentation via a lightweight
lint guard (`scripts/lint_audit_claims.py`). Install the local pre-commit hook so
it runs automatically on every commit:

```bash
make hooks
```

or manually:

```bash
ln -s "$(git rev-parse --show-toplevel)/scripts/pre-commit-lint.sh" \
      "$(git rev-parse --show-toplevel)/.git/hooks/pre-commit"
chmod +x "$(git rev-parse --show-toplevel)/scripts/pre-commit-lint.sh"
```

The hook lints every **staged `*.md`** file before a commit and blocks the commit
if an un-caveated audit claim is found. The same guard also runs in CI
(`.github/workflows/doc-lint.yml`) on every PR that touches `docs/**/*.md`, so the
hook is a fast local check, not a substitute for the gate.

## Documentation claim rules (HC-01 / HC-09 / HC-10)

The guard enforces two red lines (see `docs/autonomous/GOVERNANCE.md` §7 and the
D19/D20 decision):

### HC-01 — `src/kernels/audit` (authoritative audit sink)
Designed tamper-evident (SHA-256 hash chain), but its **runtime** chain-of-custody
integrity is currently **UNVERIFIED** (a forked chain was found: 284 broken joins /
169 duplicate-seq rows, pending F1–F6 remediation + independent verification).
Never assert it is tamper-proof / a verified true evidence source / authoritative
**without** pairing the claim with the `UNVERIFIED` / forked-chain caveat.

- ✅ Good: *"designed tamper-evident; runtime integrity currently UNVERIFIED —
  forked chain, pending F1–F6 + independent verification"*
- ❌ Bad: *"tamper-proof audit"* / *"the true evidence source"*

### HC-09 — `src/security/audit_logger.py` (CryptoAuditLogger) and
### HC-10 — `src/security/audit_policy.py` (AuditKernel)
Both are **VOLATILE / IN-MEMORY ONLY / NON-AUTHORITATIVE** by the D19/D20 decision.
Their in-memory hash chains are not persisted and not audit-grade; the high-water
/ dropped-count counters are telemetry, not evidence. Never describe them as
durable / persistent / authoritative / evidence-grade **without** the
`VOLATILE` / `NON-AUTHORITATIVE` / `IN-MEMORY ONLY` caveat.

- ✅ Good: *"CryptoAuditLogger is in-memory only, VOLATILE / NON-AUTHORITATIVE —
  not persisted, not audit-grade; telemetry, not evidence"*
- ❌ Bad: *"durable crypto audit"* / *"authoritative audit evidence"*

## Running the lint manually

```bash
python scripts/lint_audit_claims.py --all          # full docs tree (baseline)
python scripts/lint_audit_claims.py path/to/x.md   # explicit file(s)
python scripts/lint_audit_claims.py                # CI mode: changed docs vs HEAD
```

The guard fails (exit 1) when a doc introduces an HC-01 / HC-09 / HC-10 claim
without the required caveat, within a `±1` line window (override with `--window`).
See `python scripts/lint_audit_claims.py` header for the full pattern reference.
