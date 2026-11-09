# LiuHao AI OS · 鎏灏智能中枢

> **Human-Sovereign Agent Operating System（人类主权自主智能体 OS）。**
> 由十源 DNA（ULTRON / VISION / ADA / EDITH / FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）
> 统一形成。形态是**系统集成**，不是统一人格。
>
> **命名口径**：主品牌 **LiuHao AI OS · 鎏灏智能中枢**，品类
> **Human-Sovereign Agent Operating System**。唯一字面来源是
> [`src/brand.json`](src/brand.json)；代码与文档不得另写一套主名。
> 历史名称 **LIUHAO X / 鎏灏 X** 已收敛为旧称，不再作为主品牌引用。

## 获取与安装（唯一入口）

**当前为内网 / 本地交付：公网发布页尚未建立（TODO：公网发布页待建立）。** 可交付产物只从本仓库构建输出目录取，不要从来源不明的网盘/聊天附件安装：

- 安装包：`dist/installer/LiuHao-Setup-0.1.1.exe`
- 校验值：`dist/installer/SHA256SUMS.txt`（与安装包同目录；升级包哈希也登记在同一份表）
- 升级包（只给已安装的机器）：`dist/release/liuhao-0.1.1-upgrade.zip`

> ⚠️ 0.1.1 **尚未转正**。冒烟状态、产物版本与校验值一律以
> `dist/smoke-report.json`、`dist/release/LIUHAO_RELEASE.json` 和
> `dist/installer/SHA256SUMS.txt` 为准：当前唯一冒烟记录为登录 `FAIL`，
> release notes 自述产物未验证。新包冒烟通过前不要对外分发。
> 安装步骤见 [`docs/END-USER-GUIDE.md`](docs/END-USER-GUIDE.md) 第 1～3 步。

## 当前状态（诚实头 — 状态快照，不是发布就绪声明）

> **本节的唯一权威来源是 [`MASTER_PROJECT_READINESS.md`](MASTER_PROJECT_READINESS.md) 与
> [`RELEASE-READINESS-CONTRACT.md`](RELEASE-READINESS-CONTRACT.md)。**
> 本项目门禁纪律：每个门只能写 `PASS / FAIL / BLOCKED / NOT VERIFIED`；没有证据就只能写
> `NOT VERIFIED`，**不把“有代码”当“已实现”、不把“测试通过”当“生产已验证”**。本节如实呈现，不美化。

- **发布就绪判定：`NOT RELEASE READY`。** 12 项工程门禁（C1–C12）最近一次记录为
  **PASS=9 · FAIL=0 · BLOCKED=1 · NOT VERIFIED=2**（`release_ready=false`）。以运行时
  `python scripts/verify_readiness.py` 重算为准，**不引用任何签入的副本**。
- **HC-01（审计主链运行时完整性）= `UNVERIFIED` / `GO=BLOCKED`** —— 这是**人类主权决策**，
  已隔离（Decision Isolation），**不阻塞其余工程**；但在人类决策落定前，**系统不能因 HC-01
  被宣布为 RELEASE READY**，且任何“防篡改 / 权威证据源”类表述都**必须**带上 `UNVERIFIED`
  限定。依据见 [`docs/autonomous/GOVERNANCE.md`](docs/autonomous/GOVERNANCE.md) §2 与
  [`CONTRIBUTING.md`](CONTRIBUTING.md)。
- **产品门禁与工程门禁分离**：工程门禁强 ≠ 产品可用。产品门禁见
  [`docs/product/PRODUCT-READINESS.md`](docs/product/PRODUCT-READINESS.md)。

### 已实测可运行（历史快照，非“已完成”声明）

| 项 | 状态 |
| --- | --- |
| 全量测试 | **以最近一次全量运行为准**：本机执行 `.venv\Scripts\python.exe -m pytest tests/`，看最后一行的 passed/failed/skipped 汇总。仓库内任何签入的测试数字都只是历史快照，不当作交付结论 |
| 网关 | 实测可启动，`/v1/health`、`/v1/ready`、`/docs` 返回 200 |
| 控制台（驾驶舱） | 实测可启动（5173），已把 `/v1` 代理到网关 8080 |
| 真实推理 | 本机 Ollama 模型端到端跑通（实测返回真实中文回复）。注意两条代码路径默认模型不同：首启向导默认 `qwen2.5:7b`，源码冒烟脚本默认 `qwen2.5:3b`（见 `src/ai/chat.py`） |
| 端到端冒烟 | 9/9 通过（含 KAREN 画像闭环：写入画像后能正确回忆时区与语言） |
| DoD 七维 | Implemented / Tested / Observable / Permissioned / Policy Controlled / Audited / Documented —— **逐内核真实状态**见 [`docs/spec/KERNEL-DOD-AUDIT.md`](docs/spec/KERNEL-DOD-AUDIT.md)，不以本行为完成度结论 |

> ⚠️ 上表是**可运行性快照**，不是“项目已完成”。发布判据与全部未验证项以本节开头指向的
> SSoT 为准。

---

## 5 分钟跑起来（Windows 原生，不需要 Docker）

**最简单的办法**：双击仓库根目录的 `start-liuhao.bat`。它会起服务并自动打开浏览器；
关掉窗口（或在窗口里按 `Ctrl+C`）即停止。这是**源码仓库的命令行启动方式**；
安装版的桌面快捷方式走托盘常驻形态：关窗只是隐藏到托盘，退出用托盘菜单（见 `docs/END-USER-GUIDE.md`）。

命令行等价：

```bash
# 1) 一键启动：网关 8080 + 控制台 5173，并自动打开浏览器
.venv\Scripts\python.exe scripts\start_liuhao.py
```

启动器会**等健康探针真正通过**才返回，并打印：

```
鎏灏已就绪
  驾驶舱    http://127.0.0.1:5173
  API       http://127.0.0.1:8080     文档 http://127.0.0.1:8080/docs
  健康探针  http://127.0.0.1:8080/v1/health
```

（启动器输出的这一行原文是「驾驶舱」；产品术语统一按「控制台」理解。）

`Ctrl+C` 会**把子进程一起带走**，不留孤儿进程。

### 只起后端

```bash
.venv\Scripts\python.exe scripts\start_liuhao.py --backend-only
```

### 手动启动（等价）

```bash
# 网关
.venv\Scripts\python.exe -m uvicorn src.gateway.main:app --host 127.0.0.1 --port 8080

# 控制台（另一个终端）
cd apps/console/console
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

### 环境要求

- Python 3.11+（仓库自带 `.venv`；依赖见 `requirements.txt`）
- Node.js（控制台已装好 `node_modules`）
- **Ollama**（本机 LLM，可选）。默认模型口径：**首启向导默认 `qwen2.5:7b`**，
  **源码冒烟脚本默认 `qwen2.5:3b`**（见 `src/ai/chat.py`）；两者都可在界面/环境变量里改。
  安装包本身不包含任何模型权重，需要时自行 `ollama pull qwen2.5:7b`。
- 数据库：**默认 SQLite**（`sqlite:///./liuhao_ai_os.db`），不需要 Postgres。
  Postgres / Redis 仅在 `docker-compose.infra.yml` 里作为可选基础设施。

---

## 验证它真的在跑

```bash
# 网关健康（注意 --noproxy，见下方坑）
curl --noproxy '*' http://127.0.0.1:8080/v1/health
# => {"status":"ok","timestamp":...}

# 端到端冒烟
.venv\Scripts\python.exe scripts\smoke_mvp.py

# 真实对话
curl --noproxy '*' -X POST http://127.0.0.1:8080/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好","session_id":"smoke"}'
```

---

## 架构

```
规格层   docs/spec/          MASTER-SPEC v3.0 / UNIFIED-BLUEPRINT / KERNEL-CANON
内核层   src/kernels/        14 个内核
能力层   src/ai/  +  LiuHao-O/packages/
场景层   /v1/*  网关（FastAPI, 8080）  +  控制台（vite, 5173）
```

**14 个内核**：audit、capability、context、evaluation、event、execution、identity、
memory、network、plugin、policy、resource、security、trust。

**网关路由以仓库根 [`openapi.yaml`](openapi.yaml) 为准**（当前 141 条 path，随代码增删）。
常用入口包括 `/v1/chat`（含 stream / sessions / history / stats）、`/v1/dashboard/*`、
`/v1/profile*`、`/v1/policy/*`（审批与拦截态势）与 `/knowledge/*`。

---

## 目录导览

| 路径 | 说明 |
| --- | --- |
| `src/kernels/` | 14 个内核（权威定义见 `docs/spec/KERNEL-CANON.md`） |
| `src/gateway/` | FastAPI 网关与路由 |
| `src/ai/` | 能力层 |
| `LiuHao-O/packages/` | 十源 packages 层（SSOT = `LiuHao-O/packages/MAPPING.md`） |
| `apps/console/console/` | 控制台前端（vite + React + TS） |
| `scripts/` | `verify_*.py` 护栏（数量以 `scripts/` 实际文件为准，当前 78 个）+ `start_liuhao.py` + `smoke_mvp.py` |
| `docs/spec/` | 全部规格文档 |
| `capability-registry.yaml` | 能力注册表 |

## 关键文档

- `docs/RUN.md` —— **完整运行手册**（启动/登录/冒烟/四道 CI 门禁，命令均在本机实测跑通）
- `docs/QUICKSTART-RUN.md` —— 极简启动手册（5 分钟版；登录与门禁部分以 `docs/RUN.md` 为准）
- `docs/spec/KERNEL-CANON.md` —— 内核权威定义
- `docs/spec/POLICY-ENFORCEMENT-DESIGN.md` —— Policy C-1..C-7 设计与裁决
- `docs/spec/REPO-LINE-CONVERGE.md` —— 仓库线收口（两条不相干历史的处置）
- `docs/spec/GAP-MIGRATION-MATRIX.md` —— 21 个 Phase 的逐项实测

---

## 已知限制与本机坑

1. **Docker 路线本机跑不通** —— Docker Desktop 装了但 daemon 没起
   （`docker info` 报 `npipe:////./pipe/dockerDesktopLinuxEngine` 找不到）。
   请用上面的**原生方式**启动。
2. **curl 必须加 `--noproxy '*'`** —— 本机 env 有活的 `http_proxy`，
   直接 `curl localhost` 会报 `upstream connect failed`，看起来像服务没起来。
3. **`/v1/policy/*` 默认 401** —— 这是**刻意设计**：拦截态势会暴露未受管动作集合，
   后端要求令牌。用 `scripts/issue_console_token.py --list` 取本机主体、签发令牌。
4. **审批通道默认是关闭的（fail-closed）** —— 自 Policy C-7 起人类身份改为
   **正向白名单**，未登记任何人类身份 ⇒ 无人可审批。登记方式：
   `scripts/register_human_identity.py --principal <name>`。
   生产环境需挂载 `config/human_identities.json` 并设置 `LIUHAO_HUMAN_IDENTITIES_FILE`。
5. **`implementation-status.UNRELIABLE.yaml` 不可信** —— 文件名即结论，
   请以上述实测与 `capability-registry.yaml` 为准。
