# ORPHAN-AUDIT — 零调用方模块审计

> 状态：已完成（2026-09-14）。产出 = `scripts/verify_no_undocumented_orphans.py`
> ＋ `orphan-registry.yaml`（决策登记册）＋ CI 门禁 ＋ 负向对照测试。
> 纪律：**先实测再判断**、**只审计不改动代码**、**区分"已验证"与"未验证"**。

## 1. 为什么要做这件事

本仓库已确认的每一个"静默缺陷"都长成同一个形状：**代码写好了，但没有任何调用方**。

| 模块 | 曾声称 | 实际 |
|---|---|---|
| `src/distribution/msg_bus.py` | Redis 发布/订阅 | `publish()` 返回合法 ID 但**丢消息**；`consume()` 恒返回 `[]` |
| `src/performance/cache.py` `MultiTierCache` | memory→redis→disk 三层 | 只有 memory；`put(tier=REDIS)` **静默什么都不做** |
| 同上 `BatchCache` | 批量读写 | `put_batch` **每次调用都死锁**（非重入锁） |
| `src/kernels/evaluation` `Evaluator` | 自动评估 | 无调用方（阶段 3 才接上） |
| `src/observability/metrics.py` `generate_metrics()` | Prometheus 导出 | 全仓库**零调用点**（阶段 7a 才接上） |
| `src/model_gateway/*` | 模型路由 | 离线，主链路只用 `providers.get_provider()` |

共同点不是"代码有 bug"，而是**没有人调用它**——所以任何测试都碰不到，缺陷可以在里面活很久。
静态看这不算错（可能是库、可能在等接线），真正的问题是**没人做过决定**。

本审计把这件事变成一个机械问题：

> 每一个零引用的 `src/` 模块，都必须在 `orphan-registry.yaml` 里**写明理由**。

## 2. 方法（含诚实边界）

扫描逻辑（`scripts/verify_no_undocumented_orphans.py`）：

1. 枚举 `src/**/*.py` 的每一个模块（排除 `__pycache__`/`.venv` 等）。
2. 在 `src/ tests/ scripts/ apps/ LiuHao-O/ configs/ alembic/` 的
   `.py/.yml/.yaml/.json/.toml/.cfg/.ini` 语料里找三类引用：
   **完整点分路径**、**`from/import` 短名**、**引号包裹的点分路径**（动态加载）。
3. 命中在生产语料 → 不是孤儿；只被 `tests/` 命中 → *test-only reachable*（**报告但不拦截**）；全无命中 → 孤儿。
4. 孤儿必须出现在 `orphan-registry.yaml` 的 `acknowledged` 列表且**带 `reason`**，否则退出码 1；
   登记册自身畸形（缺 reason / 结构非法）退出码 2。

**诚实的局限**：可达性判断是**文本启发式**，不是真正的 import 图 —— 它可能漏掉
异常动态加载、也可能被过于通用的短名骗过。**这正是登记册存在的理由**：
脚本只负责强制"人做出决定"，不假装自己全知。

**本审计的防伪措施**（避免重蹈 `ARCHITECTURE-AUDIT.md` 的覆辙——那份把
docstring 措辞与同名方法当成了证据）：

- 对每个候选**逐一验证动态可达性**：既查完整点分路径，也查**引号字符串**与
  `importlib.import_module(...)` / `getattr(...)` 的邻近语境；字符串引用的一律**不判孤儿**。
- 对"疑似重复实现"的，**先确认 live 对照物真的被引用、真的可用**，再下结论。
- 写了 **9 项负向对照测试**（`tests/test_orphan_guardrail.py`）证明探测器**真的会失败**，
  而不只是结构上"能失败"。

## 3. 结果：18 / 216 个模块零引用

分类（`classification` 字段）与实测依据：

### 3.1 `superseded-duplicate` — 有 live 对照物的死副本（**最值得关注**）

> ✅ **2026-09-15 已处置：四个模块全部删除**（boss 授权"1 可以"）。
> 处置前做了**逐字节校验**的备份（`D:/WorkBuddyFiles/orphan-backup-20260915/`，`cmp` 全 OK），
> 文件经**回收站**删除（不用 `rm`），删除后再跑守卫确认它们已从零引用清单消失。
> ⚠️ 同步做了**第四步**：把 `orphan-registry.yaml` 里对应的四条 `acknowledged` 条目**移除** ——
> 不删的话守卫会报 `acknowledged but no longer an orphan`，登记册就开始腐烂，
> 而"登记册腐烂"正是这个文件存在要防的失败。
> 下表保留为**决策依据的历史记录**，不再是当前状态。

| 模块 | 行数 | 实测依据 |
|---|---|---|
| `src.security.vault_client` | 125 | live = `src/integrations/vault/`（`client.py` + `secret_manager.py`，其 `import hvac` **有守卫**）。此副本在**顶层**无条件 `import hvac`；实测 `hvac` 已安装，故它不会报错，只是作为**第二套 Vault 权威**静静躺着 |
| `src.security.vault_transit` | 322 | transit 加解密/密钥管理 live 在 `src/integrations/vault/client.py:249-271` |
| `src.security.rbac_abac` | 572 | **零引用**；且其同名测试 `tests/governance/test_rbac_abac.py` 实际 import 的是 `src.security.rbac`（`RBACManager`）——**连以它命名的测试都没测它** |
| `src.storage.repository` | 159 | 第 **3** 份泛型 `Repository`；live = `src/integrations/storage.py:32`（被 `tests/test_orm_storage.py` 引用），另一份在 `src/storage/backends.py:430` |

> 这正是项目宪法 P0-4 记的"**两套平行访问控制权威**"同型病：
> 权威分裂后，改一套不影响另一套，而没人知道哪套在生效。

### 3.2 `endpoint-not-mounted` — 端点定义了但从未挂载

| 模块 | 行数 | 实测依据 |
|---|---|---|
| `src.feedback.feedback_api` | 77 | 声明 `router = APIRouter(prefix="/feedback")`，但**无任何模块 include 它**（已验证 `src/feedback` 包外零引用、`src/gateway` 不引用）。即：**该反馈提交端点在运行时并不存在** |

### 3.3 `library-staged` — 可用的库代码，暂无调用方（不是缺陷）

`src.distribution.election`（258 行，Leader 选举）、`src.distribution.lock`（233 行）、
`src.gateway.validation`（8 个校验类）、`src.logging_config`、`src.observability.logging_utils`
（两个**都未接线**的日志模块）、`src.observability.alerts`、`src.pipelines.etl`、
`src.pipelines.parser`、`src.plugins.marketplace`、`src.plugins.plugin_manager`。

> 这些是**能力储备**，不是谎报：它们不在任何路径上声称"已生效"。登记理由即可。

### 3.4 `tooling` / `infra-artifact` / `legacy-not-wired`

- `src.performance.benchmarks`（**tooling**）：人工手动跑的基准脚本，本就不该被 import。
  注意它消费 `LRUCache`，所以阶段 8 的缓存改动必须保持它可跑。
- `src.infra.infra_charts`（**infra-artifact**）：生成 Helm chart 文本，非运行时代码。
- `src.adapters.observability.opentelemetry_adapter`（**legacy-not-wired**）：
  模块自己的 docstring 已声明 `[LEGACY / NOT WIRED]`。

## 4. 发现的一个关于**本会话自身**的诚实问题

`src.ai.agent_runtime` 与 `src.ai.eval_report`（阶段 3 / 阶段 6 的产物）目前是
***test-only reachable*** —— 即只有测试在引用，**没有生产调用方**。
脚本把这一类**报告但不拦截**（它是信号，不是判决）。如实记下：
这两个模块的定位是"**能力就绪、待接线**"，与上面的 `library-staged` 同类，
而非"已在生产生效"。

## 5. 我**没有**做的事

- **没有删除任何模块**。审计 ≠ 清理。零调用方可能是库、可能是被 docs/路线图
  当入口引用的组件（Phase 7b 的 `msg_bus` 就是这种），删掉可能删掉别人的接线目标。
  `verify_no_undocumented_orphans.py` 的 docstring 明确写了这条。
- **没有改任何 `src/` 运行时代码**。本片只增加：1 个脚本、1 个登记册、1 份文档、
  1 个测试、2 处 CI 配置。

## 6. 门禁如何防止复发

- **CI**：`.github/workflows/ci.yml` 的 `guardrails` 作业新增一步
  `python scripts/verify_no_undocumented_orphans.py`。今后**新增**一个零引用模块
  而未登记理由 ⇒ **CI 红**。
- **登记册防腐**：模块重新被引用后，脚本会把它作为 *stale* 输出警告
  （要求移除登记项），所以登记册不会腐烂成一份过时的借口清单。
- **元护栏**：既有的 `tests/test_guardrail_scripts.py` 同时强制本脚本
  ①bootstrap `sys.path`、②存在非零退出路径、③被 workflow 引用；
  新增的 `tests/test_orphan_guardrail.py` 用 9 项负向对照证明**它真的会拦**。

复现命令：

```bash
python scripts/verify_no_undocumented_orphans.py --list   # 只报告，恒退出 0
python scripts/verify_no_undocumented_orphans.py          # 门禁模式
```
