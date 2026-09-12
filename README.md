# 鎏灏 LIUHAO X

> 由十源 DNA（ULTRON / VISION / ADA / EDITH / FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）
> 统一形成的 AI 操作系统。形态是**系统集成**，不是统一人格。

## 当前状态（2026-09-12 实测）

| 项 | 状态 |
| --- | --- |
| CI | **1446 passed / 14 skipped / 0 failed**（Python 3.11 + 3.12 双矩阵，含前端构建与 lint） |
| 网关 | 实测可启动，`/v1/health`、`/v1/ready`、`/docs` 全部 200 |
| 驾驶舱 | 实测可启动（5173），已把 `/v1` 代理到网关 8080 |
| 真实推理 | Ollama `qwen2.5:3b` 端到端跑通（实测 5.1s 返回中文回复） |
| 端到端冒烟 | **9/9 通过**（含 KAREN 画像闭环：写入画像后能正确回忆时区与语言） |
| DoD 七维 | Implemented / Tested / Observable / Permissioned / **Policy Controlled** / Audited / Documented |
| Policy 强制链 | C-1 → C-7 **全部实施完毕** |

---

## 5 分钟跑起来（Windows 原生，不需要 Docker）

**最简单的办法**：双击仓库根目录的 `start-liuhao.bat`。它会起服务并自动打开浏览器；
关掉窗口（或在窗口里按 `Ctrl+C`）即停止。

命令行等价：

```bash
# 1) 一键启动：网关 8080 + 驾驶舱 5173，并自动打开浏览器
.venv\Scripts\python.exe scripts\start_liuhao.py
```

启动器会**等健康探针真正通过**才返回，并打印：

```
鎏灏已就绪
  驾驶舱    http://127.0.0.1:5173
  API       http://127.0.0.1:8080     文档 http://127.0.0.1:8080/docs
  健康探针  http://127.0.0.1:8080/v1/health
```

`Ctrl+C` 会**把子进程一起带走**，不留孤儿进程。

### 只起后端

```bash
.venv\Scripts\python.exe scripts\start_liuhao.py --backend-only
```

### 手动启动（等价）

```bash
# 网关
.venv\Scripts\python.exe -m uvicorn src.gateway.main:app --host 127.0.0.1 --port 8080

# 驾驶舱（另一个终端）
cd apps/console/console
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

### 环境要求

- Python 3.11+（仓库自带 `.venv`；依赖见 `requirements.txt`）
- Node.js（驾驶舱已装好 `node_modules`）
- **Ollama**（本机 LLM）。默认已装 `qwen2.5:3b` / `qwen2.5:7b` / `nomic-embed-text`。
  缺模型时：`ollama pull qwen2.5:3b`
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
场景层   /v1/*  网关（FastAPI, 8080）  +  驾驶舱（vite, 5173）
```

**14 个内核**：audit、capability、context、evaluation、event、execution、identity、
memory、network、plugin、policy、resource、security、trust。

**网关 15 条路由**：`/v1/chat`（含 stream / sessions / history / stats）、
`/v1/dashboard/*`、`/v1/profile*`、`/v1/policy/*`（审批与拦截态势）、
`/knowledge/*`。

---

## 目录导览

| 路径 | 说明 |
| --- | --- |
| `src/kernels/` | 14 个内核（权威定义见 `docs/spec/KERNEL-CANON.md`） |
| `src/gateway/` | FastAPI 网关与路由 |
| `src/ai/` | 能力层 |
| `LiuHao-O/packages/` | 十源 packages 层（SSOT = `LiuHao-O/packages/MAPPING.md`） |
| `apps/console/console/` | 驾驶舱前端（vite + React + TS） |
| `scripts/` | 10 个 `verify_*.py` 护栏 + `start_liuhao.py` + `smoke_mvp.py` |
| `docs/spec/` | 全部规格文档 |
| `capability-registry.yaml` | 能力注册表 |

## 关键文档

- `docs/QUICKSTART-RUN.md` —— 极简启动手册
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
