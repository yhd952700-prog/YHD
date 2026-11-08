# openapi.yaml 增量补登清单（7 条运行时路径）

> 任务：把 `verify_openapi_contract.py` 报出的 7 条 missing 路径补进 `openapi.yaml`。
> 只做增量补登：不重排、不格式化既有内容、不回滚任何既有条目。

## 写入前状态（证据）

- `git status --porcelain -- openapi.yaml` → ` M openapi.yaml`（工作树已有他人改动）。
- `openapi.yaml` 最近写入时间：`2026-11-07 13:07:45`；连续两次复查未变化
  （2026-11-08 检查时仍是该时间），判断当前没有别的线正在写这个文件。
- `git diff --stat -- openapi.yaml` → `550 insertions(+), 25 deletions(-)`，
  这些既有改动全部保留，本次只在下面 12 个锚点处插入。
- 运行时来源：`src.gateway.main.get_app().openapi()`（不在跑服务）。

## 补登范围（路径 / 方法 / 请求与响应要点）

| # | 路径 | 方法 | 请求要点 | 响应要点 | 插入锚点（其后插入） |
| --- | --- | --- | --- | --- | --- |
| 1 | `/v1/about` | GET | 无请求体；可带 `x-liuhao-token` / `authorization` | 200 JSON（版本、目录、许可证与第三方声明）；422 校验错误 | `  /v1/accounts:` 之前 |
| 2 | `/v1/accounts/export/all` | POST | 可选 `ExportAllRequest`（`include_files`，默认 true） | 200 JSON（一键导出 ZIP 的结果）；422 | `  /v1/accounts/migrate:` 之前 |
| 3 | `/v1/chat/export` | POST | 必填 `ChatExportRequest`（`format` md/json 默认 md；`session_id` 可空） | 200 JSON（对话文本/清单）；422 | `  /v1/chat/history:` 之前 |
| 4 | `/v1/knowledge/export` | POST | 无请求体 | 200 JSON（可回导 JSON + 纯文本正文）；422 | `  /v1/knowledge/ingest/jobs:` 之前 |
| 5 | `/v1/knowledge/import` | POST | 必填 `KnowledgeImportRequest`（`documents` 数组） | 200 JSON（导入条数/去重结果）；422 | 同上（紧接 #4） |
| 6 | `/v1/memory/import` | POST | 必填 `MemoryImportRequest`（`entries` 数组） | 200 JSON（导入条数/去重结果）；422 | `  /v1/models:` 之前 |
| 7 | `/v1/models/discover` | POST | 必填 `DiscoverPayload`（`provider` 必填；`api_key`/`base_url`/`model_id`/`timeout` 可选） | 200 JSON（上游模型列表；失败时 `manual=True`）；422 | `  /v1/models/presets:` 之前 |

### 补登过程中新暴露的第 8 条（同批收口）

7 条补完后校验剩 1 条 missing，是运行时确实存在、但既不在原 7 条清单里、也不在文件里的路径：

| # | 路径 | 方法 | 请求要点 | 响应要点 | 插入锚点（其前） |
| --- | --- | --- | --- | --- | --- |
| 8 | `/v1/accounts/{principal}/usage/events` | GET | 路径参数 `principal`；可选 query `correlation_id`/`since`/`until`/`limit`(50)/`offset`(0) | 200 JSON（逐笔用量明细，分页）；422 | `  /v1/alerts:` 之前 |

它只引用既有 `HTTPValidationError`，无需新增 schema。为了达成「0 missing」，一并增量补登。

## 需要同步补进 `components.schemas` 的定义

这 7 条路径引用了 5 个当前 `openapi.yaml` 里不存在的 schema（跑契约校验前先确认）：

| schema | 插入锚点（其前） | 字段要点 |
| --- | --- | --- |
| `ChatExportRequest` | `    ChatRequest:` 之前 | `format`（默认 md）、`session_id`（可空，≤128） |
| `DiscoverPayload` | `    EmployeeUpdateRequest:` 之前 | `provider`（必填）、`api_key`、`base_url`、`model_id`、`timeout`（1–60，默认 10） |
| `ExportAllRequest` | `    GoalCreateRequest:` 之前 | `include_files`（默认 true） |
| `KnowledgeImportRequest` | `    LoginRequest:` 之前 | `documents`（object 数组） |
| `MemoryImportRequest` | `    MemoryWriteRequest:` 之前 | `entries`（object 数组） |

这些 schema 内部不再引用别的 schema；其余引用只有既有的
`HTTPValidationError`（已存在）。

## 验收

```
python scripts/verify_openapi_contract.py      # 期望 0 ghost / 0 missing / 0 method drift
python scripts/verify_test_collection.py       # 期望 PASS
```

## 验收结果（2026-11-08 实际输出）

```
$ .venv\Scripts\python.exe scripts/verify_openapi_contract.py
NOTE: /v1/startup is documented but excluded from /openapi.json by design -- 免鉴权只读启动探针（src/gateway/startup.py 用 include_in_schema=False 隐藏）；前端在登录前轮询它，所以不进 /openapi.json，但仍必须对外登记。 (unauthenticated GET -> 200)
PASS: contract matches runtime -- 134 paths (+1 documented schema-excluded), 0 ghosts, 0 missing, methods agree.
EXIT=0
```

```
$ .venv\Scripts\python.exe scripts/verify_test_collection.py
== pytest collection integrity ==
[PASS] collection reports no error other than the two known-unimportable modules :: none
[PASS] collected test count is at or above the audited floor (MIN_TESTS=3550) :: collected 8275

== PASS: 8275 tests are collectable and accounted for ==
EXIT=0
```

写入过程备注：新增块最初按运行时 `yaml.safe_dump` 的零缩进形态落进文件，被 YAML 解析成根级键；
已按 `paths:` 的二级缩进整体右移两格（仅缩进，不改内容），解析后 `paths` = 135 个键、
`components.schemas` = 47 个，7+1 条路径方法均为预期值，无重复键、无 ghost。
