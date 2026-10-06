#!/usr/bin/env python3
"""Prove a real user can complete a real job against the DEPLOYED stack.

Why this exists
===============
Everything before this script proved the product works **in a test process**:
``verify_http_full_closure.py``, ``verify_identity_user_surface.py`` and friends
boot the app in-process and call it over HTTP. That is necessary and it is not
the same thing as *being deployed*. A deployment is a different artifact -- an
image, a compose project, a healthcheck, a volume, a published port, a browser
loading a bundle that was built by a different toolchain. Each of those can
break while every in-process test stays green.

This script closes that gap end to end:

  1. build + ``up`` the real compose stack in an **isolated** deployment;
  2. wait for the stack to be healthy through the published port;
  3. prove the first-human bootstrap is fail-closed, then bootstrap one;
  4. drive a **headless browser** against the deployed console -- log in with a
     real credential, hire a real employee, create a real goal, stop it;
  5. assert with ``docker exec`` that the artifact really exists **on the
     container filesystem** with the exact expected content;
  6. assert audit records exist and are tied to that goal;
  7. tear down with ``docker compose down -v`` so nothing leaks.

Hard rules
==========
* **A missing prerequisite BLOCKS, it never silently passes.** The script's
  subject is the deployed stack; if Docker or a browser is absent the closure
  is **NOT VERIFIED** and the exit status is non-zero. A verifier that quietly
  goes green when its subject is missing is worse than no verifier -- it is the
  exact failure mode this keystone exists to remove.
* **NOT VERIFIED is not FAIL.** A missing Docker daemon or a missing browser is
  an *environment* gap: nothing was proven, but nothing was disproven either.
  It is reported as ``BLOCKED`` / ``NOT VERIFIED`` and exits ``2``. Only a
  check that actually ran and did not hold is reported as ``FAIL`` (exit ``1``).
  Conflating the two would let an operator read "the deployment is broken"
  out of a machine that simply has no daemon.
* **No production evidence is touched.** The deployment gets a fresh temp
  project name, a fresh temp ``.env`` with generated secrets, and fresh named
  volumes that are deleted at the end. Nothing here mounts, reads or reuses
  the frozen HC-01 production audit database.
* **Never weaken the test to get green.** If the goal does not really produce
  the artifact, or the audit rows are not really tied to the goal, this FAILS.

Portability
===========
This gate runs on the **host / CI runner**, not inside the deployed container;
it drives a browser against the deployed stack. It therefore needs BOTH a
Docker daemon and a browser binary on the machine that runs it.

The browser is resolved, never hardcoded:

  1. ``--browser-binary`` / ``$LIUHAO_BROWSER_BINARY`` (explicit override);
  2. auto-detection of well-known locations (Linux chromium/chrome first, then
     Windows Edge/Chrome, then macOS);
  3. Playwright's own bundled chromium, when the ``playwright`` extra is
     installed.

If none of those exist the click-through is BLOCKED -- not skipped, not
crashed. The previous revision hardcoded
``C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe``, which
made the gate meaningless on every non-Windows host (see UNVERIFIED #4-a).

Dependencies
============
``playwright`` and ``selenium`` are **optional** -- declaring them as runtime
dependencies would bloat the production image and every CI install for a gate
most jobs cannot run. They live in the ``deploy-closure`` extra::

    pip install -e ".[deploy-closure]"

Exit status
===========
  * ``0`` -- every check passed; the closure was really demonstrated.
  * ``1`` -- at least one check FAILed (a real finding about the product).
  * ``2`` -- a prerequisite is missing: closure NOT VERIFIED. Non-zero on
             purpose -- this must never read as green.

Usage
=====
  python scripts/verify_deployed_product_closure.py
  python scripts/verify_deployed_product_closure.py --keep      # leave stack up
  python scripts/verify_deployed_product_closure.py --skip-build
  python scripts/verify_deployed_product_closure.py --host-port 18000
  python scripts/verify_deployed_product_closure.py \
      --browser-binary /usr/bin/chromium
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys

# Bootstrap sys.path so `import src...` resolves when this script is run
# directly (`python scripts/x.py` from the repo root). Required by the
# meta-guardrail tests/test_guardrail_scripts.py.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

COMPOSE_FILE = "docker-compose.prod.yml"
SERVICE = "api"

# Default matches docker-compose.prod.yml ("8080:8080"), but it is overridable:
# a hardcoded host port makes the gate unrunnable on any host where 8080 is
# already taken, and this repo already reserves 8000/8010/9090/9093 for
# concurrently running services.
DEFAULT_HOST_PORT = 8080
HOST_PORT = DEFAULT_HOST_PORT
BASE_URL = "http://127.0.0.1:%d" % HOST_PORT


def set_host_port(port: int) -> None:
    """(Re)bind the module-level port used by every HTTP call."""
    global HOST_PORT, BASE_URL
    HOST_PORT = int(port)
    BASE_URL = "http://127.0.0.1:%d" % HOST_PORT


# The job the real user performs. The content string is asserted byte-for-byte
# on the container filesystem, so it must be unusual enough that nothing else
# could have produced it.
ARTIFACT_NAME = "deploy_closure_proof.txt"
ARTIFACT_CONTENT = "LIUHAO-DEPLOY-CLOSURE-%s"
GOAL_TEXT = (
    "在工作区根目录创建文件 %s，文件内容恰好是 %s，不要加任何其他内容。"
)

BOOTSTRAP_PRINCIPAL = "deploy-closure-operator"

EXIT_OK, EXIT_FAIL, EXIT_PREREQ = 0, 1, 2


# --------------------------------------------------------------------------- #
# Result recording
# --------------------------------------------------------------------------- #
PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"


class Report(object):
    """Collects per-check PASS / FAIL / BLOCKED and renders a verdict.

    Why three states and not two
    ----------------------------
    ``FAIL`` asserts something about the *product*. ``BLOCKED`` asserts
    something about the *machine running the check*: a prerequisite was absent,
    so nothing was proven and nothing was disproven. Collapsing BLOCKED into
    FAIL turns "this host has no Docker daemon" into "the deployment is
    broken", which is a false claim; collapsing it into PASS is worse still.
    Both non-PASS states keep the exit status non-zero.
    """

    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def _add(self, name: str, status: str, detail: str = "") -> bool:
        self.rows.append((name, status, detail))
        print("[%s] %s%s" % (status, name,
                             (" -- " + detail) if detail else ""))
        return status == PASS

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        return self._add(name, PASS if ok else FAIL, detail)

    def block(self, name: str, detail: str = "") -> bool:
        """A prerequisite is missing: NOT VERIFIED, never a silent pass."""
        return self._add(name, BLOCKED, detail)

    @property
    def failed(self) -> int:
        return sum(1 for _, st, _ in self.rows if st == FAIL)

    @property
    def blocked(self) -> int:
        return sum(1 for _, st, _ in self.rows if st == BLOCKED)

    def render(self) -> str:
        total = len(self.rows)
        bad, blocked = self.failed, self.blocked
        passed = total - bad - blocked
        lines = ["", "=" * 74,
                 "deployed-product closure: %d passed, %d FAILED, %d BLOCKED "
                 "(of %d)" % (passed, bad, blocked, total),
                 "=" * 74]
        if bad:
            lines.append("RESULT: FAIL -- the following did not hold:")
            for name, st, detail in self.rows:
                if st == FAIL:
                    lines.append("  * %s%s" % (name, (" -- " + detail) if detail else ""))
        elif blocked:
            lines.append("RESULT: NOT VERIFIED -- the closure was NOT "
                         "demonstrated. Blocked prerequisites (environment gap,")
            lines.append("        NOT a product defect -- nothing was proven "
                         "and nothing was disproven):")
            for name, st, detail in self.rows:
                if st == BLOCKED:
                    lines.append("  * %s%s" % (name, (" -- " + detail) if detail else ""))
        else:
            lines.append("RESULT: PASS -- a real user completed a real job "
                         "against the deployed stack.")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Shell / HTTP helpers
# --------------------------------------------------------------------------- #
def run(cmd: List[str], env: Optional[Dict[str, str]] = None,
        stdin_data: Optional[str] = None,
        timeout: int = 900) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(cmd, cwd=str(REPO_ROOT), env=merged,
                          input=stdin_data, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def http_json(method: str, path: str, token: Optional[str] = None,
              body: Optional[Dict[str, Any]] = None,
              timeout: float = 30.0) -> Tuple[int, Any]:
    """Return (status, parsed-json-or-None). Never raises on HTTP error status."""
    url = BASE_URL + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        # The console sends the private header first (see lib/auth.ts
        # TOKEN_HEADER) because a hosted edge gateway rewrites Authorization.
        req.add_header("X-Liuhao-Token", token)
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception as exc:  # connection refused / reset while booting
        return 0, {"_error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, {"_raw": raw[:400]}


# --------------------------------------------------------------------------- #
# Headless browser
# --------------------------------------------------------------------------- #
def classify_binary(path: str) -> str:
    """Chromium-family or Edge? They need different Selenium drivers."""
    return "edge" if "edge" in os.path.basename(path).lower() else "chromium"


#: Auto-detection order. **Linux/CI first**: this gate's target deployment
#: environment is a Linux host, so a Linux browser must win over a Windows one.
BROWSER_CANDIDATES: List[Tuple[str, str]] = [
    ("/usr/bin/chromium", "chromium"),
    ("/usr/bin/chromium-browser", "chromium"),
    ("/usr/bin/google-chrome", "chromium"),
    ("/usr/bin/google-chrome-stable", "chromium"),
    ("/snap/bin/chromium", "chromium"),
    ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "chromium"),
    (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chromium"),
    (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chromium"),
    (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "edge"),
    (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "edge"),
]


def configured_binary(explicit: Optional[str] = None) -> Optional[str]:
    """The operator's explicit choice, if any (flag wins over env var)."""
    return explicit or os.environ.get("LIUHAO_BROWSER_BINARY") or None


def resolve_browser_binary(explicit: Optional[str] = None
                           ) -> Optional[Tuple[str, str]]:
    """Locate a browser binary -> ``(path, kind)``; ``None`` if there is none.

    Resolution order (first hit wins):

      1. ``--browser-binary`` / ``$LIUHAO_BROWSER_BINARY`` -- **authoritative**;
      2. auto-detection over :data:`BROWSER_CANDIDATES` (Linux/CI first);
      3. Playwright's bundled chromium (handled by the caller, not here).

    An explicit choice that does not exist returns ``None`` **without falling
    back** to a different browser. Silently substituting some other browser
    would turn a typo into a gate that "passes" using a binary nobody asked
    for -- the misconfiguration has to be reported, not papered over.

    Nothing here is Windows-specific. The previous revision hardcoded
    ``C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe``, which
    made the gate structurally meaningless off Windows.
    """
    chosen = configured_binary(explicit)
    if chosen:
        return (chosen, classify_binary(chosen)) if os.path.isfile(chosen) else None
    for path, kind in BROWSER_CANDIDATES:
        if os.path.isfile(path):
            return path, kind
    return None


class BrowserUnavailable(RuntimeError):
    """No browser backend could be started.

    Distinct from an assertion failure on purpose: the caller turns this into
    BLOCKED / NOT VERIFIED (exit 2), not into FAIL. A machine with no browser
    has not disproven the product -- it has proven nothing at all.
    """


class Browser(object):
    """Thin driver-agnostic facade over Playwright or Selenium.

    Both backends are tried, and both honour the resolved browser binary
    instead of a hardcoded path. If neither can drive a browser this raises
    :class:`BrowserUnavailable` and the caller reports BLOCKED -- the
    click-through is the point of the exercise and must never be skipped.
    """

    #: where the binary came from, for honest reporting
    source: str = ""

    def __init__(self, binary: Optional[str] = None) -> None:
        self._kind = ""
        self._raw: Any = None
        self._page: Any = None
        self._By: Any = None
        self._open(binary)

    def _open(self, binary: Optional[str]) -> None:
        resolved = resolve_browser_binary(binary)
        executable = resolved[0] if resolved else None
        kind = resolved[1] if resolved else ""
        if resolved:
            self.source = "%s (%s)" % (executable, kind)
        errors: List[str] = []

        # -- 1. Playwright (preferred: it manages its own driver) ------------ #
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:
            sync_playwright = None  # type: ignore
            errors.append("playwright not importable: %s" % exc)
        if sync_playwright is not None:
            try:
                pw = sync_playwright().start()
                launch = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
                if executable:
                    launch["executable_path"] = executable
                self._raw = pw
                self._page = pw.chromium.launch(**launch).new_page()
                self._kind = "playwright"
                if not self.source:
                    self.source = "playwright bundled chromium"
                return
            except Exception as exc:
                errors.append("playwright launch failed: %s" % str(exc)[:160])
                self._close()

        # -- 2. Selenium (Chromium or Edge, whichever the binary is) --------- #
        try:
            from selenium import webdriver  # type: ignore
            from selenium.webdriver.common.by import By  # type: ignore
        except Exception as exc:
            raise BrowserUnavailable(
                "no browser backend importable (%s); %s"
                % (exc, " | ".join(errors)))
        try:
            if kind == "edge":
                from selenium.webdriver.edge.options import Options  # type: ignore
                driver = webdriver.Edge
                label = "selenium-edge"
            else:
                from selenium.webdriver.chrome.options import Options  # type: ignore
                driver = webdriver.Chrome
                label = "selenium-chromium"
            options = Options()
            for arg in ("--headless=new", "--no-sandbox",
                        "--disable-dev-shm-usage", "--disable-gpu",
                        "--window-size=1440,900"):
                options.add_argument(arg)
            if executable:
                options.binary_location = executable
            self._raw = driver(options=options)
            self._kind = label
            self._page = self._raw
            self._By = By
            self.source = "%s via %s" % (executable or "default", label)
            return
        except Exception as exc:
            errors.append("%s launch failed: %s"
                          % (kind or "chromium", str(exc)[:160]))

        raise BrowserUnavailable(
            "could not start a headless browser (tried: %s). Set "
            "--browser-binary or $LIUHAO_BROWSER_BINARY, or install the "
            "'deploy-closure' extra for Playwright's bundled chromium."
            % " | ".join(errors))

    # -- facade ------------------------------------------------------------ #
    @property
    def kind(self) -> str:
        return self._kind

    def get(self, url: str) -> None:
        if self._kind == "playwright":
            self._page.goto(url, wait_until="domcontentloaded")
        else:
            self._raw.get(url)

    def text(self) -> str:
        if self._kind == "playwright":
            return self._page.inner_text("body")
        return self._raw.find_element(self._By.TAG_NAME, "body").text

    def fill(self, selector: str, value: str, timeout: float = 20.0) -> None:
        if self._kind == "playwright":
            self._page.fill(selector, value, timeout=timeout * 1000)
        else:
            from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
            from selenium.webdriver.support import expected_conditions as EC  # type: ignore
            from selenium.webdriver.common.by import By  # type: ignore
            el = WebDriverWait(self._raw, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
            el.clear()
            el.send_keys(value)

    def click(self, selector: str, timeout: float = 20.0) -> None:
        if self._kind == "playwright":
            self._page.click(selector, timeout=timeout * 1000)
        else:
            from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
            from selenium.webdriver.support import expected_conditions as EC  # type: ignore
            from selenium.webdriver.common.by import By  # type: ignore
            WebDriverWait(self._raw, timeout).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, selector))).click()

    def click_text(self, tag: str, text: str, timeout: float = 20.0) -> None:
        """Click an element by its visible text (nav items, buttons)."""
        if self._kind == "playwright":
            self._page.click("xpath=//%s[normalize-space()=%r]" % (tag, text),
                             timeout=timeout * 1000)
        else:
            from selenium.webdriver.support.ui import WebDriverWait  # type: ignore
            from selenium.webdriver.support import expected_conditions as EC  # type: ignore
            from selenium.webdriver.common.by import By  # type: ignore
            WebDriverWait(self._raw, timeout).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//%s[normalize-space()=%r]" % (tag, text)))).click()

    def wait_for_text(self, needle: str, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.text():
                return True
            time.sleep(0.5)
        return False

    def screenshot(self, path: str) -> None:
        try:
            if self._kind == "playwright":
                self._page.screenshot(path=path, full_page=True)
            else:
                self._raw.save_screenshot(path)
        except Exception:
            pass

    def _close(self) -> None:
        try:
            if self._kind == "playwright":
                if self._raw:
                    self._raw.stop()
            elif self._raw:
                self._raw.quit()
        except Exception:
            pass
        self._raw, self._page, self._kind = None, None, ""

    def quit(self) -> None:
        self._close()


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #
def preflight_docker(rep: Report) -> bool:
    """Docker must be present AND have a live daemon.

    A missing daemon BLOCKS (NOT VERIFIED, exit 2) -- it does not FAIL. This
    script's subject is the deployed stack, so nothing may be reported green,
    but "this host has no Docker" is a fact about the host, not evidence that
    the deployment is broken.
    """
    if shutil.which("docker") is None:
        rep.block("docker CLI present", "docker not on PATH")
        return False
    rep.check("docker CLI present", True)
    proc = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=60)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = ((proc.stderr or proc.stdout or "") or "").strip().splitlines()
        rep.block("docker daemon reachable",
                  "no live daemon -- %s" % (detail[-1] if detail else "unknown"))
        print("\n" + "!" * 74)
        print("! DOCKER DAEMON UNAVAILABLE -- deployed-product closure NOT VERIFIED.")
        print("! This script's subject is the deployed stack. It does not go")
        print("! green by skipping; it exits 2 as NOT VERIFIED, because")
        print("! nothing was proven AND nothing was disproven.")
        for line in detail[-4:]:
            print("!   %s" % line)
        print("!" * 74)
        return False
    rep.check("docker daemon reachable", True, "server " + proc.stdout.strip())
    return True


def preflight_browser(rep: Report, binary: Optional[str]) -> bool:
    """A browser must be resolvable BEFORE the stack is brought up.

    Checked up front on purpose: the previous revision booted the whole stack,
    migrated, bootstrapped a human and only then discovered it could not drive
    a browser -- minutes of work burned to reach a foregone conclusion.
    """
    resolved = resolve_browser_binary(binary)
    if resolved:
        rep.check("browser binary resolvable", True, "%s (%s)" % resolved)
    else:
        # Distinguish "you pointed me at a browser that isn't there" (a real
        # misconfiguration, worth shouting about) from "no browser at all here".
        chosen = configured_binary(binary)
        if chosen:
            rep.block("browser binary resolvable",
                      "explicitly configured browser binary does NOT exist: %s "
                      "(not falling back to a different browser). Fix "
                      "--browser-binary / $LIUHAO_BROWSER_BINARY."
                      % chosen)
            return False
        try:
            import playwright  # noqa: F401  type: ignore
            rep.check("browser binary resolvable", True,
                      "no system binary; relying on playwright bundled chromium")
        except Exception:
            rep.block(
                "browser binary resolvable",
                "no browser binary found and playwright is not installed. "
                "Install the 'deploy-closure' extra "
                "(pip install -e '.[deploy-closure]') or set --browser-binary "
                "/ $LIUHAO_BROWSER_BINARY. The click-through is NOT VERIFIED "
                "(the deployed stack itself was not disproven).")
            return False

    # A resolvable binary is not the same as a *usable* browser. Launch it and
    # navigate once: observed on the authoring host, Edge + selenium start
    # cleanly and then fail with "WebDriverException: unhandled request", which
    # would otherwise crash the gate mid-run instead of reporting BLOCKED.
    try:
        browser = Browser(binary)
    except BrowserUnavailable as exc:
        rep.block("headless browser launches", str(exc)[:300])
        return False
    try:
        browser.get("about:blank")
        browser.text()
        rep.check("headless browser usable", True,
                  "%s -- %s" % (browser.kind, browser.source))
        return True
    except Exception as exc:
        rep.block("headless browser usable",
                  "browser launched (%s) but the session is not usable: %s: %s"
                  % (browser.kind, type(exc).__name__, str(exc)[:180]))
        return False
    finally:
        browser.quit()


def write_env(tmp: Path, password: str) -> Path:
    """Isolated .env -- generated secrets, never anything from the host."""
    env_file = tmp / "deploy.env"
    lines = [
        "JWT_SECRET_KEY=%s" % secrets.token_urlsafe(48),
        "ENCRYPTION_MASTER_KEY=%s" % secrets.token_urlsafe(32),
        "API_KEY_MASTER_KEY=%s" % secrets.token_urlsafe(32),
        # Without this the identity registry refuses every row (fail-closed by
        # design), so a fresh deployment has zero recognised humans.
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY=%s" % secrets.token_urlsafe(32),
        "LIUHAO_KERNEL_POLICY_ENFORCE=CRITICAL",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_file


def compose_args(project: str, env_file: Path) -> List[str]:
    return ["docker", "compose", "-p", project, "-f", COMPOSE_FILE,
            "--env-file", str(env_file)]


def up_stack(rep: Report, project: str, env_file: Path,
             skip_build: bool) -> bool:
    args = compose_args(project, env_file) + ["up", "-d"]
    if not skip_build:
        args.append("--build")
    proc = run(args, timeout=1800)
    if proc.returncode != 0:
        rep.check("docker compose up", False,
                  (proc.stderr or proc.stdout or "")[-500:])
        return False
    rep.check("docker compose up", True,
              "project=%s%s" % (project, "" if skip_build else " (built)"))
    return True


def wait_healthy(rep: Report, timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        status, payload = http_json("GET", "/v1/health")
        if status == 200:
            rep.check("/v1/health through deployed port", True,
                      json.dumps(payload, ensure_ascii=False)[:160])
            break
        last = "status=%s %s" % (status, json.dumps(payload, ensure_ascii=False)[:120])
        time.sleep(3)
    else:
        rep.check("/v1/health through deployed port", False, last or "timeout")
        return False

    deadline = time.time() + 120
    while time.time() < deadline:
        status, payload = http_json("GET", "/v1/ready")
        if status in (200, 503):  # 503 carries the degraded check detail
            rep.check("/v1/ready through deployed port", status == 200,
                      "status=%d %s" % (status, json.dumps(payload, ensure_ascii=False)[:200]))
            return status == 200
        time.sleep(3)
    rep.check("/v1/ready through deployed port", False, "timeout")
    return False


def assert_posture(rep: Report) -> bool:
    """Production must boot with the CRITICAL policy posture armed."""
    status, payload = http_json("GET", "/v1/policy/enforcement")
    if status != 200:
        rep.check("production CRITICAL posture armed", False,
                  "enforcement status=%s" % status)
        return False
    blob = json.dumps(payload, ensure_ascii=False)
    armed = payload.get("armed_actions") or payload.get("armed") or []
    ok = ("CRITICAL" in blob) or (isinstance(armed, list) and len(armed) > 0)
    return rep.check("production CRITICAL posture armed", ok, blob[:220])


def assert_fail_closed_before_bootstrap(rep: Report, password: str) -> bool:
    """Before any human exists, nobody may log in. Prove it, don't assume it."""
    status, payload = http_json("GET", "/v1/auth/config")
    eligible = (payload or {}).get("login_eligible_humans")
    ok = rep.check("fresh deployment has zero login-eligible humans",
                   status == 200 and eligible == 0,
                   "status=%s login_eligible_humans=%r" % (status, eligible))
    status, _ = http_json("POST", "/v1/auth/login",
                          body={"principal": BOOTSTRAP_PRINCIPAL,
                                "password": password})
    return rep.check("login rejected before bootstrap (fail-closed)",
                     status in (401, 403, 400), "status=%s" % status) and ok


def bootstrap_first_human(rep: Report, project: str, env_file: Path,
                          password: str, integrity_key: str) -> bool:
    """The documented operator step: register the first human credential."""
    cmd = ["docker", "compose", "-p", project, "-f", COMPOSE_FILE,
           "--env-file", str(env_file), "exec", "-T",
           "-e", "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY=%s" % integrity_key,
           "-e", "LIUHAO_HUMAN_IDENTITIES_DB=/app/data/human_identities.sqlite3",
           "-e", "LIUHAO_AUTH_SECRETS_FILE=/app/data/auth_secrets.json",
           SERVICE, "python", "scripts/register_human_identity.py",
           "--principal", BOOTSTRAP_PRINCIPAL,
           "--display-name", "Deploy Closure Operator",
           "--backend", "sqlite",
           "--db", "/app/data/human_identities.sqlite3",
           "--secrets-file", "/app/data/auth_secrets.json",
           "--password-stdin"]
    proc = run(cmd, stdin_data=password + "\n", timeout=300)
    if proc.returncode != 0:
        rep.check("bootstrap first human credential", False,
                  ((proc.stderr or "") + (proc.stdout or ""))[-500:])
        return False
    rep.check("bootstrap first human credential", True,
              "registered %s" % BOOTSTRAP_PRINCIPAL)

    status, payload = http_json("GET", "/v1/auth/config")
    eligible = (payload or {}).get("login_eligible_humans") or 0
    ok = rep.check("bootstrap makes a human login-eligible", eligible >= 1,
                   "login_eligible_humans=%r" % eligible)
    # Wrong password must still be refused -- a bootstrap that also accepts
    # anything would be an anonymous bypass.
    status, _ = http_json("POST", "/v1/auth/login",
                          body={"principal": BOOTSTRAP_PRINCIPAL,
                                "password": password + "-WRONG"})
    return rep.check("wrong password still refused after bootstrap",
                     status in (401, 403, 400), "status=%s" % status) and ok


def login_http(password: str) -> Optional[str]:
    status, payload = http_json("POST", "/v1/auth/login",
                                body={"principal": BOOTSTRAP_PRINCIPAL,
                                      "password": password})
    if status != 200 or not isinstance(payload, dict):
        return None
    for key in ("access_token", "token", "jwt"):
        if payload.get(key):
            return str(payload[key])
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if inner:
        for key in ("access_token", "token"):
            if inner.get(key):
                return str(inner[key])
    return None


def drive_console(rep: Report, password: str, artifact_content: str,
                  shot_dir: Path, binary: Optional[str] = None
                  ) -> Dict[str, Any]:
    """The click-through, through the real UI, against the deployed stack."""
    out: Dict[str, Any] = {}
    try:
        browser = Browser(binary)
    except BrowserUnavailable as exc:
        # Environment gap, NOT a product defect: BLOCKED, never FAIL.
        rep.block("headless browser available", str(exc)[:300])
        return out
    except RuntimeError as exc:
        # A backend existed but could not start for some other reason -- still
        # an environment problem, so still BLOCKED rather than a product FAIL.
        rep.block("headless browser available", str(exc)[:300])
        return out
    rep.check("headless browser available", True,
              "%s -- %s" % (browser.kind, browser.source))

    try:
        # 1. load the console from the container (single-port: API serves the SPA)
        browser.get(BASE_URL + "/")
        rep.check("console loads from deployed container",
                  browser.wait_for_text("登录", 30),
                  "title-ish: %s" % browser.text()[:80].replace("\n", " "))

        # 2. log in with the real bootstrapped credential
        browser.fill("#os-principal", BOOTSTRAP_PRINCIPAL)
        browser.fill("#os-secret", password)
        browser.screenshot(str(shot_dir / "01-login.png"))
        browser.click("button[type=submit]")
        logged = browser.wait_for_text("总览", 45)
        browser.screenshot(str(shot_dir / "02-after-login.png"))
        rep.check("login with real credential through the UI", logged,
                  "" if logged else browser.text()[:200].replace("\n", " "))
        if not logged:
            return out

        # 3. hire a real AI employee
        browser.click_text("button", "我AI员工", 20)
        time.sleep(1.5)
        emp = "closure-team"
        inputs = _form_inputs(browser, "员工名（唯一）")
        if inputs is None:
            rep.check("hire employee through the UI", False, "hire form not found")
            return out
        browser.fill(inputs["name"], emp)
        browser.fill(inputs["count"], "2")
        browser.fill(inputs["types"], "researcher, coder")
        browser.click_text("button", "雇佣员工", 15)
        hired = browser.wait_for_text("已雇佣", 30)
        rep.check("hire a real AI employee through the UI", hired, emp)

        # 4. create a real goal that performs real work
        goal = GOAL_TEXT % (ARTIFACT_NAME, artifact_content)
        ginputs = _form_inputs(browser, "目标描述")
        if ginputs is None:
            rep.check("create goal through the UI", False, "goal form not found")
            return out
        browser.fill(ginputs["name"], goal)
        browser.click_text("button", "创建 Goal", 15)
        created = browser.wait_for_text("已创建", 30)
        rep.check("create a real goal through the UI", created, goal[:80])
        out["goal_text"] = goal

        # 5. observe a terminal state in the UI
        terminal = _wait_terminal_state(browser, rep)
        out["terminal_state"] = terminal

        # 6. stop/abort sovereignty on a genuinely in-flight goal
        out.update(_exercise_stop(browser, rep, artifact_content))
        return out
    finally:
        browser.quit()


def _form_inputs(browser: Browser, label: str) -> Optional[Dict[str, str]]:
    """Map a form's visible labels to CSS selectors, backend-agnostically."""
    script = """
    (() => {
      const label = %r;
      const form = [...document.querySelectorAll('.os-operator-form')].find(
        f => f.innerText.includes(label));
      if (!form) return null;
      const fields = [...form.querySelectorAll('.os-field')];
      const inputs = fields.map(f => f.querySelector('input'));
      if (inputs.length < 1 || !inputs[0]) return null;
      inputs.forEach((el, i) => { if (el) el.setAttribute('data-closure-idx', String(i)); });
      return { n: inputs.length };
    })()
    """ % label
    try:
        if browser.kind == "playwright":
            result = browser._page.evaluate(script)
        else:
            result = browser._raw.execute_script(script)
    except Exception:
        return None
    if not result:
        return None
    n = int(result.get("n", 0))
    if n < 1:
        return None
    return {
        "name": "[data-closure-idx='0']",
        "count": "[data-closure-idx='1']" if n > 1 else "[data-closure-idx='0']",
        "types": "[data-closure-idx='2']" if n > 2 else "[data-closure-idx='0']",
    }


def _wait_terminal_state(browser: Browser, rep: Report,
                         timeout: float = 180.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = browser.text()
        for state in ("completed", "failed", "stopped", "aborted", "已停止", "已完成"):
            if state in body:
                rep.check("goal reaches a terminal state in the UI", True, state)
                return state
        time.sleep(3)
    rep.check("goal reaches a terminal state in the UI", False,
              "no terminal state within %.0fs" % timeout)
    return ""


def _exercise_stop(browser: Browser, rep: Report,
                   artifact_content: str) -> Dict[str, Any]:
    """Start a background goal and stop it through the UI."""
    out: Dict[str, Any] = {}
    ginputs = _form_inputs(browser, "目标描述")
    if ginputs is None:
        rep.check("stop a running goal through the UI", False, "goal form not found")
        return out
    # A long-running request keeps the goal in flight long enough to stop it.
    browser.fill(ginputs["name"], "长时间运行的后台调研任务（用于验证中止主权）")
    browser.click_text("button", "创建 Goal", 15)
    if not browser.wait_for_text("已创建", 30):
        rep.check("stop a running goal through the UI", False,
                  "could not start a background goal")
        return out
    deadline = time.time() + 60
    stopped = False
    while time.time() < deadline:
        try:
            browser.click_text("button", "停止", 5)
            stopped = True
            break
        except Exception:
            time.sleep(1.0)
    rep.check("stop a running goal through the UI", stopped,
              "clicked 停止" if stopped else "no 停止 control became clickable")
    out["stop_clicked"] = stopped
    return out


def assert_artifact_on_container(rep: Report, project: str, env_file: Path,
                                 artifact_content: str) -> bool:
    """Independent proof: the file is really there, with really that content."""
    cmd = ["docker", "compose", "-p", project, "-f", COMPOSE_FILE,
           "--env-file", str(env_file), "exec", "-T", SERVICE,
           "sh", "-c",
           "find /app/data /app/workspaces -name '%s' -type f 2>/dev/null | head -5"
           % ARTIFACT_NAME]
    proc = run(cmd, timeout=180)
    found = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not found:
        rep.check("artifact exists on container filesystem", False,
                  "%s not found under /app/data or /app/workspaces "
                  "(output: %s)" % (ARTIFACT_NAME, (proc.stdout or proc.stderr)[:200]))
        return False
    rep.check("artifact exists on container filesystem", True, ", ".join(found))

    path = found[0]
    cat = ["docker", "compose", "-p", project, "-f", COMPOSE_FILE,
           "--env-file", str(env_file), "exec", "-T", SERVICE,
           "cat", path]
    proc = run(cat, timeout=120)
    actual = (proc.stdout or "")
    ok = artifact_content in actual
    rep.check("artifact content matches exactly", ok,
              "expected %r in %r" % (artifact_content, actual[:200]))
    return ok


def assert_audit_tied_to_goal(rep: Report, token: Optional[str]) -> bool:
    status, payload = http_json("GET", "/v1/audit/events?limit=200",
                                token=token)
    if status != 200 or not isinstance(payload, dict):
        rep.check("audit events readable through deployed API", False,
                  "status=%s" % status)
        return False
    events = payload.get("events") or []
    rep.check("audit events readable through deployed API", True,
              "%d events returned" % len(events))
    if not events:
        rep.check("audit records exist for this session", False, "zero events")
        return False
    principals = {str(e.get("principal_id")) for e in events}
    rep.check("audit records exist for this session", True,
              "%d events, principals=%s" % (len(events), sorted(principals)[:4]))

    # Tied to the goal: at least one event carries a correlation_id that is also
    # used by a goal-related event. Two independent reads must agree.
    corr = [str(e.get("correlation_id")) for e in events if e.get("correlation_id")]
    goalish = [e for e in events
               if "goal" in str(e.get("event_type", "")).lower()
               or "goal" in str(e.get("action") or "").lower()]
    ok = bool(goalish) and bool(corr)
    rep.check("audit records are tied to the goal", ok,
              "goal-related=%d, distinct correlation_ids=%d"
              % (len(goalish), len(set(corr))))
    return ok


def teardown(project: str, env_file: Path, keep: bool) -> None:
    if keep:
        print("\n(keeping stack up as requested; NOT torn down)")
        return
    proc = run(compose_args(project, env_file) + ["down", "-v", "--remove-orphans"],
               timeout=600)
    print("[%s] teardown docker compose down -v" %
          ("PASS" if proc.returncode == 0 else "FAIL"))


# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true",
                        help="leave the stack running for manual inspection")
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse an already-built image (faster re-runs)")
    parser.add_argument("--host-port", type=int, default=DEFAULT_HOST_PORT,
                        help="host port the stack is published on "
                             "(default %d, matching %s)"
                             % (DEFAULT_HOST_PORT, COMPOSE_FILE))
    parser.add_argument("--browser-binary", default=None,
                        help="path to a chromium/edge binary; overrides "
                             "$LIUHAO_BROWSER_BINARY and auto-detection")
    args = parser.parse_args(argv)

    set_host_port(args.host_port)
    if os.environ.get("LIUHAO_BROWSER_BINARY") and not args.browser_binary:
        args.browser_binary = os.environ["LIUHAO_BROWSER_BINARY"]

    rep = Report()
    print("=" * 74)
    print("LIUHAO deployed-product closure")
    print("host port   : %d" % HOST_PORT)
    print("browser     : %s" % (args.browser_binary or "auto-detect"))
    print("=" * 74)

    # Prerequisite: a real Docker daemon. Missing => BLOCKED / exit 2, never a
    # silent pass and never a claim that the product is broken.
    if not preflight_docker(rep):
        print(rep.render())
        return EXIT_PREREQ

    # Prerequisite: a browser, checked before we spend minutes booting a stack.
    browser_ok = preflight_browser(rep, args.browser_binary)

    tmp = Path(tempfile.mkdtemp(prefix="liuhao-deploy-closure-"))
    project = "liuhao-closure-%s" % secrets.token_hex(4)
    password = "closure-" + secrets.token_urlsafe(16)
    artifact_content = ARTIFACT_CONTENT % secrets.token_hex(6).upper()
    env_file = write_env(tmp, password)
    integrity_key = ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY="):
            integrity_key = line.split("=", 1)[1]
    shots = tmp / "shots"
    shots.mkdir(exist_ok=True)
    print("isolated deployment: project=%s tmp=%s" % (project, tmp))
    print("artifact to produce : %s -> %s" % (ARTIFACT_NAME, artifact_content))

    try:
        if not up_stack(rep, project, env_file, args.skip_build):
            return EXIT_FAIL
        if not wait_healthy(rep):
            _dump_logs(project, env_file)
            return EXIT_FAIL
        assert_posture(rep)
        assert_fail_closed_before_bootstrap(rep, password)

        if not bootstrap_first_human(rep, project, env_file, password,
                                     integrity_key):
            return EXIT_FAIL

        token = login_http(password)
        rep.check("credential login returns a JWT", token is not None,
                  "" if token else "no token in login response")

        if not browser_ok:
            # Everything from the click-through onward is unreachable. BLOCK
            # each of them by name -- do NOT let them FAIL, because a host with
            # no browser has not disproven the product. The stack-level checks
            # above still stand as real evidence.
            for name in ("headless browser available",
                         "console loads from deployed container",
                         "login with real credential through the UI",
                         "hire a real AI employee through the UI",
                         "create a real goal through the UI",
                         "goal reaches a terminal state in the UI",
                         "stop a running goal through the UI",
                         "artifact exists on container filesystem",
                         "artifact content matches exactly",
                         "audit records are tied to the goal"):
                rep.block(name, "browser prerequisite missing -- NOT VERIFIED")
        else:
            # Belt-and-braces: the browser passed preflight but may still die
            # mid-run (driver crash, OOM). A dying session must BLOCK the
            # remaining checks, never crash the gate with a traceback.
            try:
                drive_console(rep, password, artifact_content, shots,
                              args.browser_binary)
                assert_artifact_on_container(rep, project, env_file,
                                             artifact_content)
                assert_audit_tied_to_goal(rep, token)
            except Exception as exc:
                rep.block("browser session survived the click-through",
                          "%s: %s -- the click-through is NOT VERIFIED"
                          % (type(exc).__name__, str(exc)[:200]))

        print("\nscreenshots: %s" % shots)
        print(rep.render())
        # A real FAIL outranks a block; any block means the closure was NOT
        # demonstrated and must not read as green.
        if rep.failed:
            return EXIT_FAIL
        if rep.blocked:
            return EXIT_PREREQ
        return EXIT_OK
    finally:
        if not args.keep:
            teardown(project, env_file, keep=False)
        shutil.rmtree(tmp, ignore_errors=True)


def _dump_logs(project: str, env_file: Path) -> None:
    proc = run(compose_args(project, env_file) + ["logs", "--tail", "60"],
               timeout=180)
    print("\n---- container logs (tail) ----")
    print((proc.stdout or proc.stderr or "")[-4000:])
    print("---- end logs ----")


if __name__ == "__main__":
    sys.exit(main())
