#!/usr/bin/env python
"""Gate: every metric named by a Prometheus alert must have a real producer.

Run:  python scripts/verify_alert_rules.py
Exit 0 = green, exit 1 = at least one rule is keyed to a metric nothing produces.

Why this exists
---------------
``configs/observability/rules/liuhao-ai-os-alerts.yml`` used to fire on
``provider_up``, ``task_queue_depth`` and ``agent_coordination_failures_total``.
All three are *declared* in ``src/observability/metrics.py`` and none of them is
ever incremented or set anywhere under ``src/``, so those alerts could not fire
no matter what happened in production. An alert that can never fire is worse
than no alert: it reads as coverage in every review that does not re-derive the
producer. This script makes that class of defect impossible to reintroduce.

What it actually checks
-----------------------
1. Every metric name appearing in an alert ``expr`` is either
   (a) produced under ``src/`` -- a mutation (``.inc()/.set()/.observe()/...``)
       of a declared ``prometheus_client`` collector, reached through the call
       graph, including the ``getattr(module, "name")`` dispatch idiom that
       ``src/kernels/execution/fence.py`` and ``src/distribution/coordination.py``
       use to keep metrics optional;
   (b) injected by Prometheus itself at scrape time (``up``, ``scrape_*``); or
   (c) exported by an exporter whose image is really declared in
       ``docker-compose.observability.yml`` and really targeted by a scrape job
       (``node_*`` / ``container_*``).
2. ``metrics_path`` of every non-infra scrape job is a route that really exists
   in ``src/gateway`` (this is the defect that made the whole scrape 404: the
   config asked for ``/metrics``, the app serves ``/v1/metrics/prometheus``).
3. No unmounted rule file is left behind outside the mounted rules directory --
   ``config/monitoring/prometheus_rules.yml`` and its ``.yaml`` twin were dead
   config (nothing mounted them) and were deleted; this keeps them deleted.
4. Every ``rule_files`` glob resolves to at least one existing file.
5. Every alert carries ``labels.severity`` and ``annotations.summary``.

Honest limitations -- read before trusting a green run
------------------------------------------------------
* The producer test is **not path-sensitive**. A metric incremented only under
  ``if failed:`` is counted as produced even if every real call site leaves
  ``failed`` at its default. This is exactly why no alert in the shipped rule
  file keys on ``agent_coordination_failures_total``: ``track_coordination()``
  is called, but always with ``failed=False``, so that counter is dead in
  practice. The gate would have passed it; judgement kept it out.
* The call graph is name-based, not type-aware, and the alias map is global.
  Two modules binding different objects to the same name would be conflated.
* "Invoked" means *has at least one call site anywhere under ``src/``*, not
  *reachable from a request*. A helper called only by another never-called
  helper would still count. This errs toward false negatives (a dead metric
  passes) -- the direction that does not cry wolf.
* This is not ``promtool``. It does not validate PromQL syntax, label
  cardinality, or whether a threshold is sane. It only answers: can this metric
  ever carry a sample?
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402  -- imported after the sys.path bootstrap by design

EXIT_OK = 0
EXIT_FAIL = 1

SRC_DIR = REPO_ROOT / "src"
GATEWAY_DIR = SRC_DIR / "gateway"
PROM_CONFIG = REPO_ROOT / "configs" / "observability" / "prometheus.yml"
RULES_DIR = REPO_ROOT / "configs" / "observability" / "rules"
OBS_COMPOSE = REPO_ROOT / "docker-compose.observability.yml"
LEGACY_RULE_DIR = REPO_ROOT / "config" / "monitoring"

#: prometheus_client collector constructors.
PROM_COLLECTOR_CLASSES = frozenset({"Counter", "Gauge", "Histogram", "Summary", "Info", "Enum"})
#: Methods that write a value into a collector.
MUTATING_METHODS = frozenset({"inc", "dec", "set", "observe", "remove", "clear"})

#: Series Prometheus synthesises itself; they have no producer in src/ by design.
PROMETHEUS_INJECTED = frozenset({
    "up",
    "scrape_duration_seconds",
    "scrape_samples_scraped",
    "scrape_samples_post_metric_relabeling",
    "scrape_series_added",
    "scrape_body_size_bytes",
    "ALERTS",
    "ALERTS_FOR_STATE",
})

#: Exporter image -> metric prefix. A prefix is only accepted when the image is
#: really declared in the observability compose file AND really scraped, so this
#: is evidence-gated rather than a blanket escape hatch for unknown metrics.
EXPORTER_IMAGE_PREFIXES = {
    "prom/node-exporter": "node_",
    "gcr.io/cadvisor/cadvisor": "container_",
}

#: Hosts that are part of the observability stack itself (its exporters serve
#: their own /metrics; src/gateway routes do not apply to them).
EXTRA_INFRA_HOSTS = frozenset({"localhost", "127.0.0.1"})

HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")

PROMQL_RESERVED = frozenset({
    "abs", "absent", "absent_over_time", "and", "atan2", "avg", "avg_over_time",
    "bool", "bottomk", "by", "ceil", "changes", "clamp", "clamp_max", "clamp_min",
    "count", "count_over_time", "count_values", "day_of_month", "day_of_week",
    "day_of_year", "days_in_month", "delta", "deriv", "e", "end", "exp", "floor",
    "for", "group", "group_left", "group_right", "histogram_quantile",
    "holt_winters", "hour", "idelta", "ignoring", "increase", "inf", "irate",
    "label_join", "label_replace", "last_over_time", "le", "ln", "log10", "log2",
    "mad_over_time", "max", "max_over_time", "min", "min_over_time", "minute",
    "month", "nan", "offset", "on", "or", "pi", "predict_linear",
    "present_over_time", "quantile", "quantile_over_time", "rate", "resets",
    "round", "scalar", "sgn", "sort", "sort_desc", "sqrt", "start", "stddev",
    "stddev_over_time", "stdvar", "stdvar_over_time", "sum", "sum_over_time",
    "time", "timestamp", "topk", "unless", "vector", "without", "year",
})

ROUTE_DECORATORS = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "trace",
    "api_route", "websocket",
})

_BRACES_RE = re.compile(r"\{[^{}]*\}")
_GROUPING_RE = re.compile(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\(([^)]*)\)")
_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)\'')
_OFFSET_RE = re.compile(r"\boffset\s+\S+")
_DURATION_RE = re.compile(r"\b\d+[smhdwy]\b")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")
_IDENT_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")


# --------------------------------------------------------------------------- #
# generic AST / file helpers
# --------------------------------------------------------------------------- #

def _iter_python(root: pathlib.Path) -> List[pathlib.Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _parse(path: pathlib.Path) -> Optional[ast.AST]:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return "%s.%s" % (prefix, node.attr) if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _tail(node: ast.AST) -> str:
    return _dotted(node).rsplit(".", 1)[-1]


def _is_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _imports_top_package(tree: ast.AST, package: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == package for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == package:
                return True
    return False


def _load_yaml(path: pathlib.Path) -> Optional[object]:
    if not path.is_file():
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return None


# --------------------------------------------------------------------------- #
# 1. what src/ really produces
# --------------------------------------------------------------------------- #

def _declared_metrics() -> Dict[str, str]:
    """Map collector variable -> Prometheus metric name.

    Only files that import ``prometheus_client`` are considered, so the
    unrelated ``Counter``/``Gauge``/``Histogram`` classes in
    ``src/reliability/metrics.py`` cannot be mistaken for collectors.
    """
    out: Dict[str, str] = {}
    for path in _iter_python(SRC_DIR):
        tree = _parse(path)
        if tree is None or not _imports_top_package(tree, "prometheus_client"):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _tail(node.value.func) not in PROM_COLLECTOR_CLASSES or not node.value.args:
                continue
            first = node.value.args[0]
            if not _is_str(first):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = first.value  # type: ignore[union-attr]
    return out


def _import_aliases(declared: Dict[str, str]) -> Dict[str, str]:
    """Local name -> collector variable, for ``from ..metrics import X as Y``."""
    aliases: Dict[str, str] = {}
    for path in _iter_python(SRC_DIR):
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in declared:
                        aliases[alias.asname or alias.name] = alias.name
    return aliases


def _produced_metric_names(declared: Dict[str, str]) -> Tuple[Set[str], Set[str]]:
    """Return (metric names with a reachable producer, all declared names)."""
    aliases = _import_aliases(declared)
    fn_metrics: Dict[str, Set[str]] = {}
    fn_calls: Dict[str, Set[str]] = {}
    module_metrics: Set[str] = set()
    seeds: Set[str] = set()

    def resolve(token: str) -> Optional[str]:
        if token in declared:
            return token
        var = aliases.get(token)
        if var is not None and var in declared:
            return var
        return None

    def mutations(node: ast.AST) -> Set[str]:
        found: Set[str] = set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call) or not isinstance(sub.func, ast.Attribute):
                continue
            if sub.func.attr not in MUTATING_METHODS:
                continue
            for inner in ast.walk(sub):
                token: Optional[str] = None
                if isinstance(inner, ast.Name):
                    token = inner.id
                elif isinstance(inner, ast.Attribute):
                    token = inner.attr
                if token:
                    var = resolve(token)
                    if var:
                        found.add(var)
        return found

    def callees(node: ast.AST) -> Set[str]:
        found: Set[str] = set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            name = _tail(sub.func)
            if name:
                found.add(name)
            for arg in list(sub.args) + [kw.value for kw in sub.keywords]:
                if _is_str(arg):
                    found.add(arg.value)  # type: ignore[union-attr]
        return found

    def walk(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
                fn_metrics.setdefault(name, set()).update(mutations(child))
                fn_calls.setdefault(name, set()).update(callees(child))
                if child.decorator_list:
                    seeds.add(name)
                walk(child, True)
            else:
                if not in_function:
                    module_metrics.update(mutations(child))
                    seeds.update(callees(child))
                walk(child, in_function)

    for path in _iter_python(SRC_DIR):
        tree = _parse(path)
        if tree is not None:
            walk(tree, False)

    # A function with at least one call site anywhere is treated as invoked;
    # decorator-registered entry points and module-level calls are seeds too.
    for calls in fn_calls.values():
        seeds.update(calls)
    reached: Set[str] = set(seeds)
    frontier = list(seeds)
    while frontier:
        name = frontier.pop()
        for callee in fn_calls.get(name, ()):  # reachable transitively
            if callee not in reached:
                reached.add(callee)
                frontier.append(callee)

    produced_vars: Set[str] = set(module_metrics)
    for name in reached:
        produced_vars |= fn_metrics.get(name, set())
    produced = {declared[v] for v in produced_vars if v in declared}
    return produced, set(declared.values())


# --------------------------------------------------------------------------- #
# 2. what the scrape config asks for
# --------------------------------------------------------------------------- #

def _gateway_routes() -> Set[str]:
    """Full URL paths served by src/gateway (router prefix + decorator path)."""
    routes: Set[str] = set()
    for path in _iter_python(GATEWAY_DIR):
        tree = _parse(path)
        if tree is None:
            continue
        prefixes: Set[str] = set()
        router_calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _tail(node.func) == "APIRouter":
                router_calls += 1
                has_prefix = False
                for kw in node.keywords:
                    if kw.arg == "prefix" and _is_str(kw.value):
                        prefixes.add(kw.value.value)  # type: ignore[union-attr]
                        has_prefix = True
                if not has_prefix:
                    prefixes.add("")
        if not router_calls:
            prefixes.add("")  # prefix unknown here; errs toward accepting
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if dec.func.attr in ROUTE_DECORATORS and dec.args and _is_str(dec.args[0]):
                    suffix = dec.args[0].value  # type: ignore[union-attr]
                    for prefix in prefixes:
                        routes.add("%s%s" % (prefix, suffix))
    return routes


def _observability_services() -> Dict[str, str]:
    """service name -> image base (tag stripped) from the observability stack."""
    data = _load_yaml(OBS_COMPOSE)
    if not isinstance(data, dict):
        return {}
    services = data.get("services")
    if not isinstance(services, dict):
        return {}
    out: Dict[str, str] = {}
    for name, cfg in services.items():
        if not isinstance(cfg, dict):
            continue
        image = str(cfg.get("image") or "")
        out[str(name)] = image.rsplit(":", 1)[0] if image else ""
    return out


def _scrape_jobs() -> List[Tuple[str, str, Set[str]]]:
    """(job_name, metrics_path, target hosts)."""
    data = _load_yaml(PROM_CONFIG)
    if not isinstance(data, dict):
        return []
    jobs = data.get("scrape_configs")
    if not isinstance(jobs, list):
        return []
    out: List[Tuple[str, str, Set[str]]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        hosts: Set[str] = set()
        for block in job.get("static_configs") or []:
            if not isinstance(block, dict):
                continue
            for target in block.get("targets") or []:
                hosts.add(str(target).split(":")[0])
        out.append((str(job.get("job_name", "")), str(job.get("metrics_path") or "/metrics"), hosts))
    return out


def _rule_file_globs() -> List[str]:
    data = _load_yaml(PROM_CONFIG)
    if not isinstance(data, dict):
        return []
    globs = data.get("rule_files")
    return [str(g) for g in globs] if isinstance(globs, list) else []


def _exporter_prefixes(services: Dict[str, str], jobs: List[Tuple[str, str, Set[str]]]) -> Dict[str, str]:
    """prefix -> evidence, only for exporters that really run and are scraped."""
    scraped_hosts: Set[str] = set()
    for _name, _path, hosts in jobs:
        scraped_hosts |= hosts
    out: Dict[str, str] = {}
    for service, image in services.items():
        if not image or service not in scraped_hosts:
            continue
        prefix = EXPORTER_IMAGE_PREFIXES.get(image)
        if prefix:
            out[prefix] = "service %s (image %s) is scraped by prometheus.yml" % (service, image)
    return out


# --------------------------------------------------------------------------- #
# 3. what the rules ask for
# --------------------------------------------------------------------------- #

def _metric_names_in(expr: str) -> Set[str]:
    text = _GROUPING_RE.sub(" ", expr)
    while True:
        stripped = _BRACES_RE.sub(" ", text)
        if stripped == text:
            break
        text = stripped
    text = _STRING_RE.sub(" ", text)
    text = _OFFSET_RE.sub(" ", text)
    text = _DURATION_RE.sub(" ", text)
    text = _NUMBER_RE.sub(" ", text)
    return {tok for tok in _IDENT_RE.findall(text) if tok not in PROMQL_RESERVED}


def _rule_files() -> List[pathlib.Path]:
    if not RULES_DIR.is_dir():
        return []
    return sorted(p for p in RULES_DIR.iterdir() if p.suffix in (".yml", ".yaml"))


def _unmounted_rule_files() -> List[pathlib.Path]:
    """Rule-looking files outside the directory the compose file mounts."""
    out: List[pathlib.Path] = []
    if not LEGACY_RULE_DIR.is_dir():
        return out
    for path in sorted(LEGACY_RULE_DIR.iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        data = _load_yaml(path)
        if isinstance(data, dict) and isinstance(data.get("groups"), list):
            out.append(path)
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> int:
    failures: List[str] = []
    notes: List[str] = []

    def fail(message: str) -> None:
        failures.append(message)

    declared = _declared_metrics()
    if not declared:
        fail("no prometheus_client collectors found under src/ -- the scan is broken")
        return EXIT_FAIL
    produced, all_declared = _produced_metric_names(declared)
    services = _observability_services()
    jobs = _scrape_jobs()
    exporters = _exporter_prefixes(services, jobs)
    routes = _gateway_routes()

    print("declared collectors : %d" % len(declared))
    print("with a producer     : %d" % len(produced))
    for name in sorted(all_declared - produced):
        notes.append("declared but never fed in src/: %s" % name)
    print("exporter prefixes   : %s" % (sorted(exporters) or "none"))

    # ---- check 1: every alert metric has a producer -----------------------
    rule_paths = _rule_files()
    if not rule_paths:
        fail("no rule files found in %s" % RULES_DIR)
    for path in rule_paths:
        data = _load_yaml(path)
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            fail("%s is not a Prometheus rule file (no top-level 'groups')" % path.name)
            continue
        for group in data["groups"]:
            if not isinstance(group, dict):
                continue
            for rule in group.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                label = str(rule.get("alert") or rule.get("record") or "<unnamed>")
                expr = rule.get("expr")
                if not isinstance(expr, str) or not expr.strip():
                    fail("%s :: %s has no usable 'expr'" % (path.name, label))
                    continue
                for metric in sorted(_metric_names_in(expr)):
                    base = metric
                    for suffix in HISTOGRAM_SUFFIXES:
                        if metric.endswith(suffix):
                            base = metric[: -len(suffix)]
                            break
                    if metric in PROMETHEUS_INJECTED:
                        continue
                    if metric in produced or base in produced:
                        continue
                    prefix_ok = [p for p in exporters if metric.startswith(p)]
                    if prefix_ok:
                        continue
                    fail(
                        "%s :: %s references '%s', which no code under src/ "
                        "increments or sets and no running exporter provides"
                        % (path.name, label, metric)
                    )
                if "alert" in rule:
                    severity = (rule.get("labels") or {}).get("severity")
                    summary = (rule.get("annotations") or {}).get("summary")
                    if not severity:
                        fail("%s :: %s has no labels.severity" % (path.name, label))
                    if not summary:
                        fail("%s :: %s has no annotations.summary" % (path.name, label))

    # ---- check 2: metrics_path must be a route the app really serves ------
    infra_hosts = set(services) | EXTRA_INFRA_HOSTS
    for name, metrics_path, hosts in jobs:
        if hosts and hosts <= infra_hosts:
            continue  # an exporter / prometheus itself serves its own /metrics
        if metrics_path not in routes:
            fail(
                "scrape job '%s' uses metrics_path '%s', which is not a route "
                "served by src/gateway" % (name, metrics_path)
            )
    if not jobs:
        fail("could not read any scrape_configs from %s" % PROM_CONFIG.name)

    # ---- check 3: no dead rule files left behind --------------------------
    for path in _unmounted_rule_files():
        fail(
            "%s is a Prometheus rule file outside the mounted rules directory; "
            "nothing mounts it, so it cannot fire -- delete it or mount it"
            % path.relative_to(REPO_ROOT).as_posix()
        )

    # ---- check 4: rule_files globs resolve --------------------------------
    for pattern in _rule_file_globs():
        matches = sorted(REPO_ROOT.joinpath("configs/observability").glob(pattern))
        if not matches:
            fail("prometheus.yml rule_files entry '%s' matches no file" % pattern)

    print()
    for note in notes:
        print("NOTE: %s" % note)
    if failures:
        print("RESULT: %d FAILED" % len(failures))
        for message in failures:
            print("  - %s" % message)
        return EXIT_FAIL
    print("RESULT: ALL GREEN (every alerted metric has a producer)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
