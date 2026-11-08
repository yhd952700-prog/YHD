"""Real-application-path verification for audit-DB space reclamation.

Unlike ``repro_audit_reclaim.py`` (which drives the store directly), this script
starts the actual FastAPI gateway under uvicorn in a subprocess and produces
audit writes through a real HTTP endpoint (``POST /v1/auth/logout`` -> the auth
module's ``_audit`` -> kernels ``AuditStore.log_event``). It then measures what
an operator can observe: file bytes, WAL bytes, auto_vacuum, freelist_count,
row counts and request latency.

Parts:
  part1   real gateway + retention prune: shrink, WAL -> 0, auto_vacuum=2,
          freelist=0, p50/p95 latency before vs during pruning
  part2a  concurrent writers while pruning: no errors, chain + integrity OK
  part2b  hard-kill (TerminateProcess) while the DB is mid-reclamation:
          restart, integrity_check, every acknowledged row still present
  part3   legacy DB (auto_vacuum=0, large freelist) -> app start -> prune:
          VACUUM only when reclaiming, plus simulated disk-full failure
          (writes keep working, backoff retry succeeds later)

Run:
    .venv\\Scripts\\python.exe scripts\\repro_audit_realapp.py part1
    .venv\\Scripts\\python.exe scripts\\repro_audit_realapp.py part2a
    .venv\\Scripts\\python.exe scripts\\repro_audit_realapp.py part2b
    .venv\\Scripts\\python.exe scripts\\repro_audit_realapp.py part3
    .venv\\Scripts\\python.exe scripts\\repro_audit_realapp.py all
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JWT_SECRET = "repro-realapp-jwt-secret-" + "0" * 32


# ---------------------------------------------------------------------------
# harness helpers
# ---------------------------------------------------------------------------
def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def base_env(data_dir: str, db_path: str) -> dict:
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(ROOT),
        "AUDIT_DB_PATH": db_path,
        "LIUHAO_DATA_DIR": data_dir,
        "LIUHAO_JWT_SECRET": JWT_SECRET,
        # Keep every test self-contained: a short retention loop so a prune
        # really happens during the run, and a huge delete budget so one pass
        # reclaims everything above the ceiling (the shrinking case).
        "LIUHAO_AUDIT_RETENTION_ENABLED": "1",
        "LIUHAO_AUDIT_RETENTION_DAYS": "0",
        "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "2000",
        "LIUHAO_AUDIT_RETENTION_MAX_BYTES": "0",
        "LIUHAO_AUDIT_RETENTION_MIN_KEEP": "1",
        "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "100000",
        "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "0.5",
    })
    return env


class Gateway:
    def __init__(self, env: dict, log_path: Path):
        self.port = free_port()
        self._log = open(log_path, "wb")
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "--factory", "src.gateway.main:get_app",
             "--host", "127.0.0.1", "--port", str(self.port),
             "--log-level", "warning"],
            env=env, cwd=str(ROOT), stdout=self._log, stderr=subprocess.STDOUT,
        )

    def wait_ready(self, timeout: float = 90.0) -> float:
        started = time.time()
        while time.time() - started < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"gateway exited early rc={self.proc.returncode}")
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1)
                conn.request("GET", "/v1/health")
                resp = conn.getresponse()
                resp.read()
                conn.close()
                if resp.status == 200:
                    return time.time() - started
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError("gateway did not become ready")

    def stop(self, hard: bool = False) -> None:
        if self.proc.poll() is None:
            if hard:
                self.proc.kill()
            else:
                self.proc.terminate()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(10)
        self._log.close()


def mint_token() -> str:
    os.environ["LIUHAO_JWT_SECRET"] = JWT_SECRET
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token("repro-human")
    return token


def logout_once(port: int, token: str, conn=None, timeout: float = 30.0) -> float:
    """One real audited HTTP write; returns wall latency in milliseconds.

    Pass ``conn`` to reuse a keep-alive connection (measures the server-side
    write, not TCP handshakes).
    """
    own = conn is None
    if own:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    started = time.perf_counter()
    try:
        conn.request("POST", "/v1/auth/logout", "{}", {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        })
        resp = conn.getresponse()
        resp.read()
        if resp.status != 200:
            raise RuntimeError(f"logout status {resp.status}")
    finally:
        if own:
            conn.close()
    return (time.perf_counter() - started) * 1000.0


class MultiClient:
    """Keep-alive clients spread over distinct loopback source IPs.

    The gateway enforces ``server.limits.rate_limit_per_minute`` (300/min per
    client IP by default). Rotating the loopback source address keeps the
    production config untouched while allowing a realistic write volume; every
    request is still a real HTTP call to the real app.

    Sockets idle longer than uvicorn's keep-alive timeout are closed by the
    server, so a pooled connection can fail with WinError 10053/10054. That is
    a client-pool artefact, not a server error: reconnect once with the same
    source IP and re-issue the request. Only the successful attempt's latency
    is recorded (the dropped socket's time is not attributed to the write).
    """

    MAX_ATTEMPTS = 3

    def __init__(self, port: int, token: str, count: int = 32, first: int = 2):
        self.port = port
        self.token = token
        self.first = first
        self.conns = [self._new_conn(i) for i in range(count)]
        self.i = 0

    def _new_conn(self, index: int) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=60,
            source_address=(f"127.0.0.{self.first + index}", 0),
        )

    def write(self) -> float:
        index = self.i
        self.i = (self.i + 1) % len(self.conns)
        for attempt in range(self.MAX_ATTEMPTS):
            conn = self.conns[index]
            try:
                return logout_once(self.port, self.token, conn)
            except (http.client.HTTPException, OSError) as exc:
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise
                try:
                    conn.close()
                except OSError:
                    pass
                self.conns[index] = self._new_conn(index)
                if not isinstance(exc, (ConnectionError, http.client.HTTPException)):
                    raise
        raise RuntimeError("unreachable")

    def close(self) -> None:
        for conn in self.conns:
            try:
                conn.close()
            except OSError:
                pass


def db_snapshot(db_path: str, with_integrity: bool = False) -> dict:
    """Read the audit DB the way an operator would: a separate connection.

    ``with_integrity`` runs ``PRAGMA integrity_check``, which scans the whole
    file; keep it off for the 50 ms monitor polls or the measurement itself
    becomes the bottleneck.
    """
    out = {"db": 0, "wal": 0, "shm": 0, "rows": -1, "free": -1, "av": -1}
    for suffix, key in (("", "db"), ("-wal", "wal"), ("-shm", "shm")):
        try:
            out[key] = os.path.getsize(db_path + suffix)
        except OSError:
            out[key] = 0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            out["rows"] = int(conn.execute(
                "SELECT COUNT(*) FROM audit_events").fetchone()[0])
            out["free"] = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            out["av"] = int(conn.execute("PRAGMA auto_vacuum").fetchone()[0])
            if with_integrity:
                out["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        out["error"] = str(exc)
    return out


def pct(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


class Monitor(threading.Thread):
    """Poll DB bytes/rows/freelist like an operator watching the data dir."""

    def __init__(self, db_path: str, interval: float = 0.05):
        super().__init__(daemon=True)
        self.db_path = db_path
        self.interval = interval
        self.samples = []
        self.prune_events = 0
        self.peak_db = 0
        self._stop_event = threading.Event()

    def run(self):
        last_rows = None
        while not self._stop_event.is_set():
            snap = db_snapshot(self.db_path)
            self.samples.append(snap)
            self.peak_db = max(self.peak_db, snap["db"])
            if last_rows is not None and snap["rows"] >= 0 and snap["rows"] < last_rows:
                self.prune_events += 1
            if snap["rows"] >= 0:
                last_rows = snap["rows"]
            time.sleep(self.interval)

    def stop(self):
        self._stop_event.set()
        self.join(timeout=5)


# ---------------------------------------------------------------------------
# part 1
# ---------------------------------------------------------------------------
def part1() -> bool:
    print("\n=== PART 1: real gateway, retention prune, latency ===")
    root = Path(tempfile.mkdtemp(prefix="liuhao-realapp-p1-"))
    db = str(root / "audit.db")

    # --- Phase A: same gateway workload with retention OFF (no pruning) ------
    env_a = base_env(str(root), db)
    env_a["LIUHAO_AUDIT_RETENTION_ENABLED"] = "0"
    env_a["LIUHAO_AUDIT_WAL_TRUNCATE_MIN_BYTES"] = "1"
    gw = Gateway(env_a, root / "gateway-a.log")
    try:
        ready = gw.wait_ready()
        token = mint_token()
        client = MultiClient(gw.port, token)
        logout_once(gw.port, token, client.conns[0])
        monitor = Monitor(db)
        monitor.start()
        lat_a = []
        started = time.perf_counter()
        for _ in range(4000):
            lat_a.append(client.write())
        elapsed_a = time.perf_counter() - started
        monitor.stop()
        client.close()
        end_a = db_snapshot(db, with_integrity=True)
        print(f"[A no-prune] ready={ready:.1f}s requests=4000 in {elapsed_a:.1f}s "
              f"({4000 / elapsed_a:.0f} req/s)")
        print(f"[A no-prune] rows={end_a['rows']} db={end_a['db']}B peak_db="
              f"{monitor.peak_db}B wal={end_a['wal']}B peak_wal="
              f"{max(s['wal'] for s in monitor.samples)}B av={end_a['av']} "
              f"freelist={end_a['free']}")
        print(f"[A no-prune] latency p50={pct(lat_a,50):.1f}ms "
              f"p95={pct(lat_a,95):.1f}ms p99={pct(lat_a,99):.1f}ms "
              f"max={max(lat_a):.1f}ms >250ms={sum(1 for x in lat_a if x > 250)}")
        if end_a["av"] != 2 or end_a.get("integrity") != "ok":
            print("FAIL: phase A database invariants")
            return False
    finally:
        gw.stop()

    # --- Phase B: restart the SAME gateway/data with retention ON -----------
    env_b = base_env(str(root), db)
    env_b["LIUHAO_AUDIT_RETENTION_MAX_ROWS"] = "1000"
    env_b["LIUHAO_AUDIT_WAL_TRUNCATE_MIN_BYTES"] = "1"
    gw = Gateway(env_b, root / "gateway-b.log")
    try:
        gw.wait_ready()
        token = mint_token()
        client = MultiClient(gw.port, token)
        start_b = db_snapshot(db)
        monitor = Monitor(db)
        monitor.start()
        lat_b = []
        started = time.perf_counter()
        for _ in range(4000):
            lat_b.append(client.write())
        elapsed_b = time.perf_counter() - started
        time.sleep(1.5)
        # One more write triggers the final throttled maintenance pass, which
        # truncates the WAL (LIUHAO_AUDIT_WAL_TRUNCATE_MIN_BYTES=1) and leaves
        # no freed pages behind.
        client.write()
        time.sleep(0.5)
        monitor.stop()
        client.close()
        final = db_snapshot(db, with_integrity=True)
        print(f"[B prune] ready, rows_on_start={start_b['rows']} db_on_start="
              f"{start_b['db']}B wal_on_start={start_b['wal']}B")
        print(f"[B prune] requests=4000 in {elapsed_b:.1f}s ({4000 / elapsed_b:.0f} req/s) "
              f"prune_cycles={monitor.prune_events}")
        print(f"[B prune] rows={final['rows']} db={final['db']}B "
              f"min_db_after_prune={min(s['db'] for s in monitor.samples)}B "
              f"peak_db={monitor.peak_db}B")
        print(f"[B prune] wal={final['wal']}B peak_wal="
              f"{max(s['wal'] for s in monitor.samples)}B "
              f"auto_vacuum={final['av']} freelist={final['free']} "
              f"integrity={final.get('integrity')}")
        print(f"[B prune] latency p50={pct(lat_b,50):.1f}ms p95={pct(lat_b,95):.1f}ms "
              f"p99={pct(lat_b,99):.1f}ms max={max(lat_b):.1f}ms "
              f">250ms={sum(1 for x in lat_b if x > 250)}")

        shrank = min(s['db'] for s in monitor.samples) < start_b['db']
        ok = (
            final["av"] == 2
            and final["free"] == 0
            and final["wal"] == 0
            and final.get("integrity") == "ok"
            and monitor.prune_events >= 1
            and shrank
            and pct(lat_b, 95) < 250
        )
        print(f"p95 delta (prune vs no-prune) = {pct(lat_b,95) - pct(lat_a,95):+.1f}ms")
        print("PASS" if ok else "FAIL")
        return ok
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# part 2a
# ---------------------------------------------------------------------------
def part2a() -> bool:
    print("\n=== PART 2a: concurrent writers while pruning ===")
    root = Path(tempfile.mkdtemp(prefix="liuhao-realapp-p2a-"))
    db = str(root / "audit.db")
    gw = Gateway(base_env(str(root), db), root / "gateway.log")
    try:
        gw.wait_ready()
        token = mint_token()
        logout_once(gw.port, token)

        monitor = Monitor(db)
        monitor.start()
        errors = []
        latencies = []
        lock = threading.Lock()
        per_thread = 700
        threads_n = 8

        def worker(tid: int):
            local = []
            client = MultiClient(gw.port, token, count=4, first=2 + tid * 4)
            try:
                for _ in range(per_thread):
                    try:
                        local.append(client.write())
                    except Exception as exc:  # noqa: BLE001 - report, do not hide
                        with lock:
                            errors.append(f"t{tid}: {exc!r}")
                        return
            finally:
                client.close()
            with lock:
                latencies.extend(local)

        started = time.perf_counter()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - started
        time.sleep(1.5)
        logout_once(gw.port, token)
        time.sleep(1.0)
        monitor.stop()

        final = db_snapshot(db, with_integrity=True)
        from src.kernels.audit import AuditStore

        store = AuditStore(db)
        try:
            verified, count = store.verify_integrity()
        finally:
            store._conn.close()

        print(f"requests={threads_n * per_thread} in {elapsed:.1f}s; "
              f"errors={len(errors)} {errors[:3]}")
        print(f"prune cycles observed={monitor.prune_events}")
        print(f"latency: p50={pct(latencies,50):.1f}ms p95={pct(latencies,95):.1f}ms "
              f"p99={pct(latencies,99):.1f}ms max={max(latencies):.1f}ms")
        print(f"rows={final['rows']} db={final['db']}B wal={final['wal']}B "
              f"av={final['av']} freelist={final['free']} integrity={final.get('integrity')}")
        print(f"hash-chain verify_integrity={verified} events={count}")

        ok = (
            not errors
            and final["av"] == 2
            and final["free"] == 0
            and final["wal"] == 0
            and final.get("integrity") == "ok"
            and verified
            and monitor.prune_events >= 1
        )
        print("PASS" if ok else "FAIL")
        return ok
    finally:
        gw.stop()


# ---------------------------------------------------------------------------
# part 2b: hard kill during reclamation (parent/child)
# ---------------------------------------------------------------------------
def _crash_child(db: str, ack_path: str) -> int:
    """Write events forever; fsync each acknowledged id to the ack file."""
    from src.kernels.audit import AuditEventType, AuditScope, AuditStore

    store = AuditStore(db)
    fh = open(ack_path, "a", encoding="utf-8")
    while True:
        event = store.log_event(
            AuditEventType.STATE_CHANGE, "crash-probe", AuditScope.L0,
            "allow", {"pad": "y" * 180},
        )
        fh.write(event.event_id + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def part2b() -> bool:
    print("\n=== PART 2b: hard kill during reclamation, restart ===")
    root = Path(tempfile.mkdtemp(prefix="liuhao-realapp-p2b-"))
    db = str(root / "audit.db")
    ack = str(root / "ack.txt")
    env = base_env(str(root), db)
    env.update({
        "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "200",
        "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "100000",
        "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "0.05",
    })
    child = subprocess.Popen(
        [PYTHON, str(Path(__file__)), "--crash-child", db, ack],
        env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    killed_mid_reclaim = False
    try:
        deadline = time.time() + 120
        acked = 0
        while time.time() < deadline:
            if os.path.exists(ack):
                with open(ack, encoding="utf-8") as fh:
                    acked = sum(1 for _ in fh)
            if acked >= 600:
                # Kill the instant the DELETE has committed but the freed pages
                # are still being returned (freelist > 0).
                if db_snapshot(db).get("free", 0) > 0:
                    child.kill()
                    killed_mid_reclaim = True
                    break
            if child.poll() is not None:
                print(f"FAIL: child exited early rc={child.returncode}")
                return False
            time.sleep(0.003)
        if not killed_mid_reclaim:
            child.kill()
        child.wait(15)

        with open(ack, encoding="utf-8") as fh:
            acked_ids = [line.strip() for line in fh if line.strip()]

        before = db_snapshot(db)
        conn = sqlite3.connect(db, timeout=10)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            missing = [
                eid for eid in acked_ids
                if conn.execute(
                    "SELECT 1 FROM audit_events WHERE event_id = ?", (eid,)
                ).fetchone() is None
            ]
            rows = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        finally:
            conn.close()

        from src.kernels.audit import AuditStore

        store = AuditStore(db)
        try:
            verified, count = store.verify_integrity()
        finally:
            store._conn.close()

        print(f"killed mid-reclaim={killed_mid_reclaim}; acked writes={len(acked_ids)}")
        print(f"after restart: integrity={integrity} rows={rows} "
              f"acked_missing={len(missing)} {missing[:3]}")
        print(f"db={before['db']}B wal={before['wal']}B av={before['av']} "
              f"freelist={before['free']} chain_ok={verified}")
        ok = (
            killed_mid_reclaim
            and integrity == "ok"
            and not missing
            and verified
        )
        print("PASS" if ok else "FAIL")
        return ok
    finally:
        if child.poll() is None:
            child.kill()


# ---------------------------------------------------------------------------
# part 3: legacy migration + simulated disk-full
# ---------------------------------------------------------------------------
def _make_legacy_db(path: str, rows: int, retention_env: dict) -> None:
    """Create a DB whose header was frozen at auto_vacuum=NONE (old order)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")  # materialises header, av=0
    conn.execute("CREATE TABLE _legacy_probe (x INTEGER)")
    conn.execute("INSERT INTO _legacy_probe VALUES (1)")
    conn.commit()
    conn.close()

    saved = {k: os.environ.get(k) for k in retention_env}
    os.environ.update(retention_env)
    try:
        from src.kernels.audit import AuditEventType, AuditScope, AuditStore

        store = AuditStore(path)
        try:
            for i in range(rows):
                store.log_event(
                    AuditEventType.STATE_CHANGE, "legacy-probe", AuditScope.L0,
                    "allow", {"i": i, "pad": "z" * 160},
                )
        finally:
            store._conn.close()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def part3() -> bool:
    print("\n=== PART 3: legacy DB (auto_vacuum=0 + freelist) migration ===")
    root = Path(tempfile.mkdtemp(prefix="liuhao-realapp-p3-"))
    legacy = str(root / "audit.db")
    _make_legacy_db(legacy, 12000, {
        "LIUHAO_AUDIT_RETENTION_ENABLED": "1",
        "LIUHAO_AUDIT_RETENTION_DAYS": "0",
        "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "2000",
        "LIUHAO_AUDIT_RETENTION_MAX_BYTES": "0",
        "LIUHAO_AUDIT_RETENTION_MIN_KEEP": "1",
        "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "100000",
        "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "0",
    })

    legacy_before = db_snapshot(legacy)
    print(f"legacy DB: rows={legacy_before['rows']} db={legacy_before['db']}B "
          f"wal={legacy_before['wal']}B av={legacy_before['av']} "
          f"freelist={legacy_before['free']} pages")
    if legacy_before["av"] != 0 or legacy_before["free"] <= 0:
        print("FAIL: fixture is not a legacy freelist-heavy DB")
        return False

    # Opening the store alone must NOT VACUUM (no reclaim is needed yet).
    from src.kernels.audit import AuditStore

    opened = AuditStore(legacy)
    try:
        pending = opened._auto_vacuum_pending
        opened_mode = int(opened._conn.execute("PRAGMA auto_vacuum").fetchone()[0])
    finally:
        opened._conn.close()
    after_open = db_snapshot(legacy)
    print(f"open only: pending={pending} av={opened_mode} "
          f"db={after_open['db']}B (unchanged={after_open['db'] == legacy_before['db']})")

    gw = Gateway(base_env(str(root), legacy), root / "gateway.log")
    try:
        gw.wait_ready()
        token = mint_token()
        logout_once(gw.port, token)
        time.sleep(2.0)
        logout_once(gw.port, token)
        time.sleep(1.0)
        final = db_snapshot(legacy, with_integrity=True)
        print(f"after app start + prune: rows={final['rows']} db={final['db']}B "
              f"wal={final['wal']}B av={final['av']} freelist={final['free']} "
              f"integrity={final.get('integrity')}")
        ok = (
            after_open["db"] == legacy_before["db"]
            and opened_mode == 0
            and final["av"] == 2
            and final["free"] == 0
            and final["wal"] == 0
            and final["db"] < legacy_before["db"]
            and final.get("integrity") == "ok"
        )
        print("PASS" if ok else "FAIL")
        if not ok:
            return False
    finally:
        gw.stop()

    return _part3_disk_full_failure(root)


def _part3_disk_full_failure(root: Path) -> bool:
    """VACUUM fails with 'disk full': writes keep working, retry succeeds."""
    print("\n--- PART 3b: simulated disk-full during VACUUM migration ---")
    legacy = str(root / "diskfull.db")
    _make_legacy_db(legacy, 4000, {
        "LIUHAO_AUDIT_RETENTION_ENABLED": "1",
        "LIUHAO_AUDIT_RETENTION_DAYS": "0",
        "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "1000",
        "LIUHAO_AUDIT_RETENTION_MAX_BYTES": "0",
        "LIUHAO_AUDIT_RETENTION_MIN_KEEP": "1",
        "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "100000",
        "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "0",
    })
    before = db_snapshot(legacy)
    print(f"legacy DB: rows={before['rows']} db={before['db']}B av={before['av']} "
          f"freelist={before['free']}")

    os.environ.update({
        "LIUHAO_AUDIT_RETENTION_ENABLED": "1",
        "LIUHAO_AUDIT_RETENTION_DAYS": "0",
        "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "1000",
        "LIUHAO_AUDIT_RETENTION_MAX_BYTES": "0",
        "LIUHAO_AUDIT_RETENTION_MIN_KEEP": "1",
        "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "100000",
        "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "0",
        "LIUHAO_AUDIT_AUTO_VACUUM_RETRY_SEC": "3",
    })
    from src.kernels.audit import AuditEventType, AuditScope, AuditStore

    store = AuditStore(legacy)
    attempts = {"vacuum": 0}
    original = store._conn

    class DiskFullProxy:
        """Delegate to the real connection, failing VACUUM like a full disk."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            if str(sql).strip().upper().startswith("VACUUM"):
                attempts["vacuum"] += 1
                raise sqlite3.OperationalError("database or disk is full")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    store._conn = DiskFullProxy(original)
    try:
        store._retention_last_check = 0.0
        store._maybe_prune()  # prune runs, VACUUM fails, must not raise
        mode_after_failure = int(original.execute("PRAGMA auto_vacuum").fetchone()[0])
        first_attempts = attempts["vacuum"]

        latencies = []
        for i in range(200):
            started = time.perf_counter()
            store.log_event(
                AuditEventType.STATE_CHANGE, "diskfull-probe", AuditScope.L0,
                "allow", {"i": i},
            )
            latencies.append((time.perf_counter() - started) * 1000.0)

        # Backoff: a second maintenance pass before retry_sec must not retry.
        store._retention_last_check = 0.0
        store._maybe_prune()
        attempts_during_backoff = attempts["vacuum"]
        print(f"vacuum attempts={first_attempts} av_after_failure={mode_after_failure} "
              f"writes_ok={len(latencies)} p50={pct(latencies,50):.1f}ms "
              f"p95={pct(latencies,95):.1f}ms")
        print(f"retry during backoff: attempts {first_attempts} -> "
              f"{attempts_during_backoff} (expected unchanged)")

        time.sleep(3.4)  # let the backoff expire
        store._retention_last_check = 0.0
        store._maybe_prune()
        mode_after_retry = int(original.execute("PRAGMA auto_vacuum").fetchone()[0])
        final = db_snapshot(legacy, with_integrity=True)
        print(f"after retry: vacuum_attempts={attempts['vacuum']} "
              f"av={mode_after_retry} db={before['db']}B -> {final['db']}B "
              f"wal={final['wal']}B freelist={final['free']} "
              f"integrity={final.get('integrity')}")

        ok = (
            first_attempts == 1
            and mode_after_failure == 0
            and attempts_during_backoff == 1
            and mode_after_retry == 2
            and final["db"] < before["db"]
            and final["wal"] == 0
            and final["free"] == 0
            and final.get("integrity") == "ok"
        )
        print("PASS" if ok else "FAIL")
        return ok
    finally:
        store._conn = original
        original.close()


# ---------------------------------------------------------------------------
def main(argv) -> int:
    if len(argv) > 1 and argv[1] == "--crash-child":
        return _crash_child(argv[2], argv[3])
    part = argv[1] if len(argv) > 1 else "all"
    results = {}
    if part in ("part1", "all"):
        results["part1"] = part1()
    if part in ("part2a", "all"):
        results["part2a"] = part2a()
    if part in ("part2b", "all"):
        results["part2b"] = part2b()
    if part in ("part3", "all"):
        results["part3"] = part3()
    print("\nSUMMARY:", json.dumps(results))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
