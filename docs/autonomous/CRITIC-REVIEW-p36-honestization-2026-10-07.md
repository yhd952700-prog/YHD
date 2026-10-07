# Chief Systems Critic — 独立对抗审查：p36「诚实化」工作线

| | |
|---|---|
| **Author** | chief-systems-critic (辟明之) — Adversarial Review Authority |
| **Date** | 2026-10-07 |
| **Scope** | silent-success sweep · readiness 防假绿 · registry 运行产物治理 · claim 更正寄存器 |
| **Also** | 独立复核主理人两条声明（HC-01 证据已验证 / 8 WIP 已归属） |
| **Method** | 全部结论引用非模型产物：`git` / `sha256sum` / `pytest` / `importlib.metadata` / 工具原始输出 |

> 本记录不修改任何他人域的代码。它是审查结论，不是修复。

---

## 0. 独立复核结论（先于批判）

### 声明 A —「HC-01 冻结证据已当场验证（4 份吻合）」→ **VERIFIED（附一处措辞过读）**
自算 SHA-256（`sha256sum`，证据目录 `D:\WorkBuddyFiles\LIUHAO-Phase3.6-Evidence\`）：

| 文件 | 实算 sha256 | 与 memory 记录前缀 |
|---|---|---|
| `forensics-2026-09-24/audit_store.db` | `c1885668982cc53fa8feb0c4ccb4e3d6e4bd4392792ddbcd722d8a6e6859f09d` | `c1885668…f09d` ✅ |
| `forensics-2026-09-27-live-replaced/audit_store.db` | `277cad5a7fbeeda26cabf30af1921ce2d5cd362f2f4197426b318b1106da541a` | `277cad5a…541a` ✅ |
| `forensic-rca-snapshot-20260924T212733Z/snapshot.audit_store.db` | `b6e27d0556fb0fb4cf9c9c930117b96c3b59dbd9d2cff1cfa2aece402f0240e3` | `b6e27d05…40e3` ✅ |
| `forensics-2026-09-27-sep24-snapshot/audit_store.db` | `c1885668…f09d`（= 第 1 行） | 未单列 |

live DB（仓根 `audit_store.db`）：`sha256=881bfc022d83da851633fba57400683c3179a4d40fec75faf4779cc8c300b079`；
`PRAGMA integrity_check` → `ok`；`audit_events` = **40482** 行；`chain_state=(1,40482,e68eba27…,df3e50ab…)`。
**= memory 的「live DB 40,482 rows, integrity ok」完全吻合。**

**过读点（低）**：memory 原文写「**4 份** SHA-256 … 全部吻合」，但只**列出 3 条 hash**；第 4 个文件是第 1 份的 byte-identical 副本。
即「4 份文件、3 个互异 hash」。措辞把文件数当成 hash 数报——**正是本工作线自己在 claim 台账里反复警告的「计数/标识符 ≠ 事实」病族**（见 CONTROL-CLAIM-LEDGER §2 更正登记表第 1 行「三个→两个」）。不影响证据完好结论。

### 声明 B —「8 个 WIP 已归属」→ **VERIFIED（快照时点）但 15 分钟内即失效**
快照当时 `git status --porcelain` 恰为 8 个 `M`，与 memory 三线归属逐字吻合（os-systems: `.gitignore`/`plugins/registry_index.json`/3 份 docs；engineering: `src/workflow/workflow.py`+test；quality-reliability: `scripts/verify_readiness.py`）。
**但**：复核期间 HEAD 由 `c438313e` → `c89b3313` → `fbb614ea`（3 个提交），WIP 由 8 个 `M` 变为 **12 `M` + 7 `??`**。归属声明不是假的，是**易腐的**——它描述的是一个正在被 18 个写者同时改写的目标（见批判 C1）。

### 其他独立复核（全部 CONFIRMED）
- registry 提交 `ca82ecb0` 的两个可证伪 hash：`registry_index.worktree-polluted.json = e6813afff447...`、`registry_index.HEAD.json = 8899600aafdf...` —— **加算吻合**；`git ls-files plugins/registry_index.json` = **0**（确已 `git rm --cached`），`.gitignore:140` 已忽略。盘上残留的 `memory:ghostplugin:1.0.0` 已出跟踪面。
- readiness 案例 8 三要点**全部为真**：仓 `.venv`（Py3.12.14）`importlib.metadata`→`PackageNotFoundError`（无 pytest-timeout）；受管 `envs/default`（Py3.13.14）→ `pytest-timeout 2.4.0`；`pyproject.toml:115` 声明 `pytest-timeout>=2.3.0`。
- WorkflowEngine 诚实化：`pytest tests/workflow/ -q`（仓 `.venv`）→ **12 passed**（复现）。新断言对旧实现逐条为 RED（据 `git show c89b3313` diff 判定）。
- 控制声明守卫：`.venv/Scripts/python.exe scripts/lint_control_claims.py --all` → `PASS`（130 文件 / 83 claims / 22 undispositioned）。

**结论：这两条主理人声明经我独立重算，成立。** 未发现主理人虚报。

---

## 1. 批判（按杀伤力排序）

### C1 —【根本问题】共享工作树并发＝无隔离的单点故障；一切「诚实化」都钉在移动靶上
**是什么**：18 位专家在同一工作树并发写。复核期间实测：HEAD 15 分钟内前进 3 个提交，WIP 集合完全翻新（8 → 19 文件）。
**为何致命**：
- 任何「X 已通过测试」的 attestation，其真值只对**它运行那一刻的树**成立；到我复核时那棵树已不存在。「独立复核」在共享树上**结构上不可复现**。
- `git add <单文件>` 是 scoped，但取的是**该文件在 add 时刻的磁盘内容**——两个专家并发改同一文件时，先提交者会静默带走对方的半成品。
- 「同一时刻只允许一个 pytest」是**纪律，不是强制**（台账 §5 自认「当前没有任何流程强制」）。两进程共享 scratch/sqlite ⇒ 幻影失败，正是 readiness 里那句注释承认的现象。
**可证伪的缓解**：每位专家 `git worktree add`（真隔离）或显式锁+串行协议；且每条 test attestation **必须**附树指纹 `git rev-parse HEAD` + `git diff --stat` 的 hash。**逆转条件**：当 attestation 携带的指纹在审计时仍可复现→风险关闭。

### C2 —【模式问题】「honest limitation」再包装：WorkflowEngine 是幽灵面，未接线也未删除
**是什么**：`WorkflowEngine` 在 `src/` **无任何生产消费者**（`grep` 仅在 `src/workflow/__init__.py` 的 export 与 tests 出现）。诚实化把它改名为「PLACEHOLDER … for the automation demo」、返回 `success=False`，但**保留并从包命名空间继续 export**。
**为何致命**：本工作线自己的原则（台账 §1 第 3 步、案例 3）明写载体动作三选一＝**接线 / 改写 / 撤回**；「修产品，不修门」（`CONTROL-CLAIM-LEDGER` 开头）。这里**既没接线也没删除**，只把死幽灵贴了「诚实的 limitation」标签。这正是团队负责人点名要查的「把能力缺口重述为 honest limitation 完成洗白」——**是轻度实例**（标签是响的，不是静默的），但结构上就是这个模式。
**可证伪的缓解**：删除 `WorkflowEngine` 及其 export，或接一个真 executor；加一条测试断言 `import src.workflow.WorkflowEngine` 要么做真事、要么不存在。

### C3 —【同类未闭】防假绿自身残留一个假绿向量：全 skip 的套件 = PASS
**是什么**：`scripts/verify_readiness.py:249` PASS 判据 = `tests > 0 and failures == 0 and errors == 0`——**`skipped` 不参与判据**。单测把这一点固化：`tests/scripts/test_readiness_shim_and_junit.py:52` 的 `_CLEAN_JUNIT` = `tests="3" skipped="1"`，:135 断言 `status == PASS`。
**为何致命**：一个 `tests=N, skipped=N, 0 failed` 的套件（例如所需服务全缺、守卫全跳）→ **PASS**。「0 failed 因为什么都没跑」恰是本工作线要杀的假绿——它闭了「被杀进程读成 pass」与「shim rc 读成 fail」两条，**留下了第三条**。
**可证伪的缓解**：PASS 判据补 `tests - skipped > 0`（或最小已执行阈值）；补一条 `tests == skipped → NOT VERIFIED` 的单测。**逆转条件**：该测试存在且判据含已执行数→关闭。

### C4 —【表演风险】PASS 不带树指纹；且一处 attestation 计时不可复现
**是什么**：整条诚实化线的提交**均未记录测试时的树指纹**（HEAD+dirty hash），故「tests passed」无法绑回一个可复现状态（并入 C1）。另：`c89b3313` 提交信息称「tests/workflow/ -> 12 passed in **1.13s**」，我实测 **12 passed in 21.71s**（仓 `.venv`，冷启）。
**为何致命**：单独看 1.13s 无害；但它是「attestation 与其所述不完全一致」的实例——正是 review theater 的最小样本。
**可证伪的缓解**：提交信息附树指纹+实测耗时原文；不要手写耗时。

### C5 —【认识论天花板，非缺陷】claim 更正寄存器是「人判的 ratchet」，无强制
**是什么**：守卫 `PASS` 只挡**新的**静默声明；台账里的「真」是人判的（台账 §5 自认：「当前没有任何流程强制一次更正发生…这就是诚实的底线」；「复核者≠作者 是约定，无工具强制」）。计数已漂移（台账写 81 claims/3 tagged；live 实测 83/4），因语料在动。
**评断**：这是**诚实披露的已知上限**，不是掩盖。**并且台账含真实自纠**（其作者把「三个解释器」改为「两个」，登记表第 1 行）——这在自证循环里是稀有的可信信号。仅标注：天花板是真的，别把「有 ratchet」读成「声明已受控」。

### C6 —【我自己的天花板，诚实标注】同源模型审查同源模型
我是与被审 18 位专家**同一底层模型**的实例。「伪多样性」「AI 审 AI 的自我肯定循环」这些结构性谬误，**我无法从产物证伪**——我只能确认它们**可能**存在（N 个相关实例被当成 N 份独立验证）。任何声称「独立审查已覆盖」的结论，都应附带这一不确定性。此点由我主动交出，不待他人指出。

---

## 2. 对组织/主理人的压力测试结论
1. **主理人未虚报**：两条声明（HC-01 证据、WIP 归属）经独立重算成立；registry、readiness 案例 8 的可证伪 claim 也成立。**诚实化有真材实料**：安全删除 shim 陷阱被我实测复现（`pytest … -q` 全绿 25 点，`RC=1`，shim `count:83 threshold:50`）——readiness 硬化针对的是**真实现象**，不是假想。
2. **最大的系统性风险不是「不诚实」，是「移动靶 + 无隔离」（C1）**。只要树在并发漂移，任何 attestation（包括我这份）都无法被第三方在同一状态复现——这会让「独立验证」这一整条防线**结构性退化**。
3. **诚实化的方法论是可信的**（接线/改写/撤回三分、ratchet、防回退、复核者≠作者），**但它自己有未闭向量（C3）和未执行动作（C2）**——它诚实，但未完成。

## 3. 升级项（需人类决策 / 强于本席权限）
- **U1（组织级）**：是否强制每位专家使用 `git worktree` 隔离，或引入单一写者串行协议？（C1 的唯一根治）
- **U2**：`WorkflowEngine` 等无消费者的幽灵面——**接线还是删除**？请为「honest stub」立一条组织规则，避免同类再包装（C2）。
- **U3**：为所有 readiness attestation 引入**树指纹**字段为必填（C1/C4 的机器化缓解）。

---
*本席保留对本记录的独立复核要求；任何「已覆盖」读法都不成立，除非 C1 的隔离与 C3 的判据被实证关闭。*
