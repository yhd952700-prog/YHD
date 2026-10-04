# EAP-04 — Full regression: failure interpretation

Read this **before** concluding that "the product is broken". Several exit codes
mean "the environment is insufficient", not "the code regressed".

---

## First branch: which exit code did you get?

### Exit `2` — NOT VERIFIED (environment gap, **not** a product failure)

| Message you'll see | What it actually means | What to do |
|--------------------|------------------------|------------|
| `pytest is not importable by interpreter ...` | The interpreter you passed cannot import pytest. Common when using the managed/global python instead of the repo `.venv`. | Re-run with the venv python, e.g. `--python .venv/bin/python` |
| `tests root does not exist: ...` | `--repo-root`/`--tests-root` are wrong. | Pass `--repo-root` explicitly |
| `collect-only run: nothing was executed` | You asked for `--collect-only`. This is intentional. | Drop `--collect-only` to actually execute |

Do **not** report exit `2` as a failure of LIUHAO. It means "we do not know".

---

### Exit `1` — FAIL (something real)

Open `regression-report.json` and look at the `chunks[]` entries where
`"status": "FAIL"`, then read the `tail` field for that chunk.

| Symptom | Most likely cause | Interpretation |
|---------|-------------------|----------------|
| A chunk reports `chunk collected 0 tests` | Collection error (import failure, syntax error, missing plugin such as `mcp`) | **Real problem.** Read `tail`. Note: some modules legitimately fail to collect when an optional dependency is absent — that must be diagnosed explicitly, not waved away |
| Many chunks fail with import errors | Not run from the repo root, or the wrong interpreter | Re-run from repo root with the venv python |
| Chunk exceeded timeout | That area is genuinely slow under this machine's load | Raise `--timeout`; confirm it is slowness, not a hang |
| **Order-dependent** failure (passes alone, fails in suite) | Shared mutable state — most often a bare `os.environ[...] = ` assignment that is never restored | This class of bug already bit the policy-approval tests once. **Treat it as a real defect in the failing test, not as flakiness** |
| Consistent failure in sovereignty / policy-approval area | Could be the real capability breaking | Cross-check against the 107-test baseline before touching other areas |

---

## Triage order (recommended)

1. Identify the **specific** failing chunks in the JSON, not just "the suite failed".
2. For each, read its `tail` (pytest's own last lines) and `failing_examples`.
3. Re-run that single chunk standalone to establish whether it is order-dependent:
   `python -m pytest <target> -q`
4. Only then decide whether it is a product regression or a test-hygiene defect.

---

## Anti-patterns to avoid

- ❌ "The suite was red, but it's probably flaky." → Prove it by isolating the chunk.
- ❌ Relaxing an assertion to make the suite green. → This project's core discipline is never weakening a check to pass a gate.
- ❌ Treating `skipped` as `passed`. → The battery explicitly downgrades an all-skipped gate to NOT VERIFIED.
- ❌ Running `pytest` concurrently with another pytest process. → Known to corrupt results here. Use this runner's chunking, not parallel invocations.
