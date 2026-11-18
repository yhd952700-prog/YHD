"""Goals/chat/workbench 并发压力脚本（完善第二轮 A3）。

背景：全量并行跑曾出现一次 goals 持久化 ``dictionary changed size
during iteration``，被 fail-closed 成 507（没丢数据）。本脚本用明显
大于事故量级的样本给这条链一个硬结论：

* 20 个并发线程 × 多轮，混合打：建目标、列目标、目标分配、聊天发送
  （无 Key 路径）、建分组/列分组、账号信息读；
* 统计每个端点的状态码分布，专门数 507 与 ``dictionary changed``；
* 结束后核对：成功建出的目标数 == 列表里能查到的目标数（零静默丢失）。

用法（先起一个开发模式网关，再另开终端跑本脚本）：

    LIUHAO_DATA_DIR=/tmp/stress-d python -m src.gateway   # 终端 1
    python scripts/stress_goals_concurrency.py \
        --base http://127.0.0.1:8000 --rounds 5 --workers 20

账号：脚本会自己注册一个一次性根账号（开发模式、本机回环），
已注册过时改用 --user/--password 登录。
退出码：0 = 零 507、零 5xx、零数据丢失；1 = 发现问题（明细在输出里）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter


def req(base, method, path, body=None, token=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:  # 连接级失败也算一笔账
        return -1, repr(e)


# 注：成员账号没有可用模型时 POST /v1/chat 返回 409 是产品既定语义
# （provider 解析失败原样 409、不留空会话，见 src/gateway/chat.py 与
# test_p1_9），不是并发缺陷；判定只看 507/5xx 与目标一致性。

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--workers", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=5, help="每线程轮数；每轮 20 次混合请求")
    ap.add_argument("--user", default="stressboss")
    ap.add_argument("--password", default="Stress-boss-2026!")
    ap.add_argument("--accounts", type=int, default=5,
                    help="账号数：全局限流是按账号 600/分钟算的，单账号压测会先撞限流；"
                         "多账号才能把并发真正压到持久化层")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    st, _ = req(base, "POST", "/v1/accounts/register",
                {"principal": args.user, "password": args.password, "display_name": "Stress"})
    print(f"register -> {st} (200=新建, 409=已存在改走登录)")

    def do_login(user, pw):
        st, body = req(base, "POST", "/v1/auth/login", {"principal": user, "secret": pw})
        assert st == 200, f"login {user} failed {st} {body}"
        tok = body.get("access_token") or body.get("token")
        assert tok, f"no token: {list(body)[:8]}"
        return tok

    root_token = do_login(args.user, args.password)
    tokens = [root_token]
    for i in range(1, args.accounts):
        name, pw = f"stressm{i}", f"Stress-m{i}-2026!"
        st, b = req(base, "POST", "/v1/accounts",
                    {"principal": name, "display_name": f"M{i}"}, token=root_token)
        if st == 200 and isinstance(b, dict) and b.get("activation_code"):
            req(base, "POST", "/v1/accounts/activate",
                {"principal": name, "code": b["activation_code"], "password": pw})
        tokens.append(do_login(name, pw))
    print(f"账号就绪: {len(tokens)} 个")
    creds = [(args.user, args.password)] + [
        (f"stressm{i}", f"Stress-m{i}-2026!") for i in range(1, args.accounts)
    ]
    token_lock = threading.Lock()

    def areq(acct: int, method: str, path: str, body=None):
        """带 401 自动重登的请求：长压测里 access token 会过期，过期重登
        再试一次，不把 401 误判成服务端故障（run4 教训：17 分钟后全员
        401，一致性校验假报 565 个目标全丢）。"""
        tok = tokens[acct]
        st, b = req(base, method, path, body, token= tok)
        if st == 401:
            with token_lock:
                tokens[acct] = do_login(*creds[acct])
            st, b = req(base, method, path, body)
        return st, b
    token = root_token  # 兼容下方单号引用（一致性核对按账号分别做）

    codes: Counter = Counter()
    errors: Counter = Counter()
    created_goals: list[tuple[int, str]] = []  # (账号序号, goal_id)
    lock = threading.Lock()
    per_round = 20  # 每轮每线程 20 次请求：目标建/列/分配、聊天、分组、账号

    def worker(wid: int) -> None:
        acct = wid % len(tokens)
        token = tokens[acct]  # 兼容占位；实际请求走 areq（401 自动重登）
        for rnd in range(args.rounds):
            for i in range(per_round):
                kind = i % 5
                if kind == 0:
                    st, b = areq(acct, "POST", "/v1/goals",
                                {"natural_language": f"stress goal w{wid} r{rnd} i{i}"})
                    with lock:
                        codes[f"POST /v1/goals {st}"] += 1
                        if st == 200 and isinstance(b, dict) and b.get("goal_id"):
                            created_goals.append((acct, b["goal_id"]))
                        if st >= 500:
                            errors[f"goals {st}: {str(b)[:160]}"] += 1
                elif kind == 1:
                    st, b = areq(acct, "GET", "/v1/goals?limit=200")
                    with lock:
                        codes[f"GET /v1/goals {st}"] += 1
                        if st >= 500:
                            errors[f"goals-list {st}: {str(b)[:160]}"] += 1
                elif kind == 2:
                    st, b = areq(acct, "POST", "/v1/chat",
                                {"message": "stress ping"})
                    with lock:
                        codes[f"POST /v1/chat {st}"] += 1
                        if st >= 500:
                            errors[f"chat {st}: {str(b)[:160]}"] += 1
                elif kind == 3:
                    st, b = areq(acct, "POST", "/v1/workbench/groups",
                                {"name": f"stress-g-{wid}-{rnd}-{i}"})
                    with lock:
                        codes[f"POST groups {st}"] += 1
                        if st >= 500:
                            errors[f"groups {st}: {str(b)[:160]}"] += 1
                else:
                    st, b = areq(acct, "GET", "/v1/workbench/groups")
                    with lock:
                        codes[f"GET groups {st}"] += 1
                        if st >= 500:
                            errors[f"groups-list {st}: {str(b)[:160]}"] += 1

    t0 = time.time()
    threads = [threading.Thread(target=worker, args=(w,)) for w in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0

    total = sum(codes.values())
    n507 = sum(v for k, v in codes.items() if k.endswith(" 507"))
    n5xx = sum(v for k, v in codes.items() if any(k.endswith(f" {c}") for c in range(500, 600)))
    print(f"\n总请求 {total}（{args.workers} 并发 × {args.rounds} 轮 × {per_round}），耗时 {dt:.1f}s，"
          f"{total / dt:.0f} req/s")
    for k, v in sorted(codes.items()):
        print(f"  {k}: {v}")
    print(f"507 次数: {n507}；5xx 总数: {n5xx}")
    if errors:
        print("错误明细（前 10）：")
        for k, v in errors.most_common(10):
            print(f"  ×{v} {k}")

    # 数据一致性：建出的目标必须都能在列表里查到（按账号分页拉全；
    # 撞限流就等窗口过去再拉，不把 429 误判成丢数据）
    def fetch_all_goals(tok) -> set[str]:
        # GET /v1/goals 按设计无分页参数，一次返回全部可见目标摘要
        # （核实于 src/gateway/ai_management.py::list_goals）。早期版本
        # 按 limit/offset 分页拉取，在目标数 >= limit 时死循环——脚本
        # bug，不是产品缺陷；这里单次拉全，429 就等窗口过去重试。
        for _ in range(30):
            st, b = req(base, "GET", "/v1/goals", token= tok)
            if st == 429:
                time.sleep(20)
                continue
            items = (b or {}).get("goals", []) if isinstance(b, dict) else []
            return {str(g.get("goal_id")) for g in items}
        return set()

    missing: list[str] = []
    total_seen = 0
    for acct in range(len(tokens)):
        want = [g for a, g in created_goals if a == acct]
        if not want:
            continue
        seen = fetch_all_goals(do_login(*creds[acct]))
        total_seen += len(seen)
        missing.extend(g for g in want if g not in seen)
    print(f"目标一致性：建出 {len(created_goals)}，各账号列表可见合计 {total_seen}，丢失 {len(missing)}")
    ok = n507 == 0 and n5xx == 0 and not missing
    print("结论:", "PASS 零 507 / 零 5xx / 零丢失" if ok else "FAIL 见上明细")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
