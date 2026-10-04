# EAP-03 — Observability & alerting: failure interpretation

Diagnose by **which link failed**, not by "it went red". The failing link tells
you exactly where the pipeline broke.

---

## By link

| Failing link | Meaning | Likely cause / next step |
|--------------|---------|--------------------------|
| **L1_EXPORTER** | App isn't exposing metrics at all | Confirm the app is actually running and `LIUHAO_PROMETHEUS_METRICS` is enabled (the route 404s when disabled) |
| **L2_RULE_HYGIENE** | An alert rule references a metric that is never emitted | A **dead rule**. Either add the real emit site, or delete the rule. `scripts/verify_alert_rules.py` pinpoints which |
| **L3_COLLECTOR** | Prometheus isn't scraping successfully | Wrong target/port in the Prometheus config, or DNS/networking. The production config targets `api:8080`, which only resolves inside the compose network |
| **L4_STORAGE** | No sample for `liuhao_executor_fence_installed` | The executor fence did not install at startup, or the scrape hasn't happened yet (allow ≥ the 15s scrape interval) |
| **L5_FIRING** | Rule loaded but never fired | The forced condition didn't actually occur: too few 404s, `for:` duration not elapsed, or the expression is unsatisfiable. Re-check `--force-count` and allow ≥ one evaluation interval (15s) after the burst |
| **L6_VISIBILITY** | Fired but Alertmanager didn't route it, or the sink received nothing | Alertmanager not pointed at by Prometheus, sink port wrong/firewalled, or the production config's email-only route silently dropped it |

---

## By exit code

| Code | Meaning | Honest verdict |
|------|---------|----------------|
| `0` | all links proven | PASS — the pipeline genuinely fires |
| `1` | a link is missing | **Real failure.** Investigate using the table above |
| `2` | environment cannot run it | NOT VERIFIED — a gap, not a failure, and not a pass |

---

## Common environment traps (seen concretely)

1. **"Metrics endpoint answers 200, so we're done."**
   No. That's L1 only. Without L5/L6 you have proven the exporter exists and
   nothing else. Do **not** write "observability verified" on the strength of a
   200 response.

2. **Port mismatch (a real gotcha already found in this repo).**
   `configs/observability/prometheus.yml` previously scraped
   `metrics_path: '/metrics'` at `liuhao-ai-os:8000`, while the real route is
   `/v1/metrics/prometheus` and the container listens on **8080**
   (`Dockerfile` sets `ENV PORT=8080`). Symptom: target `DOWN` / HTTP 404.
   That misconfiguration was corrected; verify your deployment matches.

3. **Wrong network.** The production Prometheus config targets the compose
   service name `api:8080`. Running it outside that network yields
   `no such host`. Use the local/external configs for standalone proof.

4. **`--force down` looks like it "failed" L1/L2/L4.**
   It did not. With the app stopped there is nothing to scrape; those links are
   *legitimately skipped*. The `--force 404` run is what proves L1/L2/L4.

5. **Impatience.** Prometheus evaluates every **15s**. If you assert
   immediately after forcing the condition, you will see "not firing" and
   wrongly conclude failure. Wait ≥ one evaluation cycle plus the rule's `for:`.

---

## What NOT to conclude from a failure here

- ❌ "LIOHAO's observability is fake." → Usually it is a link-level config issue.
- ❌ "Production alerting is broken." → Distinguish the *verification* pipeline
  from the *production* alertmanager config; the production sink gap is
  separately disclosed in `expected-results.md`.
- ❌ Ignore a red and re-run until green. → A properly failing gate is the gate
  doing its job. Diagnose it.
