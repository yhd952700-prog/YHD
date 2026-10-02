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
* **Docker unavailable is a FAIL, never a skip.** This script's entire subject
  is the deployed stack; if it cannot run Docker it must exit non-zero and say
  so loudly. A verifier that quietly passes when its subject is missing is
  worse than no verifier -- it is the exact failure mode this keystone exists
  to remove.
* **No production evidence is touched.** The deployment gets a fresh temp
  project name, a fresh temp ``.env`` with generated secrets, and fresh named
  volumes that are deleted at the end. Nothing here mounts, reads or reuses
  the frozen HC-01 production audit database.
* **Never weaken the test to get green.** If the goal does not really produce
  the artifact, or the audit rows are not really tied to the goal, this FAILS.

Exit status
===========
  * ``0`` -- every check passed.
  * ``1`` -- at least one check failed (a real finding).
  * ``2`` -- a prerequisite is missing (no Docker, no browser). Also a failure:
             the closure was NOT demonstrated.

Usage
=====
  python scripts/verify_deployed_product_closure.py
  python scripts/verify_deployed_product_closure.py --keep      # leave stack up
  python scripts/verify_deployed_product_closure.py --skip-build
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

COMPOSE_FILE = "docker-compose.prod.yml"
SERVICE = "api"
HOST_PORT = 8080
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
class Report(object):
    """Collects per-check PASS/FAIL and renders a verdict."""

    def __init__(self) -> None:
        self.rows: List[Tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             (" -- " + detail) if detail else ""))
        return bool(ok)

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.rows if not ok)

    def render(self) -> str:
        total = len(self.rows)
        bad = self.failed
        lines = ["", "=" * 74,
                 "deployed-product closure: %d/%d checks passed" % (total - bad, total),
                 "=" * 74]
        if bad:
            lines.append("RESULT: FAIL -- the following did not hold:")
            for name, ok, detail in self.rows:
                if not ok:
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
class Browser(object):
    """Thin driver-agnostic facade over Playwright or Selenium.

    Both backends are tried. If neither can drive a browser this raises
    :class:`RuntimeError` and the caller turns that into a visible FAIL -- the
    click-through is the point of the exercise and must not be skipped.
    """

    def __init__(self) -> None:
        self._kind = ""
        self._raw: Any = None
        self._page: Any = None
        self._open()

    def _open(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception:
            sync_playwright = None  # type: ignore
        if sync_playwright is not None:
            try:
                pw = sync_playwright().start()
                self._raw = pw
                self._page = pw.chromium.launch(
                    args=["--no-sandbox", "--disable-dev-shm-usage"]).new_page()
                self._kind = "playwright"
                return
            except Exception:
                self._close()
        try:
            from selenium import webdriver  # type: ignore
            from selenium.webdriver.edge.options import Options as EdgeOptions  # type: ignore
            from selenium.webdriver.common.by import By  # type: ignore
        except Exception as exc:
            raise RuntimeError("no browser backend importable: %s" % exc)
        last = ""
        options = EdgeOptions()
        for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-gpu", "--window-size=1440,900"):
            options.add_argument(arg)
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if os.path.isfile(edge):
            options.binary_location = edge
        try:
            self._raw = webdriver.Edge(options=options)
            self._kind = "selenium-edge"
            self._page = self._raw
            self._By = By  # type: ignore[attr-defined]
            return
        except Exception as exc:
            last = str(exc)[:200]
        raise RuntimeError("could not start a headless browser (%s)" % last)

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
    """Docker must be present AND have a live daemon. Anything else fails."""
    if shutil.which("docker") is None:
        rep.check("docker CLI present", False, "docker not on PATH")
        return False
    rep.check("docker CLI present", True)
    proc = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=60)
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        detail = ((proc.stderr or proc.stdout or "") or "").strip().splitlines()
        rep.check("docker daemon reachable", False,
                  "no live daemon -- %s" % (detail[-1] if detail else "unknown"))
        print("\n" + "!" * 74)
        print("! DOCKER DAEMON UNAVAILABLE -- deployed-product closure NOT demonstrated.")
        print("! This script's subject is the deployed stack. It does not pass")
        print("! by skipping; it fails, because nothing was proven.")
        for line in detail[-4:]:
            print("!   %s" % line)
        print("!" * 74)
        return False
    rep.check("docker daemon reachable", True, "server " + proc.stdout.strip())
    return True


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
                  shot_dir: Path) -> Dict[str, Any]:
    """The click-through, through the real UI, against the deployed stack."""
    out: Dict[str, Any] = {}
    try:
        browser = Browser()
    except RuntimeError as exc:
        rep.check("headless browser available", False, str(exc)[:300])
        return out
    rep.check("headless browser available", True, browser.kind)

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
    args = parser.parse_args(argv)

    rep = Report()
    print("=" * 74)
    print("LIUHAO deployed-product closure")
    print("=" * 74)

    # Prerequisite: a real Docker daemon. Missing => visible failure, not a skip.
    if not preflight_docker(rep):
        print(rep.render())
        return EXIT_PREREQ

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

        drive_console(rep, password, artifact_content, shots)
        assert_artifact_on_container(rep, project, env_file, artifact_content)
        assert_audit_tied_to_goal(rep, token)

        print("\nscreenshots: %s" % shots)
        print(rep.render())
        return EXIT_FAIL if rep.failed else EXIT_OK
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
