# EAP-03 — Observability & alerting: expected results

This package proves one specific claim:

> **The real Prometheus → Alertmanager pipeline actually fires an alert** —
> not merely that `/v1/metrics/prometheus` answers HTTP 200.

`HTTP 200 from a metrics endpoint` proves the exporter exists. It proves nothing
about rules, evaluation, firing, or operator visibility. The six links below are
the minimum honest proof.

Confirmed flags were read from `scripts/verify_observability_loop.py` itself —
`--mode {external,local,docker}`, `--app-url`, `--prom-url`, `--am-url`,
`--bin-dir`, `--rules`, `--job`, `--probe-metric`, `--force {404,down}`,
`--force-count`, `--sink-port`, `--timeout`, `--keep-running`, `--json`.

---

## The six links (L1–L6) and what "pass" means for each

| Link | Check | PASS condition |
|------|-------|----------------|
| **L1_EXPORTER** | `GET {app}/v1/metrics/prometheus` | HTTP 200 **and** body parses as Prometheus text |
| **L2_RULE_HYGIENE** | every metric referenced by a rule actually appears in a live scrape | no dead rules |
| **L3_COLLECTOR** | Prometheus `GET /api/v1/targets` | target `job="liuhao-ai-os"` has `health == "up"` |
| **L4_STORAGE** | `GET /api/v1/query?query=liuhao_executor_fence_installed` | a real sample is returned |
| **L5_FIRING** | Prometheus `GET /api/v1/alerts` | the target alert's `state == "firing"` |
| **L6_VISIBILITY** | Alertmanager `GET /api/v2/alerts` (+ webhook sink) | Alertmanager holds the alert **and** the sink received real bytes |

**The acceptance threshold is L5 AND L6 together.** Anything weaker is recorded
as NOT VERIFIED. A rule merely *loaded* is not a rule that *fired*.

The alert used for the app-UP proof is **`LoopProbeHttp404Burst`**, fired by
sending real 404 requests so `http_requests_total{status="404"}` crosses the
rule threshold. For the app-DOWN proof it is **`LoopProbeServiceDown`** /
**`LiuhaoServiceDown`**.

---

## Run A — app UP, 404 burst (the primary proof)

```bash
python scripts/verify_observability_loop.py --mode local \
  --bin-dir /path/to/prometheus_and_alertmanager \
  --app-url http://127.0.0.1:8010 \
  --force 404 \
  --json eap/05-final-readiness-battery/obs_loop_up_report.json
```

Expected: all six links `PASS`, exit `0`.

## Run B — app DOWN (proves ServiceDown fires)

```bash
python scripts/verify_observability_loop.py --mode local \
  --bin-dir /path/to/prometheus_and_alertmanager \
  --app-url http://127.0.0.1:8010 \
  --force down \
  --json eap/05-final-readiness-battery/obs_loop_down_report.json
```

Expected: `ServiceDown` fires. **Note honestly:** with the app stopped, L1/L2/L4
are legitimately **skipped** — there is nothing to scrape. That is expected, not
a failure, and the `--force 404` run is what proves those links.

## Run C — static dead-rule proof (no daemon needed)

```bash
python scripts/verify_alert_rules.py
```

This AST-scans `src/` for the real emit point of every metric referenced by an
alert `expr`, and fails if any rule can never fire. It already caught three
genuinely unreachable rules (`provider_up`, `task_queue_depth`,
`agent_coordination_failures_total`) and is wired into CI `guardrails`.

---

## Exit codes

Same convention as the rest of the EAP:

| Code | Meaning |
|------|---------|
| `0` | all links proven end-to-end |
| `1` | a link is genuinely missing → investigate |
| `2` | environment cannot execute the check → recorded gap |

---

## Honest gaps you MUST read alongside any PASS

A green L1–L6 run proves **the verification pipeline fires**. It does **not**
prove an operator would see it in production. Disclosed, real gaps:

1. **Production Alertmanager has no deliverable sink.**
   `configs/alertmanager/alertmanager.yml` emails fake `.local` domains and
   carries an **invalid top-level `alerts:` block** that Alertmanager silently
   ignores. No real webhook/PagerDuty route exists. So in production today an
   alert can fire and still reach nobody.
2. **The webhook sink used by L6 is verification-only** — it exists in
   `infra/observability/alertmanager.local.yml` at `127.0.0.1:18010`, not in the
   production config.
3. **The app does not push alerts itself**; Alertmanager pull-scrapes Prometheus.
   The in-app `AlertStore` engine has an optional webhook that is **off by
   default** (`LIUHAO_ALERT_WEBHOOK` unset). No retry queue exists — the alert
   JSON file is effectively its own dead letter.
4. **Two independent alerting chains exist** and their metric names do not
   overlap (Prometheus external vs. the in-app `AlertManager` engine). Do not
   cite evidence from one as proof about the other.
5. **The closed-loop docker stack has never been run** in this environment.

So: a PASS here means *"the pipeline can be proven to fire"*. It does not mean
*"production alerting is operationally complete"*. Say both, always.
