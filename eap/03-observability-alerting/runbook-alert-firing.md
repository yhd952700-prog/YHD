# 03 · Runbook — Observability Closed-Loop Firing

**Primary script:** `scripts/verify_observability_loop.py`
**Companion (static proof):** `scripts/verify_alert_rules.py`
**What it proves:** the *real* alert pipeline actually **FIRES** — not just that
the app answers `/v1/metrics/prometheus` with 200. The chain under test:

```
application → exporter → collector → storage → alert rule → ACTUAL FIRING → operator visibility
```

Six independently-proven links: **L1** exporter 200 · **L2** rule hygiene
(referenced metrics are really emitted) · **L3** collector target health `up` ·
**L4** storage returns a sample · **L5** `state=="firing"` in Prometheus
`/api/v1/alerts` · **L6** Alertmanager `/api/v2/alerts` holds it (+ optional
webhook sink receives real bytes).

---

## A. Confirmed flags (grep of `argparse` in the script)

| Flag | Default | Meaning |
|------|---------|---------|
| `--mode` | `external` | `external` (running stack) · `local` (native binaries) · `docker` (compose stack) |
| `--app-url` | `http://127.0.0.1:8010` | where the app's exporter lives |
| `--prom-url` | `http://127.0.0.1:9090` | Prometheus base URL |
| `--am-url` | `http://127.0.0.1:9093` | Alertmanager base URL |
| `--bin-dir` | `""` | **required for `--mode local`**: dir holding `prometheus.exe`/`alertmanager.exe` |
| `--rules` | `configs/observability/alert_rules.yml` | the closed-loop probe rule file |
| `--job` | `liuhao-ai-os` | Prometheus scrape job name to assert `up` |
| `--probe-metric` | `liuhao_executor_fence_installed` | metric used for the storage link |
| `--force` | `404` | `404` (app UP, fire `LoopProbeHttp404Burst`) · `down` (app stopped, fire `ServiceDown`/`LiuhaoServiceDown`) |
| `--force-count` | `10` | 404 requests issued when `--force 404` (the rule fires at ≥5 in 2m) |
| `--sink-port` | `18010` | webhook sink port (the `.local.yml` routes to `127.0.0.1:18010`) |
| `--timeout` | `180.0` | seconds to wait for firing + visibility |
| `--keep-running` | off | leave locally-started processes running |
| `--json` | `""` | write the machine-readable report here |

**Exit codes:** `0` = every evaluated link passed · `1` = a link missing (loop
NOT verified) · `2` = environment blocker (binary / daemon / config missing).

---

## B. Recommended flow — two runs (app UP, then app DOWN)

The probe group in `configs/observability/alert_rules.yml` has two forcing
conditions. **They need two separate runs** because `--force down` SKIPs L1/L2/L4
(by design — the app is stopped) and those must be proven by the `--force 404`
run.

### Run 1 — app UP, fire `LoopProbeHttp404Burst`
```bash
python scripts/verify_observability_loop.py \
  --mode local \
  --bin-dir /path/to/prom_am_bin \
  --app-url http://127.0.0.1:8010 \
  --force 404 --force-count 10 \
  --json eap/05-final-readiness-battery/obs_loop_up_report.json
```
- The script copies `infra/observability/prometheus.local.yml`,
  `alert_rules.yml`, `alertmanager.local.yml` into a temp workdir and launches
  `prometheus.exe` + `alertmanager.exe` from `--bin-dir`.
- It issues ≥5 real 404s to the app, then waits for `LoopProbeHttp404Burst`
  (`sum(increase(http_requests_total{status="404"}[2m])) >= 5`) to reach
  `state=="firing"`.
- Asserts **L5 FIRING** and **L6 VISIBILITY** (Alertmanager holds it), and
  prints whether the webhook sink at `127.0.0.1:18010` received real bytes.

### Run 2 — app DOWN, fire `ServiceDown` / `LiuhaoServiceDown`
```bash
# Stop the app first (so /v1/health does NOT answer), then:
python scripts/verify_observability_loop.py \
  --mode local \
  --bin-dir /path/to/prom_am_bin \
  --app-url http://127.0.0.1:8010 \
  --force down \
  --json eap/05-final-readiness-battery/obs_loop_down_report.json
```
- Expects `up{job="liuhao-ai-os"} == 0` → `LoopProbeServiceDown` and
  `LiuhaoServiceDown` reach `firing`.

> **Port note.** `infra/observability/prometheus.local.yml` hardcodes the app
> target as `127.0.0.1:8010`. If you are reusing the **G9-built container**
> published on host port `18000`, do **not** use `--mode local` as-is — instead
> run `--mode external` with a Prometheus/Alertmanager that scrapes `18000`
> (`--app-url http://127.0.0.1:18000 --prom-url … --am-url …`), or edit the
> local config's target to `18000`. The 8010 target only matches an app
> listening on 8010.

---

## C. Dead-rule proof (static, no daemon needed)

`scripts/verify_alert_rules.py` AST-scans `src/` for each alert `expr`'s real
emit point and flags rules that can never fire (e.g. a metric declared but
never incremented). It is wired into CI guardrails.

```bash
python scripts/verify_alert_rules.py      # exit 0 = green, 1 = a dead rule found
```
It also validates that every non-infra scrape job's `metrics_path` is a route
that really exists in `src/gateway` (this is what caught the old `/metrics`
vs `/v1/metrics/prometheus` 404), and that every `rule_files` glob resolves.

---

## D. ⚠️ Honest disclosure — production alerting is NOT delivery-ready

The **production** Alertmanager config is `configs/alertmanager/alertmanager.yml`
(note: it lives under `configs/alertmanager/`, **not** `configs/observability/`).
It has **no deliverable sink**:

- Its only receivers are `email_config` to **fake `.local` domains**
  (`alerts@liuhao-ai-os.local`, `dev-team@liuhao-ai-os.local`,
  `on-call@liuhao-ai-os.local`) — these never deliver anywhere real.
- It carries an **invalid top-level `alerts:` block** (an Alertmanager config
  has no such key; Alertmanager silently ignores it). So the "alerts" defined
  there (`ProviderFailure`, `HighLatency`, `ErrorRateHigh`) are inert dead
  config, not real rules.
- There is **no real webhook sink** in production config.

The closed-loop *proof* in this runbook uses the **`.local.yml` verification
harness** (`infra/observability/alertmanager.local.yml`) which routes to a real
webhook sink at `127.0.0.1:18010`. **That sink exists only for the
verification run** — it is not production alerting. Flipping L6 in a
verification run does **not** mean production operators will be paged. This gap
must be closed (real receiver + valid rule file) before any production claim.

> Also: the application's alert **webhook is default off** — Alertmanager
> pull-scrapes Prometheus; the app does not push.

---

## E. Expected output (a passing `--force 404` run)

```
[PASS] L1_EXPORTER     exporter live: 5 metric families, N bytes
[PASS] L2_RULE_HYGIENE <referenced metrics> all resolve
[PASS] L3_COLLECTOR    health=up scrapeUrl=... lastError=None
[PASS] L4_STORAGE     1 series, e.g. ... = 1
[PASS] L5_FIRING      state=firing for LoopProbeHttp404Burst
[PASS] L6_VISIBILITY  Alertmanager holds LoopProbeHttp404Burst
[PASS] L6_SINK        webhook sink received a real delivery for LoopProbeHttp404Burst
VERDICT: VERIFIED (all links in a single run)
```
A `--force down` run shows `L1_EXPORTER SKIP`, `L2_RULE_HYGIENE SKIP`,
`L4_STORAGE SKIP`, and `VERDICT: PARTIAL RUN - firing + visibility verified,
but L1, L2, L4 were not evaluated in this run`.
