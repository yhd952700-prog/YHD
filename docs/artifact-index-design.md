# 产物索引与执行回溯 —— 设计说明

> 范围：只回答“**给定一次执行，它产出了哪些文件**”和“**给定一个文件，它是哪次执行写的**”。
> 不镜像目录树，不做内容管理，不改执行链（接线方案在第 6 节交回，由你统一安排）。
>
> 证据口径：静态代码阅读 + 只跑 `tests/artifacts/` 这 12 条用例。未跑全量测试、未启动服务。

---

## 1. 现状核实（真实代码）

| 来源 | 路径 | 记了什么 | 缺什么 |
|---|---|---|---|
| 执行 journal | [\_journal.py](file:///d:/LiuHao-AI-OS/src/kernels/execution/_journal.py)（表 `journal_events` / `journal_idempotency`，行 74/84） | `execution_started`、`task_started`、`task_completed`、`task_failed`、`execution_completed`、`execution_cancelled`；`task_completed` 的 payload 只有 **一个** `artifact_path` | 无版本、无哈希；一个任务写多个文件时只留一个；无“文件 → 执行”反查 |
| 产物路径提取 | [\_\_init\_\_.py:936](file:///d:/LiuHao-AI-OS/src/kernels/execution/__init__.py#L936) `artifact_path_from_output` | 从输出字典的 `written` / `path` / `file` 取**第一个**非空值 | 多产物只取一个；这是执行内核的既定契约，本原型不另立标准 |
| journal 落盘点 | [\_\_init\_\_.py:1568-1574](file:///d:/LiuHao-AI-OS/src/kernels/execution/__init__.py#L1568-L1574) | 任务标为 `COMPLETED` 后立刻写 `task_completed`（含 `artifact_path`） | 写完 journal 后没有第二个消费者 |
| 执行身份 | [\_\_init\_\_.py:1277-1285](file:///d:/LiuHao-AI-OS/src/kernels/execution/__init__.py#L1277-L1285) | `execution_id = goal.id`，仅在 `journal is not None` 时设置 | 不接 journal 时 `_execution_id` 为 `None` |
| 目标历史 | [ai_management.py:1004](file:///d:/LiuHao-AI-OS/src/gateway/ai_management.py#L1004) `_collect_artifacts` | 汇总 `capability_id == "file_write"` 的已完成任务，得到**去重后的相对路径字符串列表** `goal_entry["artifacts"]` | 丢失任务归属；无哈希/版本；只覆盖 `file_write` |
| 目标 JSON 落盘 | [ai_management.py:316](file:///d:/LiuHao-AI-OS/src/gateway/ai_management.py#L316) `_save_goals` → `.liuhao_goals.json`（`LIUHAO_WORKSPACE_ROOT` 可改向） | 顶层是 `{goal_id: entry}` | 同上 |
| 文件浏览 | [files.py](file:///d:/LiuHao-AI-OS/src/gateway/files.py) | 只读列目录 / 读文件 | 没有索引、没有“谁写的” |
| 审计链 | `audit_store.db`（`src/kernels/audit/`） | principal / action / outcome / 哈希链 | 不记产物路径，不能当产物索引 |

**结论**：现在能回答的第一问只能靠解析 `.liuhao_goals.json` 的一段字符串；第二问（文件 → 执行）**当前无任何实现**。

---

## 2. 缺口清单

1. 没有“文件 → 执行”反查。
2. 没有版本与内容哈希：同名覆盖后无法区分“哪个版本是这次执行写的”。
3. `task_completed` 只带一个路径：一个任务写多个文件时其余不可追。
4. 产物是否还在盘上、是否被人改过，没有事后校验。
5. `goal_entry["artifacts"]` 只收 `file_write`，其他能力（例如未来插件）产出的文件不可见。

---

## 3. 索引结构

模块：[src/artifacts/index.py](file:///d:/LiuHao-AI-OS/src/artifacts/index.py)，表 `artifact_writes`。

### 3.1 主键与唯一约束

```
artifact_id = sha1(workspace + "\0" + path_key + "\0" + version)[:20]
UNIQUE(workspace, path_key, version)
```

- **确定性主键**：同一事件重复投递会落到同一主键上（`INSERT OR IGNORE`），不需要额外的去重表。
- **命名空间是工作区绝对路径（realpath）**。不同工作区里的 `notes/a.md` 是两条不同记录；工作区迁移后旧记录仍可读，但不会被新根“继承”。
- `path_key` 是工作区内相对路径，统一正斜杠（`notes/a.md`）。

### 3.2 字段

| 字段 | 含义 | 可为空 |
|---|---|---|
| `artifact_id` | 主键 | 否 |
| `workspace` | 工作区根（realpath） | 否 |
| `path_key` | 工作区内相对路径（正斜杠） | 否 |
| `abs_path` | 记录时的绝对路径（校验用） | 否 |
| `version` | **按路径的写入序号**：第 1、2、3 次写入观测 | 否 |
| `content_sha256` | 记录时文件内容哈希 | 是（文件当时已不在盘上） |
| `size` | 字节数 | 是 |
| `execution_id` | 执行身份（当前 = `Goal.id`） | 是（goal JSON 对账无执行归属） |
| `goal_id` | 目标 id | 是 |
| `correlation_id` | 事件关联 id | 是 |
| `task_id` / `task_name` | 任务归属 | 是 |
| `capability_id` | 产出该文件的 capability（直连时才有） | 是 |
| `agent_id` | 执行员工 | 是 |
| `journal_seq` | 对应 `journal_events.seq`，可回跳原始事件 | 是 |
| `source` | `journal` / `goal_json` / `kernel` / `direct` | 否 |
| `recorded_at` | 写入观测时间（UTC） | 否 |

### 3.3 同名覆盖与多版本

`version` 是**写入序号**，不是“第几种内容”。理由：审计要回答的是“谁在第几次执行里写了这个路径”，内容相同也仍是两次真实写入。

- 内容变了 → 新 `version`，旧行保留（append-only）。
- 内容没变、但换了执行/任务 → 也是新 `version`，因为产出者不同。
- **幂等**：`content_sha256` 相同 **且** `execution_id` / `goal_id` / `task_id` 三者完全相同 → 视为同一次写入的重试，返回原记录，不新增版本。
- 历史永远不删：`history_for_file()` 返回新 → 旧全部版本。

### 3.4 怎么关联到某次执行

三层归属，缺哪层就写 `NULL`，不编造：

```
execution_id  ←  journal 的 execution_id（当前 = Goal.id）
   goal_id    ←  目标历史里的 goal id
     task_id  ←  journal task_completed 的 task_id
     journal_seq ← journal 事件序号（可回跳原始事件）
```

注意一个**真实约束**：HTTP 入口每次提交都会生成新的 8 位 goal id，所以 `execution_id` 在一次进程生命周期内稳定；跨重启“续跑”只有在同一个 goal id 被重新提交（例如 `POST /goals/{id}/replan`）时才对得上。这不是本模块引入的问题，是现有 journal 的既有语义。

### 3.5 路径安全与诚实性

- 所有路径先过 [resolve_in_workspace](file:///d:/LiuHao-AI-OS/src/ai/workspace.py)（realpath 消解 `..`、符号链接），越界抛 `WorkspaceViolation`，**fail-closed**。
- 文件不在盘上仍然记账（`content_sha256 = NULL`），`verify()` 如实返回 `missing`，不假装成功。
- 不上传、不复制文件内容；索引只存路径 + 哈希。

---

## 4. 查询契约

### 4.1 给定执行 → 产物

```python
index.artifacts_for_execution("goal-e2e")        # 记录列表（默认每路径取该执行内最新一次）
index.paths_for_execution("goal-e2e")            # ["report.md", ...]
```

SQL 形态：

```sql
SELECT * FROM artifact_writes WHERE execution_id = ? ORDER BY version DESC;
-- 应用层按 path_key 去重，保留最高 version
```

### 4.2 给定文件 → 执行

```python
index.executions_for_file("report.md", workspace=ws)
# [{"execution_id": "goal-e2e", "goal_id": "goal-e2e", "task_names": ["write-report"],
#   "first_seen": ..., "last_seen": ..., "max_version": 2}]
index.history_for_file("report.md", workspace=ws)   # 完整版本链（新 → 旧）
index.latest_for_file("report.md", workspace=ws)
```

同一执行写同一路径多次只出现一次（按执行摘要聚合）；没有 `execution_id` 的 goal JSON 记录不会污染反查结果。

### 4.3 事后校验

```python
index.verify(artifact_id)
# present_unchanged / present_changed / missing / unverifiable
```

---

## 5. 原型的两个接入方式

### 5.1 对账模式（**零改动，已实现**）

只读消费已有落盘数据，不改执行链：

```python
index.index_journal(journal, workspace_root=ws)       # 读 journal_events
index.index_goal_file(".liuhao_goals.json", workspace_root=ws)  # 读目标历史
```

适用：启动时补账、历史回溯。局限：只能看到**现在还在盘上**的文件内容哈希；已经被覆盖多次的旧内容无法还原（旧记录仍会以 `present_changed` 暴露差异）。

### 5.2 直连模式（**未接线，方案见第 6 节**）

在文件写成功的那一刻直接 `record()`。这样才能拿到每次写入当时的真实哈希与任务归属（尤其“写完后又被别的执行覆盖”的场景）。原型已提供入口：

```python
index.record_from_task_output(
    workspace=ws, output=task_output,
    execution_id=..., goal_id=..., task_id=..., task_name=..., journal_seq=...,
)
```

---

## 6. 接线（本轮已按此处实现）

> 约束：**只加不改**。三处共享文件的改动全部是「新增可选参数 / 新增属性 / 新增一行挂载」，不传索引时走原路径。`journal_seq` 保持 `NULL`（见 8.2）。

### 6.1 `src/kernels/execution/__init__.py`

**(a) `ExecutionEngine.__init__`**：新增 `artifact_index: Any = None`，并只多一行 `self.artifact_index = artifact_index`。不传即 `None`，`_record_task_artifacts` 第一行就返回。

```python
def __init__(self, scope="L1", capability_executor=None, journal=None,
             artifact_index=None):          # ← 新增
    ...
    self.artifact_index = artifact_index    # ← 新增（None = 与历史行为一致）
```

刻意**没有**加 `set_artifact_index()`：本轮没有调用方需要在运行时替换，少一个公开面就少一处要维护的契约。

**(b) `_execute_task`**：在 `self._journal_event(task, "task_completed", ...)` 之后、`# Verify` 之前调用 `self._record_task_artifacts(task, result, ctx.goal.id)`。实际逻辑收在独立方法里：

```python
def _record_task_artifacts(self, task, result, execution_id):
    if self.artifact_index is None:
        return
    try:
        from src.ai.workspace import workspace_root
        # 归属不成立的 agent 不许写进索引（与 COMPLETED_UNATTRIBUTED 同口径）。
        agent_id = None
        if isinstance(result.output, dict) and (
            result.output.get("agent_attribution") != "unattributed"
        ):
            agent_id = task.assigned_agent
        self.artifact_index.record_from_task_output(
            workspace=workspace_root(),
            output=result.output,
            execution_id=execution_id,
            goal_id=task.goal_id,
            correlation_id=task.correlation_id,
            task_id=task.id,
            task_name=task.name,
            capability_id=task.capability_id,
            agent_id=agent_id,
            journal_seq=None,               # journal.record() 不返回 seq
        )
    except Exception as exc:                # 旁路失败不得上升为主流程失败
        logger.warning(...)
```

相对设计稿的三处收紧（都是为了记录更诚实）：

- `execution_id` 用调用方传的 `ctx.goal.id`，而不是 `self._execution_id`：后者只在接了 journal 时才非空；不接 journal 时也必须能回答「哪个执行写的」。
- `agent_id` 只在归属成立时写。执行链会把解析不到的 agent 标成 `COMPLETED_UNATTRIBUTED`，索引不能替一个不存在的执行者背书。
- 异常全吞并 `logger.warning`：索引写失败（磁盘满 / 库损坏 / 契约变更）不能让一次已经成功的执行改判为失败。

**(c) 多产物**：仍只记一个（`artifact_path_from_output` 的契约），见 8.3。

### 6.2 `src/ai/agent_runtime.py`

`AgentRuntime.__init__` 新增 `artifact_index: Any = None`，构造 `ExecutionEngine` 时透传：

```python
self.engine = ExecutionEngine(
    scope=scope,
    capability_executor=capability_executor,
    journal=journal,
    artifact_index=artifact_index,
)
```

### 6.3 `src/gateway/ai_management.py`

`_ensure_runtime` 在 `journal = ExecutionJournal(journal_path)` 之后构造索引并传入。构造包在 `try/except`：索引库建不起来时退回 `None`（原行为），不阻塞网关启动。

```python
try:
    from src.artifacts.index import ArtifactIndex, default_index_path
    artifact_index = ArtifactIndex(default_index_path())
except Exception as exc:
    logger.warning("AIStateManager: artifact index disabled: %s", exc)
    artifact_index = None
self._runtime = AgentRuntime(..., journal=journal, artifact_index=artifact_index)
```

`default_index_path()` 已按现有约定实现：`LIUHAO_WORKSPACE_ROOT/liuhao_artifacts.db` 优先，否则 `<repo_root>/.liuhao_artifacts.db`。历史账仍可用对账入口补：`index_journal(journal)` / `index_goal_file(...)`。

### 6.4 读接口（可选，下一轮）

新增只读路由（例如 `GET /v1/artifacts?execution_id=`、`GET /v1/artifacts?path=`）时，控制台才能看到“这个目标产出了哪些文件”。**本次不做**：属于共享文件 + 前端改动。

---

## 7. 测试

### 7.1 索引单元测试

文件：[tests/artifacts/test_artifact_index.py](file:///d:/LiuHao-AI-OS/tests/artifacts/test_artifact_index.py)。13 条，全部通过：

```
python -m pytest tests/artifacts/test_artifact_index.py -q
13 passed
```

| 用例 | 证明什么 |
|---|---|
| `test_artifacts_for_execution` | 执行 → 多文件；别的执行不串入 |
| `test_artifacts_for_task` | 任务 → 产物（任务级反查） |
| `test_executions_for_file` | 文件 → 执行 + 任务名 |
| `test_executions_for_file_accepts_absolute_path` | 绝对路径也能归一 |
| `test_overwrite_creates_new_version_and_keeps_history` | 同名覆盖 → v2，旧版仍在，反查列出两个执行 |
| `test_same_execution_same_bytes_is_idempotent` | 同任务重试写同内容不涨版本 |
| `test_different_execution_same_bytes_is_a_new_version` | 同内容不同执行仍是两次写入 |
| `test_path_outside_workspace_is_rejected` | 越界 fail-closed |
| `test_missing_file_is_recorded_but_verifiable_as_missing` | 文件不在也记账，校验如实报 `missing` |
| `test_verify_detects_external_change` | 被人改过 → `present_changed` |
| `test_record_from_task_output_uses_kernel_contract` | 复用执行内核的“什么算产物”判定；无产物不造记录 |
| `test_goal_file_adapter` | 读 `.liuhao_goals.json`；任务归属如实为 `None` |
| `test_journal_adapter_end_to_end` | **真实 ExecutionEngine + 真实 journal + 真实落盘** → 双向反查 + 校验一致 |

最后一条是这套设计的关键证据：它先断言 `journal.completed_task_artifacts("goal-e2e") == {"write-report": abs}`，再断言索引能从同一份 journal 反查出 `["report.md"]`，因此索引读的不是测试自造的 JSON 结构。

### 7.2 接线验收

文件：[tests/artifacts/test_artifact_wiring.py](file:///d:/LiuHao-AI-OS/tests/artifacts/test_artifact_wiring.py)。7 条，全部通过：

```
python -m pytest tests/artifacts/test_artifact_index.py tests/artifacts/test_artifact_wiring.py -q
20 passed
```

| 用例 | 证明什么 |
|---|---|
| `test_without_index_execution_is_unchanged` | 不传索引 vs 挂了索引：journal 事件流（除 `plan_id`）、任务状态、输出、验证结果逐项相同 |
| `test_unattached_index_stays_empty` | 索引对象存在但没挂上引擎 → 一次真实执行写 0 行（接线是显式的） |
| `test_wired_index_answers_both_directions` | 传索引 → 执行成功；执行→产物、文件→执行都能反查；`verify` 为 `present_unchanged` |
| `test_index_works_without_journal` | 不接 journal 时执行归属仍不丢（`execution_id = goal.id`） |
| `test_index_failure_does_not_fail_execution` | 索引写抛异常 → 执行仍成功，journal 产物照常 |
| `test_unattributed_agent_is_not_written_to_index` | 归属不成立的 agent 不进索引（与 `COMPLETED_UNATTRIBUTED` 同口径） |
| `test_agent_runtime_passes_index_through` | `AgentRuntime` 只透传：给了就挂到引擎，不给仍是 `None` |

---

## 8. 已知边界与未验证

1. **未验证**：网关 `_ensure_runtime` 的真实启动路径（需要起服务）尚未跑过；模块导入与参数透传已由测试覆盖。
2. **未验证**：`_journal_event` 是否返回 `seq`（实测不返回），因此直连记录的 `journal_seq` 为 `NULL`。
3. 一个任务写多文件仍只记一个（执行内核契约所致，见 6.1c）。
4. 跨工作区根迁移后，旧记录不会自动迁移；`verify()` 会按旧 `abs_path` 报告 `missing`。
5. 索引库本身不做加密/压缩；若含敏感路径，需按现有备份/权限策略处理。
6. 未接 UI：控制台当前看不到这份索引（见 6.4）。
