import http.client, os, socket, sqlite3, subprocess, sys, tempfile, threading, time
sys.path.insert(0, r"d:\LiuHao-AI-OS")
SECRET = "repro-realapp-jwt-secret-" + "0" * 32
os.environ["LIUHAO_JWT_SECRET"] = SECRET
d = tempfile.mkdtemp(); db = os.path.join(d, "audit.db")
env = dict(os.environ); env.update({
    "PYTHONPATH": r"d:\LiuHao-AI-OS", "AUDIT_DB_PATH": db, "LIUHAO_DATA_DIR": d,
    "LIUHAO_AUDIT_RETENTION_ENABLED": "1",
    "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "2000",
    "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "0.5",
    "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "100000",
})
s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
p = subprocess.Popen([r"d:\LiuHao-AI-OS\.venv\Scripts\python.exe", "-m", "uvicorn", "--factory", "src.gateway.main:get_app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"], env=env, cwd=r"d:\LiuHao-AI-OS", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(100):
    try:
        cw = http.client.HTTPConnection("127.0.0.1", port, timeout=1); cw.request("GET", "/v1/health"); cw.getresponse().read(); cw.close(); break
    except OSError: time.sleep(0.2)
from src.security import get_jwt_handler
tok, _ = get_jwt_handler().create_token("probe")
c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
lats = []; spans = []; drops = []
stop = threading.Event()
def mon():
    last = None
    while not stop.is_set():
        try:
            q = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            n = q.execute("select count(*) from audit_events").fetchone()[0]
            q.close()
            if last is not None and n < last:
                drops.append(time.time())
            last = n
        except Exception:
            pass
        time.sleep(0.01)
th = threading.Thread(target=mon, daemon=True); th.start()
t0 = time.time()
for i in range(4000):
    st = time.perf_counter()
    c.request("POST", "/v1/auth/logout", "{}", {"Content-Type": "application/json", "Authorization": "Bearer " + tok})
    r = c.getresponse(); body = r.read()
    assert r.status == 200, (r.status, body[:200])
    lats.append((time.perf_counter() - st) * 1000)
    spans.append((time.time() - st, time.time()))
el = time.time() - t0
stop.set(); th.join(2)
srt = sorted(lats)
overlap = sum(1 for (a, b), ms in zip(spans, lats) if any(a <= t <= b for t in drops))
slow_overlap = sum(1 for (a, b), ms in zip(spans, lats) if ms > 100 and any(a <= t <= b for t in drops))
print("4000 req %.2fs -> %.1f req/s; drops=%d" % (el, 4000 / el, len(drops)))
print("requests overlapping a prune: %d; slow(>100ms) overlapping prune: %d" % (overlap, slow_overlap))
print("p50 %.1f p95 %.1f p99 %.1f max %.1f" % (srt[2000], srt[3800], srt[3960], srt[-1]))
print("slow>100ms:", sum(1 for x in lats if x > 100), [round(x) for x in srt[-8:]])
p.terminate()
try: p.wait(10)
except Exception: p.kill()
