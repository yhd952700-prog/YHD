# LIUHAO X —— 生产运行手册（Production Runbook）

> **本文件取代了此前的通用模板。** 旧版描述的是「负载均衡 + API 网关 + Redis + Postgres +
> Prometheus/Grafana + K8s」的泛化架构，**与本仓库真实形态不符**（网关不依赖 Redis/Postgres，
> 运行时审计/会话/身份走 **SQLite 文件**；形态是**单端口自服务**），且含 `+1-XXX-XXX-XXXX`
> 之类的占位联系方式。误导性的运维手册比没有更危险 —— 本版按运行时事实重写。
> 维护者：Vigil。2026-09-13。

---

## 1. 真实架构（一句话）

**一个 FastAPI 网关进程，单端口同时提供 API 与驾驶舱前端；状态落在本地 SQLite 文件。**

```
                    ┌──────────────────────────────────────────┐
  浏览器  ───────►  │  单端口网关（uvicorn / FastAPI）           │
                    │  ├── /v1/*            API（含驾驶舱数据）   │
                    │  └── /               驾驶舱静态产物(console/)│
                    └───────────────┬──────────────────────────┘
                                    │ 同源，零 CORS
                 ┌──────────────────┼───────────────────────────┐
                 ▼                  ▼                           ▼
        SQLite 审计/会话      身份持久化(JSON/SQLite)      14 内核 / 能力层
        (*.db，文件)          (LIUHAO_HUMAN_IDENTITIES_*)   (进程内)
```

**关键事实（与旧模板的差异）**：

| 旧模板声称 | 真实情况 |
|---|---|
| 需要 Redis (6379) | **不需要**。网关不连 Redis。 |
| 需要 Postgres (5432) | **不需要**。运行时用 SQLite 文件；`alembic`(47 表) 是另一条互不相干的链路。 |
| `/health`、`/ready`（无前缀） | 真实路径带前缀：**`/v1/health`**、**`/v1/ready`**。 |
| 前端独立部署 | 前端由网关**自服务**（`console/` 或 `LIUHAO_CONSOLE_DIST`），同源。 |
| K8s 集群 | 三种部署形态（§2），K8s 不是默认。 |

---

## 2. 三种部署形态

### 2.1 云发布包（自包含单端口，推荐给「一键上线」）

```bash
# 1) 构建（会测量网关真实 import 图生成 requirements，并复制驾驶舱产物）
.venv/Scripts/python.exe scripts/build_cloud_bundle.py

# 2) 本地自测
cd deploy/cloud && PORT=8080 python serve.py

# 3) 发布：把 deploy/cloud/ 交给单端口托管（上传器会剥离 dist/ 等构建目录，
#    故前端以 console/ 普通目录名随包）
```

发布包内容：`src/`（网关源码）、`config/`、`console/`（驾驶舱）、`capability-registry.yaml`
（名册端点需要）、`serve.py`、`requirements.txt`（由 import 图 + 显式运行时依赖生成）。

### 2.2 Docker

```bash
docker compose -f docker-compose.prod.yml up -d
```

> ⚠️ 生产清单会**默认武装 `LIUHAO_KERNEL_POLICY_ENFORCE=HIGH,CRITICAL`**（见 §4）。

### 2.3 本机（开发/自托管）

```bash
.venv/Scripts/python.exe scripts/start_liuhao.py --single-port          # 单端口
.venv/Scripts/python.exe scripts/start_liuhao.py --single-port --https  # 叠加本地 TLS
```

---

## 3. 健康检查与验证（真实命令）

```bash
curl -s http://127.0.0.1:8080/v1/health            # 存活：200 + {"status":"ok"}
curl -s http://127.0.0.1:8080/v1/ready             # 就绪：检查 key/jwt/rbac/enc/limiter
curl -s http://127.0.0.1:8080/v1/dashboard/roster  # 名册：available 应为 true
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/   # 驾驶舱：200
```

**判据**：`/v1/ready` 返回 200（`status:"ready"`）；`/v1/dashboard/roster` 的 `available` 为
`true` 且 `totals.kernels==14`、`totals.layers==14`。若 `available:false`，多半是
`capability-registry.yaml` 不在网关根目录（发布包根）。

> 冷启动提示：进程起来后前 1–3 秒内探测可能失败（初始化竞态）。先等 `/v1/ready` 返回 200 再探其它端点。

---

## 4. 人类主权与身份表（**生产强制**）

本系统的最高原则是 `Human Sovereignty Above All`。生产上线**必须**处理身份表，否则内核层
策略执法在 armed 状态下会 **fail-closed**：身份表未挂载 → 0 humans → HIGH/CRITICAL 动作**全拒**。

```bash
# 注册一个人（写入身份持久化；默认 file 后端，或 sqlite）
python scripts/register_human_identity.py ...

# 相关环境变量
LIUHAO_HUMAN_IDENTITIES_FILE=<path>        # file 后端（默认）
LIUHAO_HUMAN_IDENTITIES_BACKEND=file|sqlite
LIUHAO_HUMAN_IDENTITIES_DB=<path>          # sqlite 后端
```

**内核层策略执法的默认与开关**：

- `LIUHAO_KERNEL_POLICY_ENFORCE`（**默认空 = 不拦截**，只记录 / L1）。
  取值形如 `HIGH,CRITICAL` 或具体动作名。**只有 HIGH/CRITICAL 层可被武装**；LOW/MEDIUM
  不在执法切割线内。typo 会**大声失败**，不会静默变成「未武装」。
- 43 个生产 `@kernel_action` 调用点**一律 `enforce=False`**（由 AST 护栏锁定，不许散开写死）。
- 裁决：**生产是否 arm 属部署决定**（前置条件：身份表已挂）。arm/回滚 = 改一个环境变量 + 重启，
  零代码改动、零成本回滚。

---

## 5. 关键环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `PORT` | 单端口监听端口 | `8080` |
| `LIUHAO_CONSOLE_DIST` | 驾驶舱静态产物目录 | 包内 `console/` |
| `LIUHAO_HUMAN_IDENTITIES_FILE` | 身份表文件（file 后端） | — |
| `LIUHAO_KERNEL_POLICY_ENFORCE` | 内核层真拦截层选择 | 空（L1 记录） |
| `AI_PROVIDER_TYPE` / `*_API_KEY` | LLM provider 与密钥（默认 `mock`） | `mock` |

---

## 6. 日志与观测

- **审计**：`src/kernels/audit/`（SQLite + 哈希链，可 `verify_integrity`，能检出内容篡改与尾部截断）。
- **事件总线**：带 `correlation_id`，可追因果链。
- **trace**：`src/observability/tracing`（网关实际使用）。
- **进程日志**：标准输出（JSON 风格行）；启动行会打印端口与 provider。
- ⚠️ `src/audit/` 是**遗留并行实现**（非运行时链路），保留仅为兼容 `docs/quickstart.md` 的自检命令。

---

## 7. 备份与回滚

- **状态备份**：备份 SQLite 文件（审计 `*.db`、会话、身份表 JSON/SQLite）。这些都是**普通文件**，
  直接 `cp` 即可。
- **配置回滚**：改 `.env` / compose 变量后重启进程即可；无迁移脚本依赖。
- **策略开关回滚**：把 `LIUHAO_KERNEL_POLICY_ENFORCE` 置空并重启 → 立即回到 L1（记录）。
- **发布包回滚**：保留上一版 `deploy/cloud/` 目录，切回即可。

---

## 8. 已知限制（上线时须一并对外说明）

完整机器可读清单见 `capability-registry.yaml` → `known-open-items`。摘要：

- 本地计算沙箱（RestrictedPython 后端）只提供**能力隔离**（禁 `import`/`open`/`eval` + CPU 超时），
  **无资源隔离**（无内存上限）。
- 默认 LLM 为 `mock`（不产生真实模型智能）；真实 provider 需配置密钥。
- Network WebSocket 适配器在无 WS 依赖时**诚实拒绝**投递（不伪造成功）。
- `vault_connect` 未安装 → Vault 读密为空操作。
- Context 内核在默认权重下空转（不产出可用上下文）。

---

## 9. 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 2.0.0 | 2026-09-13 | 按**真实单端口架构**重写；移除 Redis/Postgres/K8s 假设与占位联系方式；补身份表与策略执法开关说明。 |
| 1.0.0 | 2024-01-15 | 通用模板（**已废弃**：与本仓库真实形态不符）。 |

**联系与升级**：本仓库不自造联系方式。运维联络以仓库/团队现行的 On-call 渠道为准（不在本文件内编造电话或邮箱）。
