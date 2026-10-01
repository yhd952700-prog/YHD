#!/usr/bin/env python
"""Verify the FULL observability closed loop, not just "the app answers 200".

Chain under test
----------------
    application -> exporter -> collector -> storage -> alert rule
                -> ACTUAL FIRING -> operator visibility

Links and how each is proven (no link is inferred from another):

  L1_EXPORTER      GET {app}/v1/metrics/prometheus returns 200 with a parsable
                   Prometheus text payload. This is the ONLY link that
                   "HTTP 200" proves; everything below is separate.
  L2_RULE_HYGIENE  Every metric referenced by
                   configs/observability/alert_rules.yml exists in the LIVE
                   scrape. A rule on a metric that is never emitted can never
                   fire, and would make every later link fake.
  L3_COLLECTOR     Prometheus /api/v1/targets reports the liuhao-ai-os target
                   health == "up" (i.e. a real scrape succeeded).
  L4_STORAGE       A metric the app exports is queryable from Prometheus
                   /api/v1/query with real samples.
  L5_FIRING        After a forcing condition, /api/v1/alerts shows the expected
                   alert with state == "firing". A rule that is merely *loaded*
                   is explicitly NOT accepted as proof.
  L6_VISIBILITY    Alertmanager /api/v2/alerts contains that same alert, and
                   (if a webhook sink is running) the sink captured a real
                   delivery payload.

Exit codes
---------
  0  every evaluated link passed
  1  at least one link is missing -> the loop is NOT VERIFIED
  2  environment blocker (binary / docker daemon / config missing)

Usage
-----
  # Prometheus + Alertmanager already running (docker or otherwise):
  python scripts/verify_observability_loop.py --mode external --force 404

  # Prometheus + Alertmanager started as native processes by this script:
  python scripts/verify_observability_loop.py --mode local --bin-dir <dir>

  # Docker Compose stack from infra/observability/docker-compose.yml:
  python scripts/verify_observability_loop.py --mode docker

  # Second phase: app deliberately stopped -> proves ServiceDown really fires.
  # L1/L2/L4 are reported SKIPPED in this mode and MUST be proven by a separate
  # --force 404 run; this script never pretends otherwise.
  python scripts/verify_observability_loop.py --mode external --force down

Stdlib only, so it runs under the managed venv with no extra install.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RULES = os.path.join(REPO_ROOT, "configs", "observability", "alert_rules.yml")
LOCAL_PROM_CFG = os.path.join(REPO_ROOT, "infra", "observability", "prometheus.local.yml")
LOCAL_AM_CFG = os.path.join(REPO_ROOT, "infra", "observability", "alertmanager.local.yml")
COMPOSE_FILE = os.path.join(REPO_ROOT, "infra", "observability", "docker-compose.yml")

DEFAULT_APP = "http://127.0.0.1:8010"
DEFAULT_PROM = "http://127.0.0.1:9090"
DEFAULT_AM = "http://127.0.0.1:9093"
DEFAULT_SINK_PORT = 18010

# Alert that must fire while the app is UP (forced with real 404 traffic).
UP_ALERT = "LoopProbeHttp404Burst"
# Alerts that must fire when the app is deliberately stopped.
DOWN_ALERTS = ("LoopProbeServiceDown", "LiuhaoServiceDown")

# PromQL keywords/functions that must not be mistaken for metric names.
PROMQL_NOISE = {
    "abs", "absent", "absent_over_time", "and", "avg", "avg_over_time", "bool",
    "bottomk", "by", "ceil", "changes", "clamp", "clamp_max", "clamp_min",
    "count", "count_over_time", "day_of_month", "day_of_week", "days_in_month",
    "delta", "deriv", "exp", "floor", "for", "group_left", "group_right",
    "histogram_quantile", "holt_winters", "hour", "idelta", "ignoring",
    "increase", "irate", "label_join", "label_replace", "last_over_time", "ln",
    "log2", "log10", "max", "max_over_time", "min", "min_over_time", "minute",
    "month", "offset", "on", "or", "predict_linear", "present_over_time",
    "quantile", "quantile_over_time", "rate", "resets", "round", "scalar",
    "sgn", "sort", "sort_desc", "sqrt", "stddev", "stddev_over_time", "stdvar",
    "stdvar_over_time", "sum", "sum_over_time", "time", "timestamp", "topk",
    "unless", "vector", "without", "year",
}

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
def http_json(url: str, timeout: float = 10.0) -> Tuple[bool, Any, str]:
    """GET a JSON document. Returns (ok, payload_or_None, human_note)."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return True, json.loads(raw), "HTTP %s" % resp.status
    except urllib.error.HTTPError as exc:
        return False, None, "HTTP %s" % exc.code
    except Exception as exc:  # noqa: BLE001 - blocker text is the point
        return False, None, "%s: %s" % (type(exc).__name__, exc)


def http_text(url: str, timeout: float = 10.0) -> Tuple[bool, str, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return True, raw, "HTTP %s" % resp.status
    except urllib.error.HTTPError as exc:
        return False, "", "HTTP %s" % exc.code
    except Exception as exc:  # noqa: BLE001
        return False, "", "%s: %s" % (type(exc).__name__, exc)


class SinkHandler(http.server.BaseHTTPRequestHandler):
    """Captures Alertmanager webhook deliveries (operator visibility)."""

    received: List[Any] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        try:
            SinkHandler.received.append(json.loads(body))
        except Exception:  # noqa: BLE001 - keep the raw body as evidence
            SinkHandler.received.append({"_unparsed": body})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt: str, *args: Any) -> None:
        pass


def start_sink(port: int) -> Optional[Any]:
    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), SinkHandler)
    except OSError as exc:
        print("  ! webhook sink could not bind 127.0.0.1:%d (%s)" % (port, exc))
        return None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def poll(fn, timeout: float, interval: float = 3.0) -> Tuple[bool, Any]:
    """Call fn() until it returns truthy (ok, detail) or the deadline passes."""
    deadline = time.time() + timeout
    last = (False, None)
    while time.time() < deadline:
        last = fn()
        if last[0]:
            return last
        time.sleep(interval)
    return last


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def add(self, link: str, status: str, detail: str) -> None:
        self.rows.append((link, status, detail))
        print("[%s] %-16s %s" % (status, link, detail))

    @property
    def failed(self) -> List[str]:
        return [name for name, status, _ in self.rows if status == FAIL]

    @property
    def skipped(self) -> List[str]:
        return [name for name, status, _ in self.rows if status == SKIP]

    def verdict(self) -> str:
        if self.failed:
            return "NOT VERIFIED (missing links: %s)" % ", ".join(self.failed)
        if self.skipped:
            return ("PARTIAL RUN - firing + visibility verified, but %s were not "
                    "evaluated in this run" % ", ".join(self.skipped))
        return "VERIFIED (all links in a single run)"


# --------------------------------------------------------------------------- #
# L1 / L2
# --------------------------------------------------------------------------- #
def scrape_metric_names(app_url: str) -> Tuple[bool, Dict[str, int], str]:
    """Metric family names present in the live Prometheus exposition."""
    url = app_url.rstrip("/") + "/v1/metrics/prometheus"
    ok, text, note = http_text(url)
    if not ok:
        return False, {}, "%s -> %s" % (url, note)
    if not text.strip():
        return False, {}, "%s returned an EMPTY payload" % url
    names: Dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 3:
                names[parts[2]] = names.get(parts[2], 0) + 1
    if not names:
        return False, {}, "%s has no '# TYPE' lines - not a Prometheus exposition" % url
    return True, names, "%d metric families, %d bytes" % (len(names), len(text))


def rule_metric_names(rules_path: str) -> List[str]:
    """Metric names referenced by every `expr:` in the rules file."""
    with open(rules_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    chunks: List[str] = []
    block_indent: Optional[int] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("expr:"):
            block_indent = indent
            chunks.append(stripped[len("expr:"):].strip())
        elif block_indent is not None and indent > block_indent:
            # continuation of a `expr: |` / `expr: >` block scalar
            chunks.append(stripped)
        else:
            block_indent = None
    names = set()
    for chunk in chunks:
        cleaned = re.sub(r'"[^"]*"', " ", chunk)       # drop quoted strings
        cleaned = re.sub(r"\{[^}]*\}", " ", cleaned)   # drop label selectors
        cleaned = re.sub(r"\[[^\]]*\]", " ", cleaned)  # drop range/duration [5m]
        for token in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", cleaned):
            if token in PROMQL_NOISE or token.endswith(":"):
                continue
            names.add(token)
    return sorted(names)


def check_rule_hygiene(rules_path: str, live: Dict[str, int]) -> Tuple[bool, str]:
    names = rule_metric_names(rules_path)
    if not names:
        return False, "no expr: found in %s - nothing to verify" % rules_path
    # `up` is injected by Prometheus itself for every scrape target, so it is a
    # legitimate reference even though the application never emits it.
    allowed = set(live) | {"up"}
    unknown = [n for n in names if n not in allowed]
    if unknown:
        return False, ("metrics never emitted, so their rules can NEVER fire: %s"
                       % ", ".join(unknown))
    return True, "%d referenced metrics all resolve: %s" % (len(names), ", ".join(names))


# --------------------------------------------------------------------------- #
# L3 / L4 / L5 / L6
# --------------------------------------------------------------------------- #
def check_target_up(prom_url: str, job: str) -> Tuple[bool, Any, str]:
    ok, payload, note = http_json(prom_url.rstrip("/") + "/api/v1/targets")
    if not ok:
        return False, None, "/api/v1/targets -> %s" % note
    targets = (payload or {}).get("data", {}).get("activeTargets", [])
    for tgt in targets:
        if tgt.get("labels", {}).get("job") == job:
            return True, tgt, "health=%s scrapeUrl=%s lastError=%r" % (
                tgt.get("health"), tgt.get("scrapeUrl"), tgt.get("lastError"))
    return False, None, "no active target with job=%s (seen: %s)" % (
        job, [t.get("labels", {}).get("job") for t in targets])


def check_stored(prom_url: str, metric: str) -> Tuple[bool, Any, str]:
    ok, payload, note = http_json(
        prom_url.rstrip("/") + "/api/v1/query?query=" + urllib.parse.quote(metric))
    if not ok:
        return False, None, "query %s -> %s" % (metric, note)
    results = (payload or {}).get("data", {}).get("result", [])
    if not results:
        return False, None, "query %s returned 0 series (not in storage)" % metric
    sample = results[0]
    return True, results, "%d series, e.g. %s = %s" % (
        len(results), sample.get("metric"), sample.get("value"))


def firing_alerts(prom_url: str) -> Tuple[bool, List[Dict[str, Any]], str]:
    ok, payload, note = http_json(prom_url.rstrip("/") + "/api/v1/alerts")
    if not ok:
        return False, [], "/api/v1/alerts -> %s" % note
    return True, (payload or {}).get("data", {}).get("alerts", []), "ok"


def wait_for_firing(prom_url: str, alert_names: Tuple[str, ...],
                    timeout: float) -> Tuple[bool, Any, str]:
    def probe():
        ok, alerts, _ = firing_alerts(prom_url)
        if not ok:
            return False, []
        hits = [a for a in alerts
                if a.get("labels", {}).get("alertname") in alert_names
                and a.get("state") == "firing"]
        return (bool(hits), hits)

    ok, hits = poll(probe, timeout)
    if ok:
        names = sorted({a["labels"]["alertname"] for a in hits})
        return True, hits, "state=firing for %s" % ", ".join(names)
    _, alerts, _ = firing_alerts(prom_url)
    return False, alerts, ("none of %s reached state=firing within %.0fs "
                           "(loaded: %s)" % (list(alert_names), timeout,
                                             sorted({a.get("labels", {}).get("alertname")
                                                     for a in alerts})))


def check_visibility(am_url: str, alert_names: Tuple[str, ...],
                     timeout: float) -> Tuple[bool, Any, str]:
    def probe():
        ok, payload, _ = http_json(
            am_url.rstrip("/") + "/api/v2/alerts?active=true&silenced=false"
            "&inhibited=false&unprocessed=false")
        if not ok or not isinstance(payload, list):
            return False, None
        hits = [a for a in payload
                if a.get("labels", {}).get("alertname") in alert_names]
        return (bool(hits), hits)

    ok, hits = poll(probe, timeout)
    if ok:
        names = sorted({a["labels"]["alertname"] for a in hits})
        return True, hits, "Alertmanager holds %s" % ", ".join(names)
    return False, None, "Alertmanager /api/v2/alerts has none of %s" % list(alert_names)


def sink_delivered_names(expected: Tuple[str, ...]) -> List[str]:
    delivered = set()
    for payload in SinkHandler.received:
        if not isinstance(payload, dict):
            continue
        for alert in payload.get("alerts", []) or []:
            name = (alert.get("labels") or {}).get("alertname")
            if name:
                delivered.add(name)
    return sorted(delivered & set(expected))


# --------------------------------------------------------------------------- #
# process / compose management
# --------------------------------------------------------------------------- #
def launch_native(bin_dir: str, prom_url: str, am_url: str) -> List[subprocess.Popen]:
    prom_exe = os.path.join(bin_dir, "prometheus.exe")
    am_exe = os.path.join(bin_dir, "alertmanager.exe")
    missing = [p for p in (prom_exe, am_exe) if not os.path.exists(p)]
    if missing:
        raise SystemExit("BLOCKER: missing binaries: %s" % ", ".join(missing))

    workdir = tempfile.mkdtemp(prefix="obsloop_")
    shutil.copy(LOCAL_PROM_CFG, os.path.join(workdir, "prometheus.yml"))
    shutil.copy(DEFAULT_RULES, os.path.join(workdir, "alert_rules.yml"))
    shutil.copy(LOCAL_AM_CFG, os.path.join(workdir, "alertmanager.yml"))

    prom_host = urllib.parse.urlparse(prom_url).netloc
    am_host = urllib.parse.urlparse(am_url).netloc

    proms = subprocess.Popen(
        [prom_exe,
         "--config.file=" + os.path.join(workdir, "prometheus.yml"),
         "--storage.tsdb.path=" + os.path.join(workdir, "tsdb"),
         "--web.listen-address=" + prom_host],
        cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ams = subprocess.Popen(
        [am_exe,
         "--config.file=" + os.path.join(workdir, "alertmanager.yml"),
         "--storage.path=" + os.path.join(workdir, "amdata"),
         "--web.listen-address=" + am_host],
        cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print("  collector workdir: %s" % workdir)
    return [proms, ams]


def wait_ready(url: str, path: str, timeout: float = 90.0) -> bool:
    ok, _ = poll(lambda: (http_text(url.rstrip("/") + path, timeout=3.0)[0], None),
                 timeout, interval=2.0)
    return ok


def run_compose() -> bool:
    probe = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        raise SystemExit(
            "BLOCKER: no Docker daemon reachable (`docker info` failed). The "
            "compose stack cannot be started on this host; use --mode local or "
            "--mode external.")
    up = subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, "up", "-d"],
                        cwd=REPO_ROOT)
    return up.returncode == 0


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the LIUHAO observability closed loop end to end.")
    ap.add_argument("--mode", choices=("external", "local", "docker"),
                    default="external")
    ap.add_argument("--app-url", default=DEFAULT_APP)
    ap.add_argument("--prom-url", default=DEFAULT_PROM)
    ap.add_argument("--am-url", default=DEFAULT_AM)
    ap.add_argument("--bin-dir", default="",
                    help="directory holding prometheus.exe / alertmanager.exe "
                         "(required for --mode local)")
    ap.add_argument("--rules", default=DEFAULT_RULES)
    ap.add_argument("--job", default="liuhao-ai-os")
    ap.add_argument("--probe-metric", default="liuhao_executor_fence_installed",
                    help="metric the app really emits; used for the storage link")
    ap.add_argument("--force", choices=("404", "down"), default="404")
    ap.add_argument("--force-count", type=int, default=10,
                    help="404 requests to issue when --force 404")
    ap.add_argument("--sink-port", type=int, default=DEFAULT_SINK_PORT)
    ap.add_argument("--timeout", type=float, default=180.0,
                    help="seconds to wait for firing and visibility")
    ap.add_argument("--keep-running", action="store_true",
                    help="leave locally started processes running on exit")
    ap.add_argument("--json", dest="json_out", default="",
                    help="write the machine-readable report here")
    args = ap.parse_args(argv)

    print("=" * 78)
    print("LIUHAO observability closed-loop gate (%s)"
          % time.strftime("%Y-%m-%dT%H:%M:%S"))
    print("  mode=%s force=%s app=%s prom=%s am=%s"
          % (args.mode, args.force, args.app_url, args.prom_url, args.am_url))
    print("=" * 78)

    rep = Report()
    procs: List[subprocess.Popen] = []
    sink = start_sink(args.sink_port)
    exit_code = 0

    try:
        # -- start the collector stack if this run owns it --------------------
        if args.mode == "local":
            if not args.bin_dir:
                print("BLOCKER: --mode local needs --bin-dir")
                return 2
            procs = launch_native(args.bin_dir, args.prom_url, args.am_url)
            if not wait_ready(args.prom_url, "/-/ready"):
                print("BLOCKER: Prometheus never became ready at %s" % args.prom_url)
                return 2
            if not wait_ready(args.am_url, "/-/ready"):
                print("BLOCKER: Alertmanager never became ready at %s" % args.am_url)
                return 2
        elif args.mode == "docker":
            if not run_compose():
                print("BLOCKER: docker compose up failed")
                return 2

        # -- L1 exporter ------------------------------------------------------
        live_names: Dict[str, int] = {}
        if args.force == "down":
            ok_health, _, _ = http_text(args.app_url.rstrip("/") + "/v1/health",
                                        timeout=3.0)
            if ok_health:
                rep.add("L1_EXPORTER", FAIL,
                        "--force down requires the app to be ALREADY stopped; "
                        "/v1/health still answers. Stop the app first, or run "
                        "--force 404.")
                print("\nVERDICT: %s" % rep.verdict())
                return 1
            rep.add("L1_EXPORTER", SKIP,
                    "app deliberately stopped for ServiceDown forcing; the "
                    "exporter link is NOT re-proven in this run and must be "
                    "proven by a separate --force 404 run.")
        else:
            ok, live_names, note = scrape_metric_names(args.app_url)
            rep.add("L1_EXPORTER", PASS if ok else FAIL,
                    ("exporter live: %s" % note) if ok
                    else "exporter unreachable/empty: %s" % note)
            if not ok:
                print("\nVERDICT: %s" % rep.verdict())
                return 1

        # -- L2 rule hygiene --------------------------------------------------
        if live_names:
            ok, note = check_rule_hygiene(args.rules, live_names)
            rep.add("L2_RULE_HYGIENE", PASS if ok else FAIL, note)
        else:
            rep.add("L2_RULE_HYGIENE", SKIP,
                    "no live scrape in this run (app stopped)")

        # -- L3 collector -----------------------------------------------------
        ok, tgt, note = check_target_up(args.prom_url, args.job)
        healthy = bool(ok and tgt and tgt.get("health") == "up")
        rep.add("L3_COLLECTOR", PASS if healthy else FAIL,
                note if healthy else "collector link missing: %s" % note)

        # -- L4 storage -------------------------------------------------------
        if args.force == "down":
            rep.add("L4_STORAGE", SKIP,
                    "series queryability proven in the --force 404 run; with the "
                    "app stopped the series is stale by design")
        else:
            ok, _res, note = check_stored(args.prom_url, args.probe_metric)
            rep.add("L4_STORAGE", PASS if ok else FAIL, note)

        # -- forcing ----------------------------------------------------------
        if args.force == "404":
            print("  forcing: %d real HTTP 404 requests against %s"
                  % (args.force_count, args.app_url))
            path = args.app_url.rstrip("/") + "/v1/__obs_loop_probe_not_found__"
            issued = 0
            for _ in range(args.force_count):
                http_text(path, timeout=5.0)
                issued += 1
            print("    issued %d requests" % issued)
            expected: Tuple[str, ...] = (UP_ALERT,)
        else:
            print("  forcing: application is DOWN (up == 0)")
            expected = DOWN_ALERTS

        # -- L5 firing --------------------------------------------------------
        ok, _hits, note = wait_for_firing(args.prom_url, expected, args.timeout)
        rep.add("L5_FIRING", PASS if ok else FAIL, note)

        # -- L6 visibility ----------------------------------------------------
        ok, _hits, note = check_visibility(args.am_url, expected, args.timeout)
        rep.add("L6_VISIBILITY", PASS if ok else FAIL, note)
        delivered = sink_delivered_names(expected) if sink is not None else []
        if delivered:
            print("[PASS] %-16s webhook sink received a real delivery for %s"
                  % ("L6_SINK", ", ".join(delivered)))
        else:
            print("[INFO] %-16s webhook sink captured no delivery for %s; the "
                  "Alertmanager API above is the authoritative visibility link"
                  % ("L6_SINK", list(expected)))

    finally:
        if procs and not args.keep_running:
            for proc in procs:
                proc.terminate()
        if sink is not None:
            sink.shutdown()

    print("\nVERDICT: %s" % rep.verdict())
    exit_code = 1 if rep.failed else 0
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"verdict": rep.verdict(),
                       "links": [{"link": n, "status": s, "detail": d}
                                 for n, s, d in rep.rows],
                       "sink_payloads": SinkHandler.received},
                      handle, indent=2)
        print("report written to %s" % args.json_out)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
