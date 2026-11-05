# Known gaps

Facts about this checkout that are true today and that we are **not** fixing
now. Each entry says what is broken, how we know, what it affects, and what
would close it. No "should be fine" entries — if it is unverified, it says so.

| # | Gap | Impact | Status |
|---|-----|--------|--------|
| 1 | Local `semgrep` CLI cannot start | Development tooling only | Open, accepted |

---

## 1. Local semgrep CLI is unusable because of a `mcp` version conflict

**What happens.** Running `semgrep` from this checkout's virtualenv fails at
import/startup: semgrep pulls a `mcp` dependency range that conflicts with the
`mcp>=2.0.0,<3.0.0` pin the product now needs for its MCP adapter (installed:
`mcp==2.1.1`). Either the CLI resolves against the wrong `mcp`, or it refuses to
install the version it wants without downgrading the product dependency.

**Why we are not fixing it.** The conflict is inside the development tool's
dependency closure, not in the shipped application. Resolving it by changing
`pyproject.toml` would mean downgrading `mcp` below the range the MCP adapter
(FastMCP 4.0.3) requires — the product would lose working MCP support to
satisfy a local linter. That trade is wrong, so semgrep stays out of this
checkout's virtualenv.

**Impact.** None on the product at runtime. Static analysis with semgrep has to
run from a separate tool environment instead of this venv.

**How to close it if it matters later.** Install semgrep in an isolated
environment (`uvx semgrep` / a dedicated venv) and point it at the source tree;
or wait for a semgrep release whose `mcp` range includes 2.x.

**Evidence.** `pip install -r requirements.txt` succeeds and the test suite
passes with `mcp==2.1.1` installed; the failure only appears when semgrep is
added to the same environment.
