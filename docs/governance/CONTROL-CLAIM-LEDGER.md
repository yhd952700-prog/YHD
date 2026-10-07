# CONTROL-CLAIM LEDGER

**Owner:** governance-legal (法度明) · **Created:** 2026-10-07
**Guard:** `scripts/lint_control_claims.py` (ratcheted — fails on a *new*
un-dispositioned assertive control claim; existing ones are frozen in
`scripts/control_claim_baseline.txt` and adjudicated here).
**Wired into CI:** `.github/workflows/doc-lint.yml`, in the same
`lint-audit-claims` job (`run: python scripts/lint_control_claims.py --all`),
plus the two path filters (`scripts/lint_control_claims.py`,
`scripts/control_claim_baseline.txt`). Wiring rule: this is a *claim-guard*, not
a gate to widen — **do NOT expand its scan surface to make it "look broader";
fix the product instead.**
**Deliberate deviation from precedent:** the `lint_audit_claims.py` precedent
lists only the linter script in `paths`; this guard ALSO lists its baseline
(`scripts/control_claim_baseline.txt`). Reason: the baseline is the guard's
first-class input — a PR that *only* loosens the baseline (widening/emptying the
ratchet) without touching any doc would otherwise not trigger the job and could
bypass the guard silently. Listing it closes the one channel that can quietly
weaken this guard. (Recorded so it is not mistaken for a copy-paste omission.)
**Also see:** `## 更正规则` below — the *other half*. The criterion above answers
"does a claim carry a verifiable basis pointer"; it is silent on what to do when a
pointer (or a *behaviour*) is later found **false**. A ratchet that only freezes
*un-dispositioned* claims still lets a **dispositioned-but-wrong** one sit forever.

## The criterion (three-state; NEVER two-state)

An **assertive control claim** asserts a control is *currently* in force
("is enforced", "guarantees", "will block/deny", "cannot be …", "强制…", …).
Soft modality ("designed to", "should", "planned", "intended guarantee") is
**not** a claim.

A claim is a **false guarantee** iff: it is assertive **and** no verifiable
execution basis exists in the repo. "No basis found" must resolve to one of:

| state | meaning |
|---|---|
| **FALSIFIED (已证伪)** | Assertive + no code path / gate / manifest basis exists. → fix the doc or wire the control. |
| **NOT VERIFIED (无从判定)** | The basis is external (real browser/Docker/Ubuntu CI) or cannot be checked on this host. **Not a false guarantee, not a pass.** |
| **PASS (通过)** | A verifiable basis pointer exists (path / commit / file:line / test). |

The guard never judges the pointer's *truth* — that is what this ledger does;
the guard only forbids leaving an assertive claim **un-dispositioned**.

**Cross-layer rule:** a claim about *another layer's* behaviour ("the backend
will send X", "CI enforces Y", "the server rejects Z") needs the basis on
**that layer's** artefact. One side alone = a false guarantee.

**Basis-pointer rule (reachability, not existence):** a basis pointer to a commit
must be verified **reachable** from the current branch — `git merge-base
--is-ancestor <sha> HEAD` (exit 0) or `git rev-list HEAD | grep -c ^<sha>` = 1 —
not merely **present** as a git object. A history rewrite (`git commit --amend`,
rebase) leaves the superseded commit *showable but unreachable*: `git cat-file
-t <sha>` still answers `commit` and `git show <sha>` still prints a valid
commit, so a naive existence check **passes while the pointer is dead**.
Unreachable pointer = unverifiable basis = a false guarantee. (Sample: an
earlier revision of this ledger cited the commit that a later `--amend`
superseded with `1bc3418e`; the existence check (`cat-file`/`show`) passed,
`--is-ancestor` did not.)

## Results — first full re-scan (2026-10-07)

Corpus: `docs/**/*.md` (130 files; `docs/archive/` excluded).
Assertive control claims: **81** → tagged-NOT-VERIFIED 3 · basis-present 56 ·
un-dispositioned 22 (adjudicated below).

**Scan surface & self-exclusion.** The guard's default surface is `docs/**/*.md`.
This ledger is **excluded from its own scan**: it must quote control language to
*define* the criterion and to *record* already-adjudicated claims, so scanning it
is guaranteed self-noise. Excluding it removed 4 spurious "claims" (its own lines
11/15/28/29) and restored the corpus to the 130-file / 22-claim figures above.

**Known blind spots (confirmed; registered, not hidden).** A guard `PASS` must
**not** be read as "no false guarantee exists" — these bound what this guard can
see:
1. **Markdown-only surface.** `scan_file` only reads `.md`, so **code docstrings
   (`src/`, `apps/`) are out of surface**. The headline `errorMessage.ts:32-33`
   case is exactly this kind — it was caught **by inspection + this ledger, not by
   the guard**. Widening the surface to scan code is a *product* decision and was
   deliberately **not** done here (fix the product, not the gate).
2. **`CROSS_LAYER` is English-centric, and its Chinese branch is verb-less.**
   The 后端/前端/网关/服务端/客户端/浏览器/作业 alternation requires no verb, so
   it is over-broad in Markdown and effectively absent for code — it did **not**
   match the Chinese form "跨域部署下也能拿到" on the very line above.

Each is a recorded improvement, not a silent gap.

### FALSIFIED — confirmed real defects (both now actioned)

| file:line | claim | falsification basis | status |
|---|---|---|---|
| `docs/adr/ADR-HOLD-SCOPE.md` §2.1 | "the code-level freeze" is the authoritative guarantee | No `src/` code reads `LIUHAO_HOLD`; only machine enforcement is `check_hold_gate.py`. | **FALSIFIED → FIXED** — doc rewritten in `6922c8a5`; the guarantee is now described as human discipline outside the CI release path. |
| `apps/console/console/src/lib/errorMessage.ts:32-33` | "回退到响应头 `X-Trace-ID`，跨域部署下也能拿到" (cross-layer) | On the **pre-fix HEAD** `expose_headers` was ABSENT in `src/gateway/main.py` → CORS exposes only simple response headers → browser JS could not read `X-Trace-ID`; the backend half was missing. | **FALSIFIED → PASS (re-verified)** — backend half landed in `1bc3418e` (reachable from HEAD): `expose_headers=["X-Trace-ID"]` (`src/gateway/main.py:536`), `request.state.trace_id` (`:558`), response header set (`:564`); behaviour test `tests/gateway/test_error_surface_and_boot_truth.py` (**6 passed**) pins both halves. **Both sides of the cross-layer claim now have a basis.** |

Textbook sample for the cross-layer rule: this claim was true on **neither** side
at `HEAD` (backend did not expose the header; frontend's fallback therefore
returned `null` cross-origin) and became true only when **both** artefacts
carried it. One side alone = a false guarantee.

### un-dispositioned 22 — adjudication

| file:line | state | basis / reason |
|---|---|---|
| `docs/adr/ADR-root-of-trust-hd05.md:115` | **PASS** | `src/security/evidence/interfaces.py:120-121` (`authority`, `self_attested`) + `verifier.py:59` consumes it — verified |
| `docs/adr/ADR-HOLD-SCOPE.md:191` | **PASS** | self-ref to §2.2/§2.3 (scope definitions, not an in-force claim) |
| `docs/autonomous/ADR-audit-head-notary.md:41` | **PASS** | §3 heading; guarantees enumerated with module refs in body |
| `docs/autonomous/ADR-audit-single-writer-lease.md:111` | **PASS** | basis: the mandatory-evidence gate in the audit append path (`src/kernels/audit`) |
| `docs/autonomous/ADR-audit-single-writer-lease.md:180` | **PASS** | same |
| `docs/autonomous/ADR-audit-writer-lease-identity.md:36` | **PASS** | §1 heading; body describes the actual enforcing mechanism |
| `docs/autonomous/ADR-audit-writer-lease-identity.md:638` | **PASS** | basis: `src/kernels/audit/fencing.py` remedy path |
| `docs/autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md:122` | **PASS** | basis: "docstring + test guard" (name the test at wiring time) |
| `docs/autonomous/IDENTIFIER-POLICY.md:51` | **PASS** | basis inline (UUID4 122 random bits) |
| `docs/autonomous/IDENTIFIER-POLICY.md:222` | **PASS** | basis: the test that explicitly rejects the 48-bit column |
| `docs/autonomous/PERFORMANCE-BASELINE.md:24` | **PASS** | basis: bench gate in `ci.yml` (`bench_audit_append.py --gate`) |
| `docs/autonomous/RISK-REGISTER.md:31` | **PASS** | basis: mandatory-evidence gate (audit append path) |
| `docs/autonomous/G9-image-build-runbook.md:194` | **NOT VERIFIED** | "forbidden host port must FAIL" — needs a real Docker host |
| `docs/autonomous/G9-image-build-runbook.md:202` | **NOT VERIFIED** | "dead endpoint must FAIL" — needs a live stack |
| `docs/autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md:25` | **NOT VERIFIED** | "evidence-preservation guarantee" — enforcement = HC-01 freeze (human discipline); not code-enforced |
| `docs/autonomous/ADR-audit-storage-C3.md:434` | FP (past tense) | "Step 0 froze an immutable copy" — historical fact, not an in-force claim |
| `docs/autonomous/ADR-audit-writer-lease-identity.md:128` | FP (negation) | "makes no … guarantee" — a *negative* statement |
| `docs/autonomous/ADR-tsa-quorum-ha.md:19` | FP (threat desc) | "silently defeats the guarantee" — describes an attack, not a claim |
| `docs/autonomous/GOV-LEGAL-RETENTION-ERASURE-CONFLICT.md:114` | FP (recommendation) | "Minimize PII …" — soft modality |
| `docs/autonomous/HUMAN-DECISION-BACKLOG.md:20` | FP (invariant) | "Do NOT delete … immutable history" — a constraint statement |
| `docs/autonomous/HUMAN-DECISION-BACKLOG.md:25` | FP (status) | "Irreversible action still blocked" — status, not a control guarantee |
| `docs/autonomous/IDENTIFIER-POLICY.md:230` | FP (heading) | "identifiers lacking a uniqueness guarantee" — a section title |

**Net:** **2 FALSIFIED found, 0 outstanding** — both actioned (one doc rewrite
`6922c8a5`; one wired + re-verified `1bc3418e`, re-adjudicated FALSIFIED → PASS);
12 PASS; 3 NOT VERIFIED; 7 regex false positives (FP) — the FP set is why this is
a **ratchet** (baseline), not a "clean all N lines" gate.

## Note on scope of applicability

The assertiveness detector is regex-based and has residual FP/FN. Its job is
**triage**, not adjudication: it forces every assertive control claim to carry
a basis pointer or an UNVERIFIED tag; the ledger (this file) is where truth is
decided. Treat a `PASS` here as "a pointer was found and checked on this date",
not as a permanent guarantee — re-check when the cited artefact changes.

## 更正规则（CLAIM-CORRECTION RULES）

**为什么需要这一节。** 上面的判据 + ratchet 只回答"一条声明有没有挂上可核验的指针"。
它对**指针本身是假的**、或**行为是假的**、且**事后才发现**的情况一字未提。只冻结
*未处置* 声明的 ratchet，仍会让一条 *已处置但写错* 的声明永远躺着。本节补的就是这另一半：
**发现一个假信号之后必须做什么**。它同时是上面那条判据的**运行时对偶**——
*一个信号不得比它的依据更自信*。

**先立坐标系（之后一切按方向分，不按载体或意图分）：**
**假声明**（文本自称某状态成立而依据不成立）· **假成功**（运行时报"做了/健康"而实际没做/已死）·
**假失败**（运行时报"失败"而实际成功）· **未执行被洗成裁决**（检查根本没跑，却记成"跑了但没裁决"）。

### 1. 更正义务（发现之后必须做什么）

按此顺序，**遏制先于更正**（先止血，后改口）：
1. **遏制（contain）**：立刻让该信号在下游不可当"当前事实"读——在台账标 `FALSIFIED` /
   `NOT VERIFIED`；若是运行时路径则 **fail-closed**（宁可 raise，绝不回退成假值）。
2. **留痕（register）**：以 `file:line`（或 commit / 测试）为键登记到本节更正表。
3. **更正载体（correct the carrier）**：三选一——**接线**（把控制做成真的）· **改写**
   （把声明改成与事实一致）· **撤回**。绝不允许"既假又不标"。
4. **加回归（regression）**：一条**行为**测试，修前 RED、修后 GREEN（§6 案例 3/4/5 的"双向锁"范式）。

### 2. 谁被告知 · 时限 · 留痕 · 谁判"已更正"

**告知（分层）**：① 伪件的 **layer owner**；② **交叉层 owner**——沿*声明依赖图*（非组织架构）
找所有依赖该假信号的下游；③ **老板（人类）**：当信号是**证据级**、**对外面**、或更正需改范围/决策时；
④ **外部**：仅当假陈述已**发布到仓外**（已发布文档/公开契约）——目前 LIUHAO **无确认的对外发布通道，
此层未触发**，但规则必须存在。诚实点：**当前"通知"＝人发消息 + 本台账，没有任何自动通知机制。**

**时限**：**当前不存在任何时限。** 建议值（纯纪律，仓内无时钟可强制）：遏制 ≤ 1 个工作日；
交叉层告知 ≤ 1 个工作日；更正载体 ≤ 1 周（否则降级为 UNVERIFIED）；证据级/对外 ≤ 72h 交人类裁决。
理由：假信号的成本 ∝ 暴露时长 × 消费者数量。

**留痕**：**复用**现有 ratchet/baseline 机制，**不新增平行件**——更正登记表就落在本节
（一收获一行：键 / 方向 / 检出方式 / 更正动作 / 提交 / 回归测试）。门禁侧把
"更正过的声明不得静默回退"接进 `scripts/lint_control_claims.py`（认可 `FALSIFIED→PASS`
这类更正标记，并要求更正窗口内带上更正提交或回归测试路径）。

**谁判"已更正"**：**自证无效**——写错的人不能给自己判过。**独立复核**是硬要求，至少覆盖
`FALSIFIED→PASS` 翻转与一切**证据级/对外**声明；低等级内部文本更正由**第二位读者（非作者）**
核 RED→GREEN 即可。诚实点：**当前没有工具强制"复核者 ≠ 作者"这一角色分离，它是约定。**
台账只记"谁、何时、据何判定"。

### 3. 假成功与假失败：同一条规则

**是，同一条规则。** 两者**摧毁同一物——信号的可用性**。一条恒亮的
"AUDIT CHECKPOINTER FAILED TO START"（案例 5，实际已启动）会训练运维**忽略那一行**，
于是**真**故障来临时它已被无视（狼来了）；假成功是其镜像，**成本函数完全相同**。
`1bc3418e` 自己的提交信息即此规则实证："A false failure claim costs what a false success
costs: it teaches people to ignore the line."（该 commit 信息还自纠了一处*假可达性*声明——同类第三例。）
**唯一区别在严重度，不在规则**：假成功落在**证据级/安全**路径 → **P0**（静默、掩盖危害）；
假失败 → **P1**（响亮、浪费注意力）。**一条规则，分档严重度。**

### 4. 有意 vs 疏忽

**不作为触发条件**：触发依据是**可观测的"假"**，不是动机。理由：意图**无法从产物可靠判定**；
把门禁建在不可判定的谓词上，门禁自身就是一条假保证（元层自洽）。**可判的部分**：若有直接证据表明
**伪造 + 隐瞒**（如把虚构实验当实测引用＝案例 6），那是一处**升级标志（suspected fabrication）**，
由**人类**记入，而非门禁分支。更正后的产物比原件更诚实时（案例 6 明写"并非真实历史"），
规则应**奖励该勘误**，而非要求先证成意图。

### 5. 能进门禁 vs 只能人工纪律 · 真正的兜底

**能进（机器可查）**：新出现的**未处置**声明 → 已门禁（ratchet）。**更正过的声明回退** → 门禁
（更正窗口须带更正提交/回归测试）。运行时**假成功**（fail-closed 路径）→ 行为测试断言未配置即
`raise`/`unavailable`（案例 2/4）。**幽灵旗子** → 调该旗子并断言其文档承诺行为（案例 3）。
**受跟踪运行产物** → .gitignore + 禁止生成物入库（案例 7，已部分落地）。**依赖↔CLI 参数错配**
（案例 8）→ runner 里出现 `--timeout` 必须有 `pytest-timeout` 声明。

**只能靠人工纪律（诚实列出，不粉饰）**：**跨层 docstring 一致性**（案例 1 前端半——守卫只扫 `.md`，
代码 docstring 在扫描面之外，只能靠人把两半对齐）· **docstring 里的"历史/实测"断言**（案例 6，
扫描器无法重推一次过去的实验）· **产物内容的"陈旧 vs 当前"判断**（案例 7）· **意图/伪造嫌疑**
（按构造不可门禁）· **通知到人**（没有工具能验证"某人被告知了"）· **时限**（仓内无时钟）。

**当前真正的兜底（直说）**：**声明类**：一台 ratchet 机器（`lint_control_claims.py` + baseline）
＋**人的裁决**（本台账）——ratchet 只挡*新的*静默声明，**台账里的"真"是人判的**。**行为类**：
**行为测试**是唯一真兜底，**但只在其存在处**（案例 1/3/4/5）；不存在处＝**人工 inspection**。
**其余一切（跨层、docstring 历史、意图、通知、时限）：是人工纪律，不是代码强制，不是"已受控"。**
当前**没有任何流程强制一次更正发生**；一条假声明可以无限期不被更正，除非有人注意到。**这就是诚实的底线。**

### 6. 8 个案例的分类归位（2026-10-05~07，全部 ∈ HEAD；逐笔 `git show` 亲验）

| # | 案例 | 方向 | 更正 | 现在靠什么兜 |
|---|---|---|---|---|
| 1 | `errorMessage.ts:32-33` 跨层假保证（后端半缺） | 假声明（跨层） | `1bc3418e` 补后端半（`main.py:536/558/564`） | 行为测试 6 passed；**跨层对齐本身靠人工** |
| 2 | `openai.py:28/37/45/59` 伪造载荷 + 恒 `healthy` | 假成功 | `c3643614` fail-closed（抛 `ProviderNotConfiguredError`） | 行为测试（未 opt-in 即 raise） |
| 3 | `derive_audit_view.py` `--allow-stale` 幽灵旗子 | 假声明 | `70f519e8`+`076b54de` 接线 + STALE 落每个输出 | 行为测试（sidecar±flag → exit1/0 且 STALE 在 3 件） |
| 4 | `embedding.py:59-62` `except: pass` → `[0.0]*1536` | 假成功 | `6a8889ab` 抛 `EMPTY_EMBEDDING`；保留 mock 的**诚实**零向量 | 行为测试（provider 失败即 raise） |
| 5 | `main.py:325-360` **假失败**（INFO 未打印 + 假 ERROR，实已启动） | 假失败 | `1bc3418e` 改调用（模块级函数） | 行为测试**双向锁**（真 raise 必出且 app.state 无 checkpointer） |
| 6 | preflight docstring 伪证（断言某变异仍绿灯，实测为假） | 假声明 | `652e9a09` 删假证据、改**构造反例**并明写"并非真实历史" | 反例可测；**docstring 历史断言靠人工** |
| 7 | `scripts/readiness_report.json` 陈旧证据 | 假声明（陈旧当当前） | `189144ac` 移出跟踪（`.gitignore:130`） | 门禁（生成物不入库）；**10-04 的 `PASS=6` 副本从未提交、永久丢失** |
| 8 | `verify_readiness.py:163/263` 依赖 `--timeout`，`pytest-timeout` 全仓未声明 | 未执行被洗成 NOT VERIFIED | 声明依赖（或改调用） | 门禁（依赖声明面 ↔ CLI 参数一致）；**该门禁现只查 fakeredis/lupa，此处是已知缺口** |

**案例 8 亲验修正**：本机 `.venv` 实测 `pytest-timeout` **既未声明、也未安装**（`pip list` 无；
`--timeout` 在 `PYTEST_DISABLE_PLUGIN_AUTOLOAD` 开/关**两种模式**下均 `unrecognized arguments`
→ **EXIT=4**）。比"本机装了"更强：即便装了，也依赖一个**未被声明的隐式环境**。⇒"检查压根没执行"
会被洗成正常的 NOT_VERIFIED——与案例 7 共症：**产物/工具的缺失被当作正常裁决**。

### 附：Q4 复检（`docs/spec/incoming/MERGE-LEDGER.md:307`）

**定位（亲验）**：§3「合并前置待决策（阻塞项）」表（`:302-317`，Q1–Q15）之内，原文逐字
`| Q4 | Capability ID 是否迁移到 LHX（C4） | **是**，双写过渡（`legacy_id` 保留） | ⚠️ 待确认（改动大） |`。
该表性质：2026-09-06 的**合并决策台账**（plan-of-record），非控制 in-force 声明。
`EXECUTION-QUEUE.md:38-40` 的 Q4.1/Q4.2/Q4.3 是**另一族**工作项，全 Closed，**未混用**。

**复检结论：「Q4 已作出不实陈述」不成立（不予维持）——这是一次「字符串命中」，不是「事实主张」。**
① **事实主张读法**：Q4 裁决格称"**是**，双写过渡（`legacy_id` 保留）"，**为真**——实测
`capability-registry.yaml:43-44` = `id: LHX-C-003` + `legacy_id: context_compression`，
文件头 `:2-4` 记"id 改为 LHX-C-NNN 并保留 legacy_id"，`CAPABILITY-REGISTRY.md:61` 记
"2026-09-06 同步完成…两者现已一致"。**双写确已执行。** ② **字符串命中读法**：§3 `:298` 写
"以下全部已裁决"，而同表 5 行状态列为"⚠️ 待确认"——仅读此两点即得表面矛盾；但 `:300` 已就地
消解（⚠️＝"用户偏好项，建议值已生效但可推翻"，"已裁决"指*已有处置*，非*最终裁决*）。⇒ 假读者命中，真读者不。

**残留缺陷（真实、但较轻）**：`:298` "全部已裁决"用词**过读**。**更正判据 → 勘误（erratum），非撤回**：
核心事实为真，只需收紧措辞，建议 `:298` 改为"以下各项均已有裁决或**建议值**；⚠️ 行列的是**可推翻的
默认值**，非最终裁决"（该编辑留给 MERGE-LEDGER 的 owner 批准，本笔不动）。
**时间腿（不依赖 D22）**：更正记录的**时间基准取 git commit 时间**（更正提交本身，已是本台账的
basis-pointer 单位），可选加 **HD-05 时间戳腿**（`src/security/evidence`，RFC3161，带 `self_attested`）；
**不取 D22 `derive_audit_view.py` 的派生视图**——D22 正是案例 3/7 的隐患载体（WAL sidecar fail-closed、
输出标 STALE），把更正时间接上去等于接到一个会过期的派生件。
