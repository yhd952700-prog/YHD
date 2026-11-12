# 测试与工程质量域 · 只读深度体检报告

- 仓库：`d:\LiuHao-AI-OS`
- 分支 / HEAD：`p36` / `2a4a681f`（读取时刻工作树有 236 项未提交变更；审计期间仍在变化）
- 读取时刻：2026-11-08 00:30:56（基线）— 01:50（主统计落盘）— 02:05（复跑收尾）
- 审计方式：只读。唯一写入是本文件。未改代码、未改测试、未 commit、未 stash、未删除测试、未 `git add`。
- 并发前提：同一工作树当时至少有 3 路其他会话在跑 pytest / uvicorn / 前端构建（进程创建时间 00:18–01:47），源码在审计窗口内被改动过（见 QA-024）。本报告所有结论以**对应时刻的工作树**为准。
- 隔离手段：全量 pytest 未使用仓库数据目录，`TEMP/TMP` 指向 `D:\cache\temp\qa-audit-20261108\...` 独立目录；崩溃实验与分块运行均在仓库外。

---

## 0. 结论摘要（先看这五句）

1. **套件当前是红的：97 failed + 33 errors**，不是"全绿"；其中约 50 条红是测试写真实系统路径被安全护栏拦下（`C:\LiuHaoBackups`、`C:\Users\Administrator\.liuhao`），不是产品逻辑单测失败。
2. **单进程跑不完全量套件**：两次在约 9% 处触发 Windows fatal exception（`0x80000003`），退出码 `-2147483645`，并留下一个杀不掉的僵尸 pytest 进程（PID 30672，00:17 创建，至今存活）。
3. **`--basetemp` 复用是雷**：第二次带 `--basetemp` 运行得到 `0 passed / 8799 error / 63 skipped`，每个用例 setup 都被护栏 `fail-closed` 拒绝（可复现，见 QA-002）。
4. **CI 的"绿"有结构性盲区**：前端 557 个 vitest 用例任何自动 CI 都不跑；37 个 slow 用例任何 CI 都不跑；主 CI 不构建前端 dist 导致 5 个单端口服务用例永久 skip；覆盖率 80% 阈值只存在于手动触发的工作流。
5. **"改密码"这个核心功能零测试**：后端路由与前端入口都没有任何用例引用（QA-003）。

---

## 1. 真实测试统计

### 1.1 Python（pytest）

单进程全量运行在本机不可用（QA-001），因此改用**逐目录分块**（35 块，root + 33 子目录 + `__pycache__`），单块独立 `TEMP`，总墙钟约 78 分钟。

| 指标 | 数值 | 说明 |
|---|---|---|
| collected | **8937** | 35 个分块 junit 合计；审计开始时 `--collect-only` 为 8799，说明窗口内工作树新增了用例 |
| passed | **8723** | 由总数反推（8937-97-33-84） |
| failed | **97** | 断言失败 |
| errors | **33** | setup/teardown 失败（25 个是 QA-024 的瞬时 ImportError，8 个是升级用例被护栏拦） |
| skipped | **84** | 见 §2 |
| exit 5 分块 | 5 个目录 | `__pycache__`、`data`、`fixtures`、`framework`、`output` 收集到 0 个用例 |

分块红色分布：

| 分块 | failed | errors | 主要成因 |
|---|---|---|---|
| root | 46 | 8 | `C:\LiuHaoBackups` 探针（≈20）、console 单端口（5）、google-genai 环境（5）、其它红 14 |
| kernels | 4 | 25 | 25 error = 瞬时 `ImportError: is_address_rejected_error`（已自愈）；4 红 = 身份默认路径 ×2、未分类内核动作 ×2 |
| gateway | 24 | 0 | 账号写入 503、handoff/recovery 码、https-only 拒绝本地 stub、文案断言 |
| security | 22 | 0 | 21 个 RBAC 用例写真实 `~/.liuhao\rbac.json` 被护栏拦 + 1 个出站域名未登记 |
| knowledge | 3 | 0 | 账号作用域文档名前缀断言 |
| tenant | 3 | 0 | 同类真实路径被护栏拦 |
| ai | 2 | 0 | 文案回归（"已取消"措辞、任务产出措辞） |
| scripts | 1 | 0 | 根目录新增 `RELEASE-RUNBOOK*.md` 未进 lint 扫描面 |

### 1.2 前端（vitest）

```
npx vitest run  →  52 files / 557 tests  |  557 passed, 0 failed, 0 skipped  |  44.62s
```
配置：`apps/console/console/vitest.config.ts`（jsdom、`globals:false`、无 retry、无 coverage）。**该命令不在任何 workflow 中执行**（QA-009）。

---

## 2. 跳过清单（84 条，逐类判定）

| 原因 | 数量 | 代表位置 | 是否关键路径 |
|---|---|---|---|
| `need --run-slow option to run` | 37 | `tests/conftest.py:56`；用例集中在 `tests/test_contract_drift_surface.py:33`、`tests/test_dependency_dll_scan.py:48`、`tests/test_deployment_gate_vars_judgement.py:69` | **是**（发版断言/依赖扫描），但任何 CI 都不带 `--run-slow`（QA-010） |
| `LangGraph not available` | 13 | `tests/test_langgraph_workflow.py:175`（模块级 skipif） | 是（工作流引擎）；本机可 import，CI 未装则整块跳 |
| `openssl ts engine not available(/on PATH)` | 17（11+4+2） | `tests/kernels/audit/test_timestamp_quorum.py:24`、`test_root_of_trust.py:159`、`tests/security/evidence/test_rfc3161_compat.py:102` | **是**（审计时间戳/根信任），本机全跳；CI 有 openssl 会跑 |
| `需要真实 redis-server` / `本地无 Redis` | 3 | `tests/distribution/test_bus_backends.py:522`、`test_coordination.py:1734`、`test_fence_coordination_chaos.py:226` | 是（分布式协调）；CI 起 redis 服务，会跑 |
| `需要真实 etcd` | 2 | `tests/distribution/test_coordination.py:1871`、`:2349` | 是；CI 起 etcd |
| `本机无 PostgreSQL` | 1 | `tests/kernels/memory/test_postgres_backend.py:438` | **关键**；任何 CI 都没有 PG（QA-025） |
| `会临时修改系统时钟` | 1 | `tests/test_clock_consistency.py:32` | 中；需 `LIUHAO_ALLOW_CLOCK_CHANGE_TESTS=1` |
| `audit_store.db 不在本次检出中` | 1 | `tests/test_cwd_delete_guard.py:134` | **是**（真实 DB 删除护栏），在 CI 永远不生效（QA-026） |
| `Windows 不要求管理员即可绑定 <1024` | 1 | 桌面端口用例 | 低 |
| `no untracked .env on this machine` | 1 | `tests/test_deployment_gate_vars_judgement.py:319` | 低 |
| `RLIMIT_AS is Unix-only` | 1 | 资源限制用例 | 低 |
| `monty 不可用…` | 1 | `tests/test_sandbox_monty_backend.py` | 中（沙箱后端） |
| `HD-05 … RFC 3161 … off by default` | 1 | 真实 TSA 往返 | 中；离线默认关 |
| `PyJWT version too old for PyJWK.from_pem` | 1 | JWT 用例 | 低 |
| `GAP B / GAP C`（xfail） | 2 | `tests/kernels/execution/test_failure_propagation_honesty.py:159,185` | **关键**：这是被隐藏的产品缺陷（QA-004） |
| 环境自跳过（403 当"环境不足"） | 1 | `tests/kernels/test_kernel_registry.py:214-218` | **关键**：假绿，见 QA-014 |

结论：
- `skipif` 类（openssl/redis/etcd/PG/Windows）大多是**环境门**，CI 覆盖了一部分（redis/etcd/openssl），但 **PG / 时钟回拨 / slow / 真实 DB 删除护栏在"任何环境"都不跑**。
- **危险跳过**：`test_kernel_registry.py:214-218` 把"403 拒绝"当成"环境不足"直接 skip；skip 文本里已经带着真实 403 细节，属于用 skip 掩盖红。

---

## 3. 十个核心行为：有没有"真实端到端"

判定口径：真实 = 真浏览器/真速记进程/真外部依赖；API 集成 = 进程内 `TestClient` + 真存储（provider/embedder 打桩）；单测 = 只测函数。

| # | 行为 | 最接近真实的一层 | 用例 | 判定 |
|---|---|---|---|---|
| 1 | 登录 | **真浏览器**（CI `console-readability` 作业对活网关真登录 + 遍历 9 页，但只断言对比度/可读性） | `tests/test_gateway_auth.py:246` | 🟡 有真浏览器路径，不校验登录业务语义 |
| 2 | 发消息 | **夜间真 LLM**（`first-run-e2e.yml`：向导填 Key→连接测试→聊天→重启→再聊）；常规 CI 只有 TestClient + mock provider | `tests/gateway/test_chat_reliability.py`、`tests/gateway/test_p10_real_job_e2e.py` | 🟡 真链路只在夜间且需密钥 |
| 3 | 改 Key | 夜间真链路同一作业覆盖；HTTP 保存/切换为 API 集成 | `tests/test_gateway_models_api.py`、`tests/test_model_key_and_chat_priority.py` | 🟡 |
| 4 | 上传知识库 | API 集成（multipart 真写盘 + 真入库），无浏览器上传 | `tests/knowledge/test_documents_admin_api.py:396` | 🟡 只有 API 层 |
| 5 | 派活 | API 集成 + 重启后持久化校验（真 FastAPI + 真 JWT + 真 store） | `tests/gateway/test_employee_full_closure.py` | 🟡 执行器打桩 |
| 6 | 取消任务 | API 集成偏弱（只断言 `status=="cancelled"`）；另有强断言的运行时停止用例 | `tests/test_api_extended.py:391`、`tests/ai/test_agent_runtime_stop.py:59` | 🟡 |
| 7 | **改密码** | **无任何测试**（后端路由 `src/gateway/accounts.py:987` 与前端 `resetMemberPassword` 无用例引用） | — | 🔴 |
| 8 | 导出备份 | API 集成，真 SQLite 导出→导入 round-trip | `tests/gateway/test_account_backup_stores.py:105` | 🟡 |
| 9 | 升级检查 | API 集成，真 zip/manifest/真 HTTP source/真磁盘布局 | `tests/test_upgrade_api.py`、`test_upgrade_version_source.py` | 🟡（其中 20 条因 QA-005 当前红） |
| 10 | 退出登录 | API 集成，强断言（吊销后 `/me` 401） | `tests/test_gateway_auth.py:382` | 🟡 无浏览器 |

总体：**10 项里 9 项的真实端到端缺位**；唯一真浏览器作业只做"登录 + 可读性"，不做业务行为。

---

## 4. 不稳定测试实测

协议：候选批 A（6 文件 31 用例：实例锁、员工并发、审计多进程、持久化并发、目标真值并发、流断开释放锁）连跑 3 次；候选批 B（端口敏感：桌面 runner 排空、目标停止、启动门禁）连跑 3 次。

- 批 A：3/3 均 `31 passed`，用例集合与结果完全一致。
- 批 B：3/3 均 `19 passed`。
- 对"窗口内出现过红绿翻转"的用例单独复跑 5 次：

| 用例 | 3 次批跑 | 单独 5 次 | 结论 |
|---|---|---|---|
| `tests/test_chat_gateway.py`（13 个） | 目录跑 3 红 | **13/13 ×5 全绿** | 非稳定 flake；是工作树在 01:12–01:36 之间改了存储接线（QA-024） |
| `tests/kernels/policy/test_quota_enforcement.py`（39 个） | 分块 25 error | **39/39 全绿（7.09s）** | 非 flake；运行当时 `src.ai.providers` 尚无 `is_address_rejected_error`，20 分钟后该符号在 `src/ai/providers.py:261` 出现 |
| `tests/kernels/test_employee*` / `test_account_scope_three_layers.py::test_employee_scope_follows_subtree` | 红 | **1 failed ×5（5/5）** | 确定性红，不是 flake |
| `tests/test_console_single_port.py`（54 个） | 5 红 | **5 failed ×3（一致）** | 确定性红 |

**不稳定用例清单：0 条（无一条可复现的随机 flake）**；但存在**结构性不稳定**：同一条用例在不同"时间窗"结果不同，原因是共享工作树被并行会话改源码（诚实记录，不当作 flake 统计）。

---

## 5. 测试隔离实测（真实目录 / 真实端口 / 真实 LOCALAPPDATA / 残留）

### 5.1 已确认的写穿（本轮实测文件与时间戳）

| 目标 | 证据 | 归属 |
|---|---|---|
| `C:\LiuHaoBackups\.liuhao-write-probe`（5 字节） | 01:00:15、01:44:03 两次写入并**残留至今** | 本审计的升级用例（`src/upgrade/manager.py:287-297`） |
| `C:\Users\Administrator\.liuhao\rbac.json.<rand>.tmp` | 护栏报错原文：`删除接口: pathlib.Path.unlink 目录项: C:\Users\Administrator\.liuhao\rbac.json.5j_eltuj.tmp` | RBAC/tenant 用例（`src/security/rbac.py:314` 默认 `Path.home()/".liuhao"/"rbac.json"`） |
| `%LOCALAPPDATA%\LiuHao\data`（目录 mtime） | 00:31–00:32（崩溃窗口内）、01:10:53（desktop 分块窗口内） | 桌面/安装类用例；另有 6+ 别的会话进程，无法 100% 归因，但时间点与分块吻合 |
| 仓库根 DB/WAL | `data/knowledge_vectors.sqlite3-shm` 23:54:44、`.liuhao_artifacts.db` 23:54:45、`memory_store.db-shm` 23:54:45、`.liuhao_execution.journal-shm`、`.liuhao_projects.json.lock`、`.probe_tmp\slow_default.txt` 23:47:25 | 第一次全量 pytest（23:47–23:57）；模块默认回退到 CWD（QA-020） |

`tests/conftest.py:1165-1195` 有一个 session 收尾的清道夫，专门删除 `%LOCALAPPDATA%\LiuHao\data.after-smoke-*` / `data.bak-*` 这类"测试期复制出来的真实数据目录"，注释自己承认**这些副本可能含凭据/身份/主密钥**。也就是说：写真实目录是设计内的，安全性只靠"跑完再删"；进程被杀（本审计就崩了两次）就会留下含密钥副本。

### 5.2 护栏（conftest 删除守卫）

- 位置：`tests/conftest.py:987-1046`（`_check`、`_is_under`、`guarded_path_unlink` 在 `:1023`）。
- 行为：任何 `unlink/rmtree` 目标必须"在 TEMP 之下，或在已登记的 scratch root 之下"，否则 `pytest.fail`（fail-closed）。
- 正面价值：它**救下了真实数据**——21 个 RBAC 用例与 3 个 tenant 用例本会直接改写/删除真实 `~/.liuhao/rbac.json`，是护栏把它们变成红，而不是静默破坏。
- 缺陷：它抛的是 pytest `Failed`（`BaseException`）而不是 `OSError`，所以 `manager.py:299 except OSError` 吞不住，探针用例全红（QA-005）；且 `--basetemp` 复用场景会误杀整场（QA-002）。

### 5.3 残留进程 / 端口（收尾实测，02:00 快照）

| 项 | 实测 | 归属 |
|---|---|---|
| 僵尸 pytest PID 30672 | 创建 00:17:03，CPU 0，WS 32KB，`Stop-Process -Force`/`taskkill /F` 均返回 Access denied，命令行为空 | **本审计崩溃遗留**，杀不掉 |
| 僵尸 python PID 30924 | 创建 14:52:46，命令行为空 | 其它会话遗留 |
| 早于本审计的 pytest 进程 | PID 16340/5868/7108/9660（15:15–17:03）仍存活 | 其它会话遗留 |
| 端口 8765 / 8766 | `python -m http.server` 22:07 / 22:12 起，存活 3.5 小时 | 其它会话遗留，占用固定端口，任何绑 8765 的用例都会被动失败 |
| 端口 11434 / 11435、5173 / 5199、18080–18097 | 存活 | ollama / vite / 本地网关，多会话 |
| `D:\_audit\provider-drift\mock_provider.py` (PID 18864, 16:08) | 存活 | 其它会话遗留 mock 服务 |

**本审计自身遗留清单**：`C:\LiuHaoBackups\.liuhao-write-probe`（1 文件）、僵尸 PID 30672、`D:\cache\temp\qa-audit-20261108\`（仓库外临时产物，含 1.5 万+ pytest 临时目录）。仓库内未新增任何文件（除本报告）。

---

## 6. 门禁脚本真实性（`scripts/` 全量）

审计方法：机械扫描 78 个 `verify_*.py`（存在性/退出码可达性/异常吞噬/白名单），再人工读高风险脚本。

| 检查项 | 结论 |
|---|---|
| "文件不存在就 PASS" | **未发现**。`verify_readiness.py` 的 hash-chain 检查在脚本缺失时返回 `NOT_VERIFIED`（不是 PASS）；`verify_no_undocumented_orphans.py:92` 的 `is_file()` 是扫描循环跳过，不是放行 |
| "异常被吞就当成功" | **未发现**。`verify_persistence.py`、`verify_orm_vs_db.py` 异常即非零；`verify_alert_rules.py` 有显式 EXIT 常量 |
| "白名单过宽" | 扫描命中的字符串列表实为 PromQL/文档术语表；扫描白名单每项都要求写明理由，并由 `tests/test_guardrail_scripts.py` 反向校验防腐烂 |
| 元门禁强度 | `tests/test_guardrail_scripts.py` 强制 78 个门禁**必须能被某个 workflow 调用**、**必须有非零退出路径**、与 `GATE-COUNT-MARKER: 78` 计数一致、报告类门禁白名单必须为空；这一层是真正的强约束 |
| 3 个"无静态非零路径"告警 | 人工核实均为误报（`main()->int`、`sys.exit(0 if ok else 1)`、`raise RuntimeError`） |

**确认存在的"跳过即绿"（3 处）**：
1. `scripts/verify_audit_integrity.py`：汇总时把 status=SKIP 的门禁计入 `n_pass`，`rc==0` 即判 PASS。
2. `scripts/verify_audit_chain_no_fork.py:196-250`：仓库根无 `audit_store.db` 时打印 `GATE SKIP` 并 `return 0`——本机正是无 DB，因此这道门禁"绿但什么都没验"。
3. `scripts/verify_metrics_persist.py`：`METRICS_PERSIST` 未开启时直接 `0` 返回。

**其他门禁事实**：
- `scripts/verify_readiness.py` 的 C3 "稳定测试"只跑**一次**套件（无重复跑），C4 只 grep CI YAML 里有没有 bandit/semgrep 字样，不执行工具。
- `scripts/verify_readiness.py` 的 `***_NOT_VERIFIED` **不再**使整门变绿：有 NOT_VERIFIED / BLOCKED 时以**退出码 3** 收尾并打印显式 warning（`verify_readiness.py:1120-1124`），FAIL 或 checker 自身跑不起来是退出码 2。「没验证」与「通过」在退出码上已可区分。
- `release-readiness.yml` 仅在分支 `p36` 的 push/PR 触发（当前分支正是 p36，所以会跑）；但它的全量套件**不传 `--run-slow`**，37 个 slow 用例依旧不跑。
- `scripts/verify_persistence.py` / `verify_orm_vs_db.py` 会在**仓库根**创建 `liuhao_ai_os.db`（门禁自身污染工作树，QA-020）。
- `scripts/verify_test_collection.py:90-98` 每次用"全新 basetemp"规避陈旧目录——这是对 QA-002 的绕行（注释自称旧目录会让 pytest "伪装成整场灾难性失败"），没有修根因；`MIN_TESTS = 3550`（:67）远低于实际 8937，形同虚设（QA-028）。

---

## 7. 断言质量抽查（20 例）

| # | 用例 | 类型 | 判定 |
|---|---|---|---|
| 1 | `tests/test_gateway_auth.py:246` 登录 | API 集成 | 好：断言 principal/display_name/scope/expires_in 内容 |
| 2 | `tests/test_gateway_auth.py:382` 退出登录 | API 集成 | 好：断言 revoked=True 且随后 `/me` 401 |
| 3 | `tests/knowledge/test_documents_admin_api.py:396` 上传 | API 集成 | 好：断言 `source` 内容 |
| 4 | `tests/gateway/test_account_backup_stores.py:105` 备份导出 | API 集成 | 好：真 SQLite round-trip + 逐字段比对 |
| 5 | `tests/gateway/test_employee_full_closure.py` 派活关闭 | API 集成 | 好：重启后仍可读 |
| 6 | `tests/test_model_key_and_chat_priority.py:190` 改 Key | 集成 | 好：真 HTTP upstream，断言头部实际序列化 |
| 7 | `tests/test_upgrade_version_source.py` 升级检查 | 集成 | 好：真 zip/manifest/sha，断言"未配置≠最新" |
| 8 | `tests/ai/test_agent_runtime_stop.py:59` 停止 | 单测集成 | 好：断言状态 CANCELLED + 只跑 1 个任务 + 错误文案 |
| 9 | `tests/test_api_extended.py:391` 取消任务 | API 集成 | 偏弱：只断 200 + `status=="cancelled"`，不断言执行真的停了 |
| 10 | `tests/gateway/test_p10_real_job_e2e.py` 真实作业 | API 集成 | 好：真落盘 + 真审计记录 |
| 11 | `tests/gateway/test_goal_truth_concurrency.py:117` | 单测 | 部分内部 mock（`_save_goals`/`_ensure_runtime`），偏"测自己搭的桩" |
| 12 | `tests/kernels/execution/test_failure_propagation_honesty.py:139` | xfail | **假绿**：GAP A 非 dict 返回被当成功，永不失败 |
| 13 | 同上 `:159` | xfail | **假绿**：GAP B 只信 `ctx.failed_tasks`，忽略校验 |
| 14 | 同上 `:185` | xfail | **假绿**：GAP C 空判据直接 SUCCESS |
| 15 | `tests/test_smoke.py:1` | 单测 | `assert True`，零价值 |
| 16 | `tests/kernels/test_kernel_registry.py:214-218` | 集成 | **假绿**：`except Exception → pytest.skip`，403 被当"环境不足" |
| 17 | `tests/kernels/test_lifecycle_runtime_driven.py:224` | 集成 | 同上模式 |
| 18 | `tests/test_console_single_port.py:257/264/270` | 集成 | 断言内容正确（缓存头/不落盘），当前红是因为 `mount_console` 契约变了（QA-008） |
| 19 | `tests/test_cross_account_isolation.py:273` | 集成 | 强断言（boss 列表 total==0），当前红 → 见 QA-007 |
| 20 | `tests/security/test_rbac_kernel.py`（21 例） | 单测 | 断言质量尚可，但写真实 home（QA-006） |

补充扫描统计（启发式）：`ONLY_STATUS_CODE` 命中 157 处，抽样后**绝大多数是可接受的鉴权拒绝断言（401/403 本身就是行为）**，只有少数 API 用例只断状态码；`pytest.raises(Exception)` 11 处（宽接异常，容易掩盖类型回归）；`assert True` 1 处。

---

## 8. CI 与提交前校验：实际能拦住什么

| 机制 | 存在 | 实际拦截 |
|---|---|---|
| `.github/workflows/ci.yml` `test` 作业 | 有 | 收集完整性（`verify_test_collection.py`）、全量 pytest、`flake8 src/ --select=E,F,W`；起真 redis + etcd |
| `guardrails*` 作业 | 有 | 78 个 `verify_*.py` 全跑；`tests/test_guardrail_scripts.py` 保证每个门禁都被接线 |
| `security` 作业 | 有 | Bandit HIGH 阻断；Trivy `--exit-code 0`（不阻断）；pip-audit `|| true`（不阻断） |
| `console-readability` 作业 | 有 | Playwright 真浏览器对活网关登录 + 9 页对比度 |
| `ci-cd.yml` | 有，但 **仅 workflow_dispatch/tag** | `pytest --cov=src --cov-fail-under=80`：**自动 CI 从不执行**，80% 阈值从未生效 |
| `release-readiness.yml` | 有（仅 p36 分支） | `verify_readiness.py --full`：真的会跑全量套件；NOT_VERIFIED / BLOCKED 以**退出码 3** 收尾（不是 0、不算通过），FAIL 或 checker 跑不起来是 2；只跑一次、不带 `--run-slow` |
| `first-run-e2e.yml` | 有（夜间/dispatch） | 真密钥、真 LLM、真重启；缺密钥时**明确失败**（好） |
| pre-commit / husky / `.pre-commit-config.yaml` | **无** | `.git/hooks` 只有 `.sample`；一切校验都在 push 之后 |
| vitest | **无** | 557 个前端用例零 CI 覆盖 |
| 覆盖率 | 无自动 | `pytest-cov 7.1.0` 本地已装，但默认 `addopts` 不带 `--cov` |

---

## 9. 发现条目（QA-001 起）

> 格式：编号 / 模块 / 文件:行号 / 表现 / 是否复现 / 根因 / 影响面 / 安全或数据风险 / 修复方案 / 需要改的文件 / 验证方法 / 等级 / 状态

### QA-001 / 测试运行器 / `src/desktop/diagnostics.py:734-740` + `tests/conftest.py:987-1046`
- 表现：单进程全量 pytest 在约 9% 处崩溃：`Windows fatal exception: page error` ×2、`Windows fatal exception: code 0x80000003`，退出码 `-2147483645`；faulthandler 栈落在 `tests/desktop/test_desktop_repair.py:120 → src/desktop/repair.py:370 → src/desktop/diagnostics.py:734/740 → conftest _check/guarded_path_unlink`，以及 `src/desktop/probe.py:184` 的 subprocess。
- 是否复现：**已复现 2 次**（独立 TEMP、独立进程）；崩溃后留下**无法终止**的僵尸 pytest 进程（PID 30672，`Stop-Process -Force` 与 `taskkill /F` 均 Access denied）。
- 根因：护栏在 GC/解释器收尾期对"删除真实路径"抛 pytest 失败，与 fatal exception 的时序叠加；属累积型进程级故障，单测单跑无法复现。
- 影响面：任何"一把梭"全量运行/本地发版预检都不可靠；CI 用 ubuntu 不受影响，Windows 开发机全量验证不可用。
- 安全或数据风险：中（崩溃时临时副本与真实目录清理中断）。
- 修复方案：让护栏抛可回收异常类型或把探针目录加入 scratch 白名单；修复 `probe_directory_writable` 不要对真实盘根做删除；给 Windows 全量运行提供官方分块入口。
- 需要改的文件：`tests/conftest.py`、`src/desktop/diagnostics.py`、`src/desktop/probe.py`、`scripts/run_tests.py`（新增分块模式）。
- 验证方法：`python -m pytest tests/ -q -p no:cacheprovider` 连跑 2 次，退出码 0/1 且无 `Windows fatal exception`。
- 等级：**P1**；状态：**OPEN**

### QA-002 / 测试运行器 / `tests/conftest.py:1037-1046`
- 表现：带 `--basetemp D:\...\basetemp` 第二次运行（目录已存在）→ `{'error': 8799, 'skipped': 63, 'passed': 0}`，每个用例 setup 失败：`护栏拒绝（fail-closed）…当前用例: <session setup>`；目标正是 basetemp 根。
- 是否复现：**已复现**（junit `pytest_run1.xml` 40MB 为证）；不带 `--basetemp` 或换新目录则正常。
- 根因：session fixture 里用 `getattr(tmp_path_factory, "_given_basetemp", None)` 取"CLI 传入的 basetemp"，在本机 pytest 9.1.1 上取不到（返回 None），随后访问 `tmp_path_factory.basetemp` 触发对旧 basetemp 的 `rm_rf`，此时该路径既不在 `%TEMP%` 下也未被登记 → 护栏拒绝 → 每个用例 setup 都炸。`scripts/verify_test_collection.py:90-98` 用"每次全新 basetemp"绕行，未修根因。
- 影响面：所有想复用 basetemp 的本地/CI 循环、增量调试、IDE 重跑；错误信息表现为"整场灾难性失败"，极易误判为代码坏。
- 安全或数据风险：低（fail-closed 方向正确）。
- 修复方案：从 `config.option.basetemp` 直接取 CLI 路径并在**访问任何 `tmp_path_factory` 属性之前**登记；或把 basetemp 根自身的删除显式放行。
- 需要改的文件：`tests/conftest.py`。
- 验证方法：`pytest tests/test_smoke.py --basetemp D:\cache\tmp_bt` 连续跑两次均 1 passed。
- 等级：**P1**；状态：**OPEN**

### QA-003 / 账号 / `src/gateway/accounts.py:987-1000`、`apps/console/console/src/pages/Members.tsx`（`resetMemberPassword`）
- 表现：`POST /v1/accounts/{principal}/password`（改密/轮换凭据）在 `tests/` 与前端 `*.test.*` 中**零引用**；全仓仅 `tests/security/test_secret_store.py` 提到 `/password` 字样。
- 是否复现：**静态判断**（`grep -rn "/password|reset_password" tests/` 无业务用例；前端测试无 `resetMemberPassword`）。
- 根因：功能（UI+路由）先上线，测试未跟上；`rotate_credential` 有单测，但 HTTP 层与 UI 层未覆盖。
- 影响面：改密失败、旧令牌未失效、跨账号越权改密、密码策略绕过都不报警。
- 安全或数据风险：**高**（凭据轮换是安全关键路径）。
- 修复方案：补 API 用例（成功改密→旧令牌失效→新密码可登录→他人账号 403/404 中性响应）+ 前端组件测试。
- 需要改的文件：新增 `tests/gateway/test_account_password.py`、`apps/console/console/src/pages/Members.test.tsx`。
- 验证方法：新用例先红（旧令牌仍 200）后绿；并断言审计事件。
- 等级：**P1**；状态：**OPEN**

### QA-004 / 执行内核 / `tests/kernels/execution/test_failure_propagation_honesty.py:139,159,185`
- 表现：三个 `@pytest.mark.xfail(strict=False)` 把**真实产品缺陷**标记成"预期失败"：
  - GAP A：执行器返回非 dict（如 None）被当作 `{'status':'executed'}` 成功（`src/kernels/execution/__init__.py`）；
  - GAP B：`AgentRuntime.run_goal` 只按 `ctx.failed_tasks` 判状态，忽略校验结果（`src/ai/agent_runtime.py:326-330`）；
  - GAP C：评估器空判据集返回 `SUCCESS` 且 `overall_score=0.0`（`src/kernels/evaluation/__init__.py:238-244`）。
- 是否复现：**静态 + 运行**（分块结果里 GAP B/C 以 skip 形式出现，GAP A 已 XPASS → `strict=False` 下不会红）。
- 根因：用 xfail 记录缺陷，且 `strict=False` 既不阻止回归也不提示"缺陷已修"。
- 影响面：任务"未验证就报完成"、空评估报成功——直接影响"诚实性"承诺；suite 统计上它们既不算失败也不算通过。
- 安全或数据风险：中（错误完成状态会触发后续自动化）。
- 修复方案：改成 `strict=True` 并挂 issue 编号；或直接修三处缺陷后移除 xfail。
- 需要改的文件：上述 4 个文件。
- 验证方法：`pytest tests/kernels/execution/test_failure_propagation_honesty.py -q` 出现 3 个 XPASS 即证明缺陷已修。
- 等级：**P1**；状态：**OPEN**

### QA-005 / 升级模块 / `src/upgrade/manager.py:287-300`（`:262,373-394` 默认备份根）
- 表现：`_probe_writable_dir()` 对默认备份根 `C:\LiuHaoBackups` 建目录 → 写 `.liuhao-write-probe` → `unlink()`；conftest 护栏在 `unlink` 处 `pytest.fail`，导致 **升级相关约 20 条用例红 + 8 个 setup error**；并在主机上留下真实 `C:\LiuHaoBackups\.liuhao-write-probe`。
- 是否复现：**已复现**（`pytest tests/test_upgrade_api.py` = 3 failed / 1 passed / 8 errors；`C:\LiuHaoBackups\.liuhao-write-probe` 01:00:15 与 01:44:03 两次实测存在）
- 根因：`default_backup_root()` 会挑"另一块固定本地盘"（这里选到 C:），而用例没有注入 `backup_root=tmp_path`；护栏抛 `pytest.Failed`（非 OSError），`except OSError` 吞不住。
- 影响面：升级/备份/回滚/版本源 20+ 用例长期红；测试写穿到 C 盘，污染主机。
- 安全或数据风险：中（测试在外盘根目录创建/删除文件；崩溃时会留下探针文件）。
- 修复方案：所有升级用例用 tmp 目录作为 `backup_root`；`_probe_writable_dir` 捕获 `BaseException` 前先判断路径是否受管；或把备份探针统一走 `launch_installed.py:711` 那种"数据目录内探针"。
- 需要改的文件：`tests/test_upgrade_*.py`（夹具）、`src/upgrade/manager.py:287-300`。
- 验证方法：`pytest tests/test_upgrade_api.py tests/test_upgrade_cli.py tests/test_upgrade_version_source.py -q` 全绿，且 `C:\LiuHaoBackups` 不再出现探针。
- 等级：**P1**；状态：**OPEN**

### QA-006 / 安全 RBAC / `src/security/rbac.py:314`、`tests/security/test_rbac.py`、`tests/security/test_rbac_kernel.py`、`tests/tenant/test_tenant_isolation.py`
- 表现：21 个 RBAC 用例 + 3 个 tenant 用例在真实 home 下创建 `C:\Users\Administrator\.liuhao\rbac.json.*.tmp`，删除被护栏拒绝 → 全红（`security.xml` 22 failed、`tenant.xml` 3 failed）。
- 是否复现：**已复现**（失败原文包含完整真实路径 `C:\Users\Administrator\.liuhao\rbac.json.5j_eltuj.tmp`）。
- 根因：`RbacStore` 默认 `store_path = Path.home()/".liuhao"/"rbac.json"`，用例未注入 `store_path`/环境变量；conftest 没有把 RBAC 存储纳入按用例隔离。
- 影响面：所有 RBAC/租户行为当前不可验证；且如果护栏失效（例如被别的会话改弱），这些用例会**直接改写真实 RBAC 数据库**（权限错乱、越权）。
- 安全或数据风险：**高**（写真实权限库）。
- 修复方案：conftest 统一把 RBAC/tenant 存储路径指向 per-test tmp；或用例显式 `RbacStore(store_path=tmp_path)`。
- 需要改的文件：`tests/conftest.py`（新增 RBAC/tenant 隔离夹具）、`tests/security/*`、`tests/tenant/*`。
- 验证方法：`pytest tests/security tests/tenant -q` 全绿且 `~/.liuhao/rbac.json*` mtime 不变。
- 等级：**P1**；状态：**OPEN**

### QA-007 / 知识库账号隔离 / `tests/test_cross_account_isolation.py:273`
- 表现：boss 账号 `GET /v1/knowledge/documents` 返回 `total==2`（alice/bob 各 1 篇），断言要求 `0`；同文件文档名断言也红（`member-e31a…--member-doc` 与 `member--member-doc` 不一致）。
- 是否复现：**已复现 2 次**（分块 root 与独立复跑 `/v1` 单文件各 1 次）。
- 根因：知识库列表/文档命名作用域与"成员/主账号隔离"契约不一致；疑似另一条线正在改账号域（`member-<hash>--` 前缀），测试与实现不同步。
- 影响面：主账号可枚举成员知识库文档（文档名/数量），跨账号机密性受损；同文件"成员不能读他人文档"仍绿，说明泄漏只在列表聚合路径。
- 安全或数据风险：**高**（跨账号数据暴露）。
- 修复方案：确认设计意图——若"主账号不可见成员文档"，修列表聚合过滤；若新设计允许管理员看，需改测试并显式声明权限；同时固定文档名作用域格式。
- 需要改的文件：知识库文档路由（`src/gateway/knowledge*.py` / `src/knowledge/*`）、`tests/test_cross_account_isolation.py`。
- 验证方法：boss 与成员分别列表，断言 total 与文档集合互不包含。
- 等级：**P1**；状态：**OPEN**

### QA-008 / 控制台单端口 / `src/gateway/console_static.py:265-290`（`mount_console`）、`tests/test_console_single_port.py:257,264,270,330`
- 表现：`cache_client` 用裸 `FastAPI()` + `mount_console(app, dist)` 后请求 `/assets/*`、`/`、`/sw.js` 全部 404；`is_installed(get_app())` 为 False。5 条红 ×3 次一致。
- 是否复现：**已复现 3 次**（5 failed / 49 passed 稳定）。
- 根因：`mount_console` 现在只设置 `app.state.console_fallback`，静态资源与 404 处理器依赖 `main.py` 安装；单独调用 `mount_console` 不再"自足"。
- 影响面：任何非 `main.py` 的宿主（测试、插件、嵌入式网关）拿到空控制台；CI 里该文件因无 dist 而整体 skip（双重盲区）。
- 安全或数据风险：低-中（404 回显行为曾有路径泄露隐患，注释已说明改为 state 方案）。
- 修复方案：`mount_console` 恢复自足（挂 assets + 安装唯一 404 处理器），或把测试改为经 `main.create_app` 构造。
- 需要改的文件：`src/gateway/console_static.py`，必要时 `tests/test_console_single_port.py` 夹具。
- 验证方法：裸 app + `mount_console` 后 `GET /`、`/sw.js`、`/assets/<hash>.js` 均 200；CI test 作业内也能跑（先 `npm run build`）。
- 等级：**P1**（含 CI 盲区）；状态：**OPEN**

### QA-009 / CI / `.github/workflows/*.yml`
- 表现：全仓没有任何 workflow 执行 `vitest`/`npm test`；只有 `npm run build`（`tsc -b` 类型检查）。
- 是否复现：**静态判断**（`grep -rn "vitest|npm test" .github/` 零命中）。
- 根因：前端测试只在本地约定。
- 影响面：557 个前端用例（含登录页、消息渲染、状态机）在合并前零拦截。
- 安全或数据风险：低-中（前端权限/展示回归可无声进主干）。
- 修复方案：`test` 作业加 `cd apps/console/console && npm ci && npx vitest run`（可复用 build 缓存）。
- 需要改的文件：`.github/workflows/ci.yml`。
- 验证方法：故意改坏一个前端断言，PR 应红。
- 等级：**P2**；状态：**OPEN**

### QA-010 / CI / `pyproject.toml` markers 注释、`scripts/verify_readiness.py`（C3 调用参数）
- 表现：37 个 `slow` 用例（含 17 分钟级契约漂移、200 秒依赖 DLL 扫描、发版变量判定）只在带 `--run-slow` 时运行；**没有任何 workflow 传该参数**（release-readiness 的 `--full` 也不传），与 `pyproject.toml` 中"只在发版门禁开启"的注释矛盾。
- 是否复现：**静态判断**（`grep -rn "run-slow" .github/ scripts/` 零命中；`scripts/run_tests.py` 是唯一支持者）。
- 根因：门禁脚本拼参数时漏了 slow；注释与实现脱节。
- 影响面：发版前最该跑的重扫描从不跑。
- 安全或数据风险：中（依赖漏洞/契约漂移可在发布时漏检）。
- 修复方案：`verify_readiness.py --full` 增加 `--run-slow`；或把这 4 个文件拆成独立 scheduled workflow。
- 需要改的文件：`scripts/verify_readiness.py`、`.github/workflows/release-readiness.yml`。
- 验证方法：CI 日志出现这 37 条用例执行记录。
- 等级：**P2**；状态：**OPEN**

### QA-011 / CI / `.github/workflows/ci.yml:331-361`
- 表现：两处门禁**显式容忍 `exit 2`**：A4 门禁与"agent-collaboration"门禁；注释自认多智能体/记忆层"causally DECORATIVE"，0/3 真实 agent 与 3/3 ghost agent 结果位级相同。
- 是否复现：**静态判断**（脚本 `|| [ $? -eq 2 ]` 分支）。
- 根因：用了 `2` 表示"已知红/已知缺口"，CI 永久放行。
- 影响面：这两个质量面永不阻断发布；"红着走"制度化。
- 安全或数据风险：低-中。
- 修复方案：把已知红改成 `xfail(strict=True)` + issue 门禁，而不是 CI 容忍；或直接删除装饰层。
- 需要改的文件：`.github/workflows/ci.yml`、对应 `verify_*.py`。
- 验证方法：去掉 exit-2 容忍后跑一次 CI，确认红的原因被记录并修复。
- 等级：**P2**；状态：**ACCEPTED RISK**（当前被明示接受）

### QA-012 / CI / `.github/workflows/ci-cd.yml`
- 表现：`--cov=src --cov-fail-under=80` 只出现在 `workflow_dispatch/tag` 触发的工作流；主 `ci.yml` 无 coverage；本地 `pytest-cov 7.1.0` 已装但 `addopts` 不带 `--cov`。
- 是否复现：**静态判断**。
- 根因：覆盖率迁移到手动作业，自动路径未接管。
- 影响面：覆盖率阈值"存在但永不执行"，80% 是纯装饰。
- 安全或数据风险：低。
- 修复方案：主 CI 增加 coverage 作业（可只对 `src/` 关键域），或删除失效阈值避免误导。
- 需要改的文件：`.github/workflows/ci.yml`、`ci-cd.yml`、`pyproject.toml`。
- 验证方法：一次 PR 触发后 artifacts 里有 `coverage.xml`。
- 等级：**P2**；状态：**OPEN**

### QA-013 / 门禁 / `scripts/verify_audit_integrity.py`、`scripts/verify_audit_chain_no_fork.py:196-250`、`scripts/verify_metrics_persist.py`
- 表现：三处"跳过即绿"：SKIP 计入 PASS；无 `audit_store.db` 时 `return 0`；`METRICS_PERSIST` 未开时 `return 0`。
- 是否复现：**静态判断 + 本机事实**（仓库根无 `audit_store.db`，所以链不分叉门禁确实"绿但未验"）。
- 根因：汇总语义把 SKIP 当成功；环境未就绪时用成功退出。
- 影响面：审计完整性/指标持久性两道门禁可以长期空转。
- 安全或数据风险：中（审计链是最强合规证据）。
- 修复方案：SKIP 与 PASS 分开计数，`--full` 下 SKIP 使整体退出 3；无 DB 时改用"生成临时 DB 再验"。
- 需要改的文件：上述 3 个脚本。
- 验证方法：临时移走 `audit_store.db`，`verify_audit_integrity.py` 退出码应非 0 或 3。
- 等级：**P2**；状态：**OPEN**

### QA-014 / 测试自身 / `tests/kernels/test_kernel_registry.py:214-218`、`tests/kernels/test_lifecycle_runtime_driven.py:224`
- 表现：`except Exception: pytest.skip("无法在当前环境构建完整网关客户端…")`；本次运行实际触发，skip 文本里带着**真实 403**：`当前登录的不是个人账号，不能执行这个操作`。
- 是否复现：**已复现**（分块 junit 的 skip message 含完整 403 响应）。
- 根因：为兼容旧环境写的兜底 skip 没有排除"断言失败/权限回归"。
- 影响面：401/200 真实门禁链路在"任何环境"都可能被跳过；且把产品权限缺陷乔装成环境问题。
- 安全或数据风险：中。
- 修复方案：只把 `TestClient` 构造类异常列为 skip 条件；断言失败一律抛出（另一文件已这样做，可对齐）。
- 需要改的文件：`tests/kernels/test_kernel_registry.py`、`test_lifecycle_runtime_driven.py`。
- 验证方法：把 skip 收窄后此用例应红，暴露 403 问题。
- 等级：**P2**；状态：**OPEN**

### QA-015 / 测试资产 / `tests/desktop/test_desktop_repair.py:1`
- 表现：文件首字节含 UTF-8 BOM（`EF BB BF`），`tests/test_vault_dependency_honesty.py` 扫描 `ast.parse` 时报 `SyntaxError: invalid non-printable character U+FEFF`（红）。
- 是否复现：**已复现**（分块 `root.xml` 有完整报错；字节级复核 BOM 存在）。
- 根因：编辑器保存带 BOM；扫描类测试未用 `utf-8-sig`。
- 影响面：任何"全仓 AST 扫描"类门禁都会红；BOM 也可能干扰多行导入。
- 安全或数据风险：低。
- 修复方案：去 BOM；扫描器统一 `encoding="utf-8-sig"`。
- 需要改的文件：`tests/desktop/test_desktop_repair.py`、`tests/test_vault_dependency_honesty.py`。
- 验证方法：`pytest tests/test_vault_dependency_honesty.py -q` 绿。
- 等级：**P2**；状态：**OPEN**

### QA-016 / 策略注册 / `tests/kernels/policy/test_internal_service_policy.py:239`、`tests/kernels/test_risk_classification.py`
- 表现：`memory.delete_by_key_prefix` 未被策略显式分类（两道门禁同报）。
- 是否复现：**已复现 2 次**（分块 + 独立复跑）。
- 根因：新增内核动作未登记 `RISK`/allow-list。
- 影响面：默认拒绝（方向安全），但策略清单完整性被破坏，运维/审计无法解释该动作。
- 安全或数据风险：中（未分类动作行为不可预期）。
- 修复方案：在策略注册表显式登记该动作（含理由与风险级）。
- 需要改的文件：`src/kernels/policy/*`（注册表）。
- 验证方法：两个门禁用例转绿。
- 等级：**P2**；状态：**OPEN**

### QA-017 / 策略面 / `tests/test_policy_approval_http.py:505`
- 表现：`/v1/plugins/{plugin_id}/execute` 出现在路由表；该测试要求"策略面不存在执行路由"。
- 是否复现：**已复现 2 次**（分块 + 独立复跑）。
- 根因：插件执行被直接暴露为 HTTP 路由，绕过策略内核执行面。
- 影响面：策略面/执行面契约被打破；若鉴权仅到"人类令牌"，可能成为动作旁路。
- 安全或数据风险：**中-高**（执行路由扩大攻击面）。
- 修复方案：删除/下线该路由，或改为只经策略内核；并在契约测试中固化。
- 需要改的文件：插件路由模块、`tests/test_policy_approval_http.py`（若改为允许需加鉴权断言）。
- 验证方法：路由表快照中不再含 `execute`。
- 等级：**P2**；状态：**OPEN**

### QA-018 / 错误文案 / `tests/test_audit_low_priority_hardening.py`、`tests/test_knowledge_live.py`
- 表现：审计库不可读、知识库未启用本应返回带专门中文原因的 503，现在返回通用"服务端处理这个请求时遇到意外问题…"。
- 是否复现：**已复现**（分块 2 红；文案为通用兜底）。
- 根因：错误分类映射被统一兜底文案覆盖（或异常类型变了未登记映射）。
- 影响面：用户无法区分"暂时读不到"和"真崩了"；支持成本上升。
- 安全或数据风险：低-中（掩盖真实原因）。
- 修复方案：恢复分类映射，保留追问编号。
- 需要改的文件：网关错误映射模块。
- 验证方法：两用例转绿。
- 等级：**P2**；状态：**OPEN**

### QA-019 / 身份存储 / `tests/kernels/identity/test_human_identity_persistence.py`
- 表现：未配置时后端 location 期望 `""`，实际为 `<basetemp>/data/config/human_identities.json`；"未配置不得建文件"断言发现 `data` 目录被创建。
- 是否复现：**已复现 2 次**。
- 根因：默认位置改为跟随 `LIUHAO_DATA_DIR`（好事），但测试仍按旧契约断言。
- 影响面：测试过期造成红；也说明 conftest 的数据目录隔离对身份库有效。
- 安全或数据风险：低。
- 修复方案：更新测试契约（明确"未配置时落在数据目录，但不建文件"或相反）。
- 需要改的文件：`tests/kernels/identity/test_human_identity_persistence.py`（或实现）。
- 验证方法：该文件绿，并断言 `~/.liuhao` 无新文件。
- 等级：**P2**；状态：**OPEN**

### QA-020 / 隔离 / `src/artifacts/index.py:117-118`、`src/gateway/projects.py:99-100`、`src/tasks/journal.py:30-32`、`src/selfmod/paths.py`（`.probe_tmp`）
- 表现：测试运行向**仓库根**写入真实文件：`.liuhao_artifacts.db`、`memory_store.db-shm`、`data/knowledge_vectors.sqlite3-shm`、`.liuhao_execution.journal-shm`、`.liuhao_projects.json.lock`、`.probe_tmp\slow_default.txt`（时间戳 23:47–23:57、00:29–00:32 实测）。
- 是否复现：**已复现**（文件与 mtime 实测；`tests/test_ai_management_api.py` 的注释自认"这些文件会被本文件真的写出来（2026-10-4）"，说明是已知历史问题）。
- 根因：多个模块在环境变量缺省时回退到 CWD；conftest 只覆盖了部分模块的变量，没有统一 chdir/根目录隔离。
- 影响面：测试污染工作树；两个并发 pytest 会共享同一批 DB/WAL，制造锁竞争与假红。
- 安全或数据风险：中（真实开发数据与测试数据混写；WAL 残留可能被误提交/打包）。
- 修复方案：conftest session 级把 CWD 指到隔离目录或统一设置全部存储类环境变量；模块回退时禁止使用 repo 根。
- 需要改的文件：`tests/conftest.py`、上述 src 模块。
- 验证方法：跑完 `git status --porcelain --ignored` 与 repo 根 DB mtime 不变。
- 等级：**P2**；状态：**OPEN**

### QA-021 / 隔离 / `tests/conftest.py:1165-1195`
- 表现：desktop/安装类用例会向真实 `%LOCALAPPDATA%\LiuHao\data` 复制/写入（目录 mtime 00:31、01:10:53），session 收尾再删 `data.after-smoke-*`/`data.bak-*`。
- 是否复现：**部分复现**（mtime 实测与分块窗口吻合；当前无 `after-smoke` 残留，说明清道夫正常；但本审计崩溃两次，若崩溃发生在该窗口即会残留含密钥副本——注释自己承认副本可能含凭据/主密钥）。
- 根因：安装/升级链路按真实安装位置工作，测试未完全沙箱化，靠收尾删除兜底。
- 影响面：被杀进程 → 真实用户目录中出现含密钥的数据副本。
- 安全或数据风险：**中-高**（凭据/身份材料落地到真实目录且无事务性清理）。
- 修复方案：该类用例改成 `LOCALAPPDATA` 注入到 tmp；清理改为启动时先清（崩溃后下次运行也能回收）。
- 需要改的文件：`tests/conftest.py`、`tests/desktop/*`。
- 验证方法：在测试中把 `LOCALAPPDATA` 指向 tmp，跑完后真实目录 mtime 不变。
- 等级：**P2**；状态：**OPEN**

### QA-022 / 提交前校验 / 仓库根
- 表现：无 `.pre-commit-config.yaml`、无 husky、`.git/hooks` 仅 `.sample`；一切校验都在 push 之后。
- 是否复现：**静态判断**。
- 根因：未引入本地钩子。
- 影响面：低质量提交（BOM、断言空转、格式）先污染历史再被 CI 抓。
- 安全或数据风险：低。
- 修复方案：引入 pre-commit（最小集：BOM 检查、`python -m compileall`、前端 lint、改动文件对应 pytest 快速子集）。
- 需要改的文件：新增 `.pre-commit-config.yaml`。
- 验证方法：故意提交带 BOM 的文件应被拒。
- 等级：**P2**；状态：**OPEN**

### QA-023 / 环境 / `tests/test_provider_google_genai_parsing.py`
- 表现：5 条红，`ImportError: cannot import name 'types' from 'google.genai' (unknown location)`。
- 是否复现：**已复现 2 次**（分块与独立复跑）。
- 根因：本机 `google-genai` 安装残缺（`types` 子模块缺失）。属环境问题，不是代码问题。
- 影响面：Google 提供商解析路径在本机不可验证；CI 若装全则覆盖。
- 安全或数据风险：低。
- 修复方案：锁版本安装 `google-genai`（或 CI 固定 extras）；测试加 `importorskip("google.genai.types")` 并在 CI 强制安装（避免本地红掩盖真实）。
- 需要改的文件：`pyproject.toml` extras、测试文件。
- 验证方法：`pip install -U google-genai` 后 5 条转绿。
- 等级：**P3**；状态：**OPEN**

### QA-024 / 并发工作树 / 全局
- 表现：同一条用例在不同时间窗红/绿翻转：`test_quota_enforcement.py` 25 error（01:21–01:29）→ 39/39 绿（01:40）；`test_chat_gateway.py` 3 红（gateway 分块）→ 13/13 ×5 绿；`is_address_rejected_error` 在 01:29 不存在、01:40 已存在（`src/ai/providers.py:261`）。
- 是否复现：**已复现（翻转）**；单独 5 次复跑**未复现**随机性（判为源码并发修改，而非 flake）。
- 根因：多会话同时改 `src/` 与 `tests/`，全量运行跨 78 分钟，跨版本取样。
- 影响面：任何"全量绿/红"结论都必须带时间戳；本地与 CI 结论可能不一致。
- 安全或数据风险：低。
- 修复方案：审计/门禁运行前记录 `git rev-parse HEAD` + `git diff` 哈希；或使用 worktree 快照跑测试。
- 需要改的文件：无（流程约定）。
- 验证方法：同一 HEAD + 干净工作树连跑两次结果一致。
- 等级：**P3**；状态：**NOT REPRODUCED**（作为 flake）/ **OPEN**（作为流程风险）

### QA-025 / 覆盖盲区 / `tests/kernels/memory/test_postgres_backend.py:438`、`tests/test_clock_consistency.py:32`
- 表现：PG 真实后端与时钟回拨在**任何环境**都不验证：本机无 PG、CI 无 PG 服务、workflow 也不起；时钟用例需显式开关，CI 不设。
- 是否复现：**静态判断 + skip 实测**。
- 根因：缺 CI 服务与开关。
- 影响面：多用户/多实例存储方言与时间回拨属高风险面，长期无验证。
- 安全或数据风险：中-高（多实例数据一致性与审计时间戳）。
- 修复方案：CI 增加 `postgres:16` service 与一个时钟回拨作业；或用 testcontainers。
- 需要改的文件：`.github/workflows/ci.yml`、测试夹具。
- 验证方法：CI 日志中 PG/时钟用例执行而非 skip。
- 等级：**P3**；状态：**OPEN**

### QA-026 / 删除护栏用例 / `tests/test_cwd_delete_guard.py:134`
- 表现：守卫真实 `audit_store.db` 的用例在 `audit_store.db` 不存在时 `pytest.skip`；本机与 CI 都没有该文件 → 永跳。
- 是否复现：**已复现（skip）**。
- 根因：用例依赖"检出里正好有真实 DB"。
- 影响面：最关键的"删库保护"路径没有任何活体验证。
- 安全或数据风险：中。
- 修复方案：用 tmp 造一个同名受保护文件/目录再断言护栏。
- 需要改的文件：`tests/test_cwd_delete_guard.py`。
- 验证方法：该用例在无真实 DB 的机器上仍执行并绿。
- 等级：**P3**；状态：**OPEN**

### QA-027 / 断言质量 / 全局
- 表现：`pytest.raises(Exception)` 11 处；`tests/test_smoke.py:1` 为 `assert True`；部分 API 用例仅断状态码（如 `tests/test_api_extended.py:391`）；`tests/gateway/test_goal_truth_concurrency.py:117` mock 掉 `_save_goals` 等内部实现后断言"状态一致"。
- 是否复现：**静态判断（AST 扫描）**。
- 根因：追求"绿"优先于"断言真实行为"。
- 影响面：真实回归可穿过这些用例。
- 安全或数据风险：低-中。
- 修复方案：宽接异常改为具体类型；状态码断言补内容/副作用；删除 `test_smoke.py` 或改成真冒烟。
- 需要改的文件：上述用例。
- 验证方法：变异测试（故意改坏一处实现，对应用例应变红）。
- 等级：**P3**；状态：**OPEN**

### QA-028 / 收集门禁 / `scripts/verify_test_collection.py:67`
- 表现：`MIN_TESTS = 3550`，实际 8937；下限只挡住"腰斩"，挡不住删掉 60% 用例。
- 是否复现：**静态判断**。
- 根因：下限未随规模上调。
- 影响面：删测试不会红。
- 安全或数据风险：低。
- 修复方案：按域记录基线数并设 95% 下限，或按文件列表哈希比对。
- 需要改的文件：`scripts/verify_test_collection.py`。
- 验证方法：临时删除一个测试文件应使门禁非零退出。
- 等级：**P4**；状态：**OPEN**

### QA-029 / 仓库卫生门禁 / `tests/test_orphan_guardrail.py`
- 表现：仓库存在 1 个未登记孤儿文件（门禁报 `1 == 0`）；`tests/scripts/test_lint_audit_claims_surface.py` 报根目录新增 `RELEASE-RUNBOOK*.md` 未进扫描面。
- 是否复现：**已复现（当前工作树）**。
- 根因：并发会话新增文档/文件后未同步登记清单。
- 影响面：门禁"正常工作"（正面证据），但当前树红；也说明根目录文档治理有漂移。
- 安全或数据风险：低。
- 修复方案：登记孤儿或删除；把新文档加入扫描面。
- 需要改的文件：`scripts/_orphan_guardrail*`、`scripts/lint_audit_claims.py`。
- 验证方法：对应两用例绿。
- 等级：**P4**；状态：**OPEN**

### 附：密钥位置与类型（只报位置与类型，不复制内容）

| 位置 | 类型 | 版本管理 |
|---|---|---|
| `config/external_anchor/anchor_key.pem` | RSA 私钥 PEM（2484 字节） | 未跟踪，`.gitignore:25 *.pem` 已忽略 |
| `deploy/cloud/certs/liuhao-local.key` | 私钥 PEM（1679 字节） | 未跟踪，`.gitignore:110 deploy/cloud/` 已忽略 |
| `deploy/cloud/certs/liuhao-local.pem` | 证书 PEM | 同上 |
| `tests/test_security_compliance.py:300` | 内嵌测试 PEM/JWT 常量 | 已跟踪（测试夹具，非真实密钥） |
| `tests/desktop/test_desktop_system_layer.py:60,62,80` | 内嵌 Bearer JWT / PEM 常量 | 已跟踪（测试夹具） |

未在受跟踪文件中发现真实生产密钥；上述两台私钥均在工作树但被忽略。风险点是打包/归档时（CI `Archive Artifacts: path: .`）可能把未忽略的私钥一起上传——已忽略的不会，但 `Archive Artifacts` 打包整个目录仍需人工确认忽略规则生效。

---

## 10. 假绿清单（"看起来通过、其实没验"）

1. `tests/kernels/execution/test_failure_propagation_honesty.py:139,159,185` —— 3 个 `xfail(strict=False)` 掩盖 3 个产品缺陷（QA-004）。
2. `tests/kernels/test_kernel_registry.py:214-218` —— `except Exception → skip`；本次 skip 文本里已经带着真实 403（QA-014）。
3. `tests/kernels/test_lifecycle_runtime_driven.py:224` —— 同模式。
4. `tests/test_console_single_port.py:60` —— CI 无 dist → 5 条用例永久 skip；本地因 `mount_console` 契约变更实际红（QA-008）。
5. `tests/test_cwd_delete_guard.py:134` —— 真实 DB 删除护栏永远 skip（QA-026）。
6. `tests/test_smoke.py:1` —— `assert True`。
7. 37 个 `slow` 用例 —— 任何 CI 都不执行（QA-010）。
8. `verify_audit_integrity.py` / `verify_audit_chain_no_fork.py` / `verify_metrics_persist.py` —— SKIP 计 PASS / 无输入即 0（QA-013）。
9. `ci-cd.yml` 覆盖率 80% —— 自动路径永不执行（QA-012）。
10. `.github/workflows/ci.yml:331-361` —— 两道门禁永久接受 exit 2（QA-011）。

---

## 11. 最该先修的 5 条

1. **QA-005**：升级/备份探针写真实 `C:\LiuHaoBackups` → 约 28 条红 + 主机残留。注入 `backup_root` 到 tmp、探针捕获路径受管异常。
2. **QA-006**：RBAC/tenant 写真实 `~/.liuhao\rbac.json` → 24 条红，且在护栏失效时会直接改真实权限库。conftest 增加 RBAC/tenant 路径隔离。
3. **QA-007**：跨账号知识库列表暴露成员文档（boss 看到 2 篇）→ 数据机密性。先确认设计意图，再修过滤/契约。
4. **QA-002**：`--basetemp` 复用导致 8799 setup error；修"CLI basetemp 未登记"的时序 bug，顺便删掉 `verify_test_collection.py` 的绕行。
5. **QA-004**：3 个产品缺陷被 `xfail(strict=False)` 永久绿；至少改 `strict=True`，最好直接修 GAP A/B/C。

紧接其后：QA-003（改密码零测试）、QA-008（CI 单端口盲区 + `mount_console` 契约）、QA-009（vitest 不进 CI）。

---

## 12. 复现命令与证据索引

- 分块全量：`D:\cache\temp\qa-audit-20261108\run_chunks.ps1`，产物 `chunks\*.xml`，汇总 `agg.json`。
- basetemp 复现：`pytest tests/ --basetemp D:\cache\temp\qa-audit-20261108\basetemp` ×2；证据 `pytest_run1.xml`（40MB，8799 error/63 skip）。
- 崩溃：`pytest tests/ -q`（TEMP 独立）两次，退出码 `-2147483645`；栈与僵尸进程见 §5.3、QA-001。
- 前端：`npx vitest run`（`apps/console/console`），52 files / 557 tests，557 passed。
- 复跑：`run_flaky.ps1`（31 用例 ×3）、`run_flaky_b.ps1`（19 用例 ×3）、`confirm.ps1`（员工 ×5、单端口 ×3）、`test_chat_gateway.py` ×5。
- 断言扫描：`scan_assertions.py` → `scan_assertions_full.log`；门禁扫描：`scan_gates.py` → `scan_gates.log`；门禁矩阵 `scan_gate_matrix.py` → `gate_matrix.txt`。
