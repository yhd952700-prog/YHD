# 2026-11-04 自主执行记录（收口轮）

> 本文件记录本轮实际执行的命令与关键输出，用于证明结论来自运行证据而非推断。
> **已入库**：`docs/README.md` §4.10 索引它；`verify_docs_index_coverage.py`
> 与 `verify_doc_links.py` 在 CI 中机械校验，本文件不能虚指路径。

## 1. 起点与当前状态

- 分支：`p36`
- 起点 HEAD：`d54513ab`
- 当前 HEAD：见文件末尾「状态快照」（每轮提交后更新）
- 起点工作树：2 个测试文件、门禁脚本、门禁测试已修改；WCAG 修复未落盘

## 2. 本轮提交（12 个，按时间顺序）

| Commit | 主题 | 根因 / 内容 |
|---|---|---|
| `86be8250` | test(stability) | 限流器 `available_tokens` 读取即补充，精确等值断言不稳；改 `pytest.approx`。cwd 删除护栏的沙箱父目录在 Linux 上取到 `/` 不可写；改为「家目录 → 仓库父目录」可写搜索 + `-p no:phoenix` |
| `ba19593f` | fix(gate) | 部署门禁扫描器不识别 `echo "NAME=value" >> "$GITHUB_ENV"`，新增 shell 赋值正则；`AUDIT_DB_PATH` 证据行 768→793 |
| `22d65c1d` | fix(console) | `#d93a4a` 在 10% 混色底仅 3.84:1；新增 `--os-danger-ink`（`#b4232f`，5.65:1） |
| `5f277a87` | style(lint) | `src/` flake8 317→0；F821×7 补 import，仅 2 处 `# noqa: F401` |
| `4951f6ec` | fix(launcher) | 原生启动未加载仓库根 `.env`，`SECRET_KEY`/`APP_ENV` 对网关不可见；新增 `_load_dotenv(override=False)` |
| `c416386d` | fix(security) | Bandit HIGH 6→0（`usedforsecurity=False` ×5，`# nosec B602` ×1） |
| `ed60038d` | fix(guardrails) | C-2 的 HIGH 动作强制审计证据，缺 `AUDIT_DB_PATH` 时 `AuditStore()` 拒绝回落冻结的 HC-01 库；脚本自带临时审计库。P4 越界断言改走 `ActionExecutor`（`capability_executor` 失败即 raise，不会返回结果字典） |
| `e98e81ea` | fix(auth) | 令牌 `sub` 是身份 id（`human:liuhao`），`/v1/auth/me` 只按 principal 查 → `still_human=false`；改为双键空间解析 |
| `b330d539` | fix(deps) | 门禁同时扫 `scripts/`，`dotenv` 能 import 但未在 `pyproject.toml` 声明 |
| `f588006b` | fix(observability) | `AlertStore._save` 先截断再多次 dump，并发写留下空文件/半截 JSON，`_load` 解析失败后整库重置（CI 3.11 的 `/v1/alerts/rules` count=0）；改 `RLock` + `mkstemp` + `os.replace` |
| `5118743e` | fix(guardrails) | 守卫 job 无审计通道 → C-3 评估阶段 fail-closed 成 `verdict=error`；把 `LIUHAO_WORKSPACE_ROOT`/`AUDIT_DB_PATH`/`LIUHAO_ALERTS_STORE_PATH` 钉到 `RUNNER_TEMP`。P6 接受 `unauthorized_success`（C-1 记录态的诚实标记） |
| `0f4dcbda` | docs | KERNEL-CANON §1 十行 + 合计按 HEAD 复算（12375→13201 / 20253→21118）；修 4 条死链；索引 125→187，补 59 篇与 4 个目录 |

## 3. CI 运行序列（真实查询，`gh run view`）

| Run | Head | 结论 | 失败面 |
|---|---|---|---|
| `41731615021` | `22d65c1d` | failure | 3.11/3.12 单测、Linting、Security、Guardrails、Extended、Observability、P0（后五者多为 watchdog 级联） |
| `41767551223` | `5f277a87` | failure | Guardrails（C-2）、Extended（P6/P4）、Security（Bandit 6 HIGH）、Observability、P0 |
| `41770781190` | `ed60038d` | failure | 单测（`dotenv` 未声明）→ 全 job 级联 |
| `41771918509` | `e98e81ea` | success | Release Readiness（另一 workflow） |
| `41773132268` | `b330d539` | failure | 3.11（AlertStore 竞态）→ 级联 |
| `41774999758` | `f588006b` | failure | **仅 2 项**：Guardrails step 10 `Verify C-3 sovereignty / DEFER semantics`；Extended step 15 `Verify P6 real execution vs fail-closed honesty`。其余 11 个 job 全绿 |

### 3.1 run 41774999758 的全量单测真实数字（CI 日志原文）

```
Run Tests (3.11): 6452 passed, 31 skipped, 3 xfailed, 32 warnings in 352.19s (0:05:52)
Run Tests (3.12): 6452 passed, 31 skipped, 3 xfailed, 436 warnings in 425.22s (0:07:05)
```

命令：

```powershell
gh run view 41774999758 --json jobs > .tmp_run_latest.json
gh api --allow-escape-sequences "repos/<owner>/<repo>/actions/jobs/<job_id>/logs" |
  Select-String -Pattern 'passed|failed|error' | Select-Object -Last 8
```

### 3.2 两个失败的真根因（不是策略缺陷）

- **C-3**：`PolicyDeniedError: policy denied action='capability.retire' verdict=error
  rule=human_sovereignty`。HIGH/CRITICAL 动作带强制审计证据（`reraise=True`），
  守卫 job 没有审计库，`AuditStore()` 又拒绝默认回落到项目根冻结的 HC-01
  `audit_store.db` → 评估阶段 fail-closed 成 `error`。修法与 Extended 一致：钉
  `RUNNER_TEMP` 根。
- **P6**：`DECORATOR_MECHANISM_OK: False {'denied':['denied'],
  'success':['unauthorized_success'],'failure':['failure']}`。C-1 是记录型姿态：
  未登记规则的动作判决 `deny` 但默认不拦截，审计写 `unauthorized_success`
  才是诚实的（"执行了、但从未被授权"）。该步要证的是三态可区分，故两种成功
  形态都算"正常返回"。

本地 CI-like 环境（`AUDIT_DB_PATH` 等指向临时目录）复跑：

```
scripts/verify_c3_sovereignty.py -> RESULT: ALL GREEN (19 checks), exit=0
scripts/verify_p6_real_execution.py -> RESULT: PASS, exit=0
```

## 4. Windows 原生实机验证（当前 HEAD，端口 8099）

启动命令（`.env` 已由 `4951f6ec` 加载；JWT 密钥来自本机环境变量，不写入仓库）：

```powershell
$env:LIUHAO_JWT_SECRET = '<本机密钥>'
.venv\Scripts\python.exe -u scripts/start_liuhao.py --single-port --api-port 8099
```

启动器输出：

```
[ok] 网关 就绪（1.5s，HTTP 200）
[ok] 驾驶舱（同端口） 就绪（0.0s，HTTP 200）
驾驶舱 http://127.0.0.1:8099
API    http://127.0.0.1:8099   文档 http://127.0.0.1:8099/docs
```

### 4.1 端到端检查（`.tmp_verify.ps1`，15 项）

```
SUMMARY: 15 passed / 0 failed
```

| 项 | 结果 |
|---|---|
| `/`（驾驶舱） | 200，1628 bytes |
| `/v1/health` | 200，status=ok，provider=ollama/real/simulated=False |
| `/v1/auth/config` | 200 |
| `/v1/auth/login`（错密码） | 401（拒绝，不崩） |
| `/v1/auth/me` | 200，principal=`human:liuhao`，display_name=`Liu Hao`，scope=`L0`，status=`active`，`still_human=true` |
| `/v1/dashboard/summary` | 200，status=ready，model=qwen2.5:3b |
| `/v1/dashboard/roster` | 200，8 层 |
| `/v1/identity/summary` | 200，19 principals（agent 1 / service 1 / human 17） |
| `/v1/plugins` | 200，total=9，active=0（注册但未激活） |
| `/v1/kernels` | 200，14 内核，lifecycle 字段可读 |
| `/v1/chat` | 200，completed，turn=4，**reply=`2 + 2 = 4`**，3.3s（本机 3B） |
| `/v1/chat/history` | 200，count=8（会话记忆持久化） |
| `/v1/chat/stats` | 200，model=qwen2.5:3b，history_messages=20 |
| `/v1/observability/metrics` | 200 |
| `/v1/production/preflight` | 200，`ok=false`，critical_count=1：`SECRET_KEY` 占位（本机配置缺口，非代码缺陷） |

### 4.2 本机噪声（不掩盖）

- 启动时路由层打印两条 `REFUSING registry row ... integrity tag` 警告：
  `data/human_identities.sqlite3` 里有测试遗留行（`audit.operator`、
  `persist1-human`）未带 `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 完整性标签，
  因此被拒绝计入身份总数（19 而非 21）。这是**本地数据**问题，干净 checkout
  的 CI 不触发；真实人类行 `human:liuhao` 校验通过（`/v1/auth/me` 的
  `still_human=true` 即为证据）。
- 本地告警：`[ALERT][HIGH] Audit Log Sync Lag 60-68s`、`[ALERT][MEDIUM]
  High P99 Latency 3.3s`（单机 3B 模型 + 无外部审计同步），非阻断。

## 5. 门禁与文档

### 5.1 守卫/扩展（本地实跑）

| 脚本 | 结果 |
|---|---|
| `verify_c2_enforcement.py` | 11/11 PASS |
| `verify_p6_e2e_file_creation.py` | 7/7 PASS（含越界被拒 + 越界目标无落盘） |
| `verify_p6_real_execution.py` | PASS（5 项断言） |
| `verify_c3_sovereignty.py` | ALL GREEN（19 checks） |

### 5.2 文档门禁（本轮修复后）

```
verify_doc_count_claims.py   -> RESULT: PASS
verify_docs_index_coverage.py-> 187/187 (100.0%) PASS
verify_doc_links.py          -> 0 dangling（689 条相对链接）
```

### 5.3 未闭合的人工裁决项（明确外部阻塞，不擅自武装）

`scripts/verify_deployment_gate_vars.py` 在 CI 中会报 11 条 findings：

- 8 × `gate-var-never-set`：`LIUHAO_EXECUTOR_FENCE`、
  `LIUHAO_REQUIRE_EXECUTOR_FENCE`、`LIUHAO_POSTURE_STRICT`、
  `AUDIT_DB_BACKUP_PATH`、`LIUHAO_EXECUTOR_FENCE_LEASE_DIR`、
  `LIUHAO_SANDBOX_ENFORCE`、`LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS`、
  `LIUHAO_API_KEY_STORE`
- 3 × `unclassified-gate-candidate`：`LIUHAO_ALLOW_SIMULATED_EXECUTION`、
  `LIUHAO_CONSOLE_DIST`、`LIUHAO_WORKSPACE_ROOT`

仓库自身在 `ci.yml:738` 注释与 `docs/ci/gate-exemptions.md` 明确写明这是
**open human adjudication / task #250**、「honest repository verdict, not a
wiring gap」。按硬边界（不得改验收标准造假绿、不得擅自武装生产开关），本轮
不添加 ALLOWLIST、不改门禁，保留为明确外部阻塞。

## 6. 状态快照

- 分支 `p36`，当前 HEAD `5026bd0b`（含模型管理功能与令牌声明修复，已推送）
- CI：`41792630502`（head `33027b36`）在 **Verify Code Quality（flake8）** 处失败，
  其余 job 多为 watchdog 级联；`Console Readability Audit (WCAG-AA)` 该轮已 success。
  flake8 的 3 条告警是模型管理改动自身引入的（未使用导入 ×2、多余空行 ×1），
  已在 `5026bd0b` 修复并推送，等待新 run。
- `verify_deployment_gate_vars.py` 的 11 条人工裁决项仍按 §5.3 保留，未擅自武装。

## 7. 模型管理功能轮（同日续做）

### 7.1 提交（按时间顺序）

| Commit | 主题 | 内容 |
|---|---|---|
| `3e5d9592` | fix(dashboard) | 时间轴按日历日构造，跨夏令时不再少一天 |
| `93427248` | test(gate) | 门禁证据行号随 `ci.yml` 漂移同步（`:793` → `:806`） |
| `deb52256` | test | 身份/安全测试隔离身份库；协调测试补瞬时重试与诊断 |
| `72988cc5` | feat(models) | 后端：SQLite 持久化、AES-256-GCM 密钥加密、CRUD/测试连接 API、运行时热加载 |
| `702dd5cb` | feat(console) | 前端：Settings 页「模型管理」列表/表单/测试/设为默认/删除 |
| `33027b36` | test(models) | 45 条后端用例 + 8 条前端映射用例；模型库按用例隔离 |
| `ebdd6cca` | fix(auth) | `issue_console_token.py` 签发的 claims 与身份记录同源（scope/permissions/metadata） |
| `5026bd0b` | style(lint) | 清理模型管理引入的 flake8 告警，`src/` 恢复 0 告警 |

### 7.2 设计与关键取舍

- **存储**：stdlib `sqlite3` + WAL + `CREATE TABLE IF NOT EXISTS`，DB 默认
  `data/model_registry.sqlite3`（`data/` 已 gitignore）；不引入 ORM 迁移框架，
  与既有 `ConversationStore` 同款约定。
- **密钥**：`AesGcmCipher`（AES-256-GCM），AAD=`ai-model:{id}`，密文
  `enc:v1:<b64 nonce>:<b64 ct>`；密钥来源 `LIUHAO_MODEL_CONFIG_KEY`（SHA-256 派生）
  或 `LIUHAO_MODEL_CONFIG_KEY_FILE`（默认 `data/model_config.key`，自动生成）。
  拿不到密钥时**拒绝保存**（fail-closed），不以明文落盘。接口响应只返回
  `api_key_masked`；非 `enc:v1:` 前缀的存量值拒绝按明文使用。
- **热加载**：`resolve_provider(model_id)` 每次请求重新从注册表解析并即时构造
  provider，不缓存；`get_provider()` 改为「注册表默认 → `.env` 兜底」，显式注入的
  `_provider_instance` 仍最高优先（保护既有测试）。会话复用也不缓存实例，
  默认模型/密钥变更对已存在会话立即生效。
- **信任缺陷**：非法 `AI_PROVIDER_TYPE` 不再静默降级 mock，改为
  `warnings.warn` + `logger.error` + 抛 `AIProviderConfigError`，`/v1/health`
  如实返回 `mode=invalid`；API Key 支持 `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`
  等别名（与 roster `required_env` 一致，有单测锁定两边一致）；
  `config/default.yaml` 的 providers/fallback_chain 已显式标注「未启用、无运行期读取路径」。

### 7.3 真实运行证据（本机 Windows + 真实 Ollama `qwen2.5:3b`）

端到端脚本 15/15 PASS（服务 `127.0.0.1:8099`，全程未重启）：

```
GET  /v1/models                         200 count=0
POST /v1/models                         -> id e2b958b7be12 (ollama)
POST /v1/models/{id}/test                ok, latency=3343ms（真实最小请求）
POST /v1/chat/completions                '5'（保存后立即生效）
POST /v1/models (mock + 假密钥)          -> 响应 sk-e…9876，无 enc:v1: 前缀
sqlite api_key_enc                       密文，且整行不含明文
同一会话即时切默认 → mock                 reply 以 "LiuHao AI OS status:" 开头
model_id 指定 ollama                      '你好！有什么可以帮助你的吗？'
DELETE 默认 mock → 提升 e2b958b7be12      对话即时回落 ollama '5'
GET  /v1/health                          type=ollama mode=real simulated=false source=model_config
最终 GET /v1/models                      1 条且为默认；列表/详情无密钥泄露
```

浏览器实机（登录会话注入 localStorage，使用构建后的真实 SPA）：

- 设置页出现「模型管理」与既有模型；
- 表单填 `ui-mock / mock / mock-model / timeout=5 / api_key=sk-ui-test` →
  「连接成功（0 ms）」→「添加模型」后列表出现；
- 密钥列仅脱敏，DOM 中无明文；
- 刷新后配置仍在；删除后恢复 1 条。

### 7.4 门禁与测试（本机）

```
tests/test_model_config.py + tests/test_gateway_models_api.py   45 passed
provider/roster/chat 相关回归（7 文件）                          111 passed
tests/test_issue_console_token_claims.py                          2 passed
python -m flake8 src/ --max-line-length=100 --select=E,F,W \
  --ignore=E501,W503                                              0 告警（exit 0）
python -m py_compile scripts/ops/audit_console_readability.py     exit 0
npm run build (tsc -b && vite build)                              exit 0
npm run lint (oxlint)                                             0 error / 2 warning（exit 0）
npm test (vitest)                                                 56 passed（6 文件）
```

WCAG-AA 审计（本地 Chrome + demo 人类 `ceo`，3 种视口 × 19 页）真实输出：

```
覆盖文本样本 8136 条；低对比违规 0 处；运行时问题 0 条   （exit 0）
```

其中 `settings` 页（新增「模型管理」所在）在 desktop/web/mobile 三档均 PASS。

