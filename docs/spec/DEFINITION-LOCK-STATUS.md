# Definition Lock 状态声明与处置方案

> **状态**：⚠️ 本体文件缺失
> 声明日期：2026-09-06
> 影响：全库 16 处以上引用其条款，但依据不可查证

---

## 1. 问题

声称路径 `D:\LiuHao-AI-OS\LIUHAO-X-V3.0-DEFINITION-LOCK.md` **不存在**，
且经全量 git 历史检索**从未入过版本库**（2026-09-06 验证，见 §4 方案 A）。

但以下文件引用了它的具体条款：

| 引用来源 | 引用条款 | 用途 |
|---|---|---|
| `src/kernels/*/__init__.py`（12 个） | `§112` | 十二核心 Kernel 的实现依据 |
| `src/kernels/audit/__init__.py` | `§113` | Audit Kernel 实现依据 |
| `src/kernels/plugin/__init__.py` | `§115` | Plugin Registry 实现依据 |
| `implementation-status.UNRELIABLE.yaml` × 4 | `§5-§8` | identity / memory / security / audit 的补齐依据 |
| `capability-registry.yaml` × 12 | `§112` | 能力追溯 |
| `docs/architecture/kernels-interface.md` | `§96`、`§112`、`§122` | 并行 Workstream 规则、Kernel 接口、总条款数 |
| `docs/architecture/gap-analysis.md` | `§75-§83` | 当前代码与宪法要求的差距 |
| `docs/archive/PHASE_ACCEPTANCE_REPORTS.md` | `§75-§90`、`§83` | PM PRD / UIUX 生成依据 |
| `blueprint-requirements.yaml` | `§1-§122` | 需求索引 |

**后果**：任何"按 §112 已实现"的声明**目前无法验证**。这是验证链的根节点断裂。

---

## 2. 已还原的部分（从代码 docstring 反推）

好消息：每个 kernel 的模块 docstring 都写了依据条款，据此可还原 Kernel 层结构：

```text
§112  十二核心 Kernel（Definition Lock 的核心条款）
      identity · memory · context · capability · policy · execution
      resource · event · network · trust · evaluation · security
      佐证：security/__init__.py 明确写 "依据 DL:§112"

§113  Audit Kernel（第 13 个）
      佐证：audit/__init__.py 写 "依据 DL:§113"

§115  Plugin Registry（第 14 个）
      佐证：plugin/__init__.py 写 "依据 DL:§115"

校验：12 (§112) + 1 (§113) + 1 (§115) = 14 = src/kernels/ 实际目录数 ✅
```

**这证明代码是严格按 Definition Lock 实现的**，只是宪法文本丢失。

同时解释了历史文档的错误：
- `kernels-interface.md` 把 audit 算进十二 → 漏了 security
- MS:§6 把 security 算进十二 → 漏了 audit
- 两者都漏了 plugin（§115 位置靠后）

---

## 3. 条款推断映射表

在宪法恢复前，遇到以下引用时按此表理解：

| 条款号 | 推断内容 | 可信度 | 替代依据 |
|---|---|---|---|
| §1-§122 | Definition Lock 全文范围 | 高 | 无（待恢复） |
| §5-§8 | Identity / Memory / Security / Audit 四个 Kernel 的字段与接口要求 | 中 | 看 `src/kernels/{identity,memory,security,audit}/__init__.py` 的 docstring 与实现 |
| §75-§90 | PM PRD v3.0 的生成依据（122 条需求） | 中 | 看 `docs/product/PM-PRD-v3.0.md` |
| §83 | Designer UIUX v3.0 的生成依据 | 中 | 看 `docs/product/Designer-UIUX-v3.0.md` |
| §96 | 并行 Workstream 规则（哪些可并行、哪些禁止） | 高 | 已复制到 `docs/architecture/kernels-interface.md` §3 |
| §112 | 十二核心 Kernel 定义 | **高**（代码佐证） | **[`KERNEL-CANON.md`](KERNEL-CANON.md) §1** |
| §113 | Audit Kernel 定义 | **高**（代码佐证） | `src/kernels/audit/__init__.py` |
| §115 | Plugin Registry 定义 | **高**（代码佐证） | `src/kernels/plugin/__init__.py` |
| §122 | 条款总数 | 高 | 无 |

---

## 4. 处置方案

### 方案 A — 恢复原件 ❌ **已验证失败，关闭（2026-09-06）**

~~从 git 历史或备份中找回 `LIUHAO-X-V3.0-DEFINITION-LOCK.md`。~~

**验证过程与结果**：

```bash
cd D:/LiuHao-AI-OS
git log --all --name-only --pretty=format: | grep -i "definition" | sort -u
# 输出仅两条，均为 node_modules 内无关文件：
#   frontend/node_modules/@typescript-eslint/eslint-plugin/docs/rules/consistent-type-definitions.md
#   frontend/node_modules/three-mesh-bvh/src/gpu/glsl/bvh_struct_definitions.glsl.js

git log --all --diff-filter=D --name-only --pretty=format: | grep -i "definition"
# 输出为空 —— 无任何删除记录
```

**结论**：原件**从未进入版本库**，不存在"被删除"的历史。git 恢复路径为死路。

**处置**：方案 A 正式关闭。默认改走 **方案 B + C**（见下），
落地见 [`UNIFIED-BLUEPRINT.md`](UNIFIED-BLUEPRINT.md) §0.3。

> 若日后在仓库外的备份/聊天记录中找到 122 节原件，请重新打开本方案并通知 Principal Engineer。

### 方案 B — 重建关键条款（推荐并行做）

基于代码 docstring + Master Spec v3.0，重建 §112 / §113 / §115 三个 Kernel 条款的正式文本，
写入 `docs/spec/DEFINITION-LOCK-KERNELS.md`。这三个有代码佐证，可高保真还原。

### 方案 C — 宣告失效并重定向（当前默认）

在宪法恢复前：

1. **新代码不再引用 `DL:§xx`**，改为引用：
   - Kernel 相关 → [`KERNEL-CANON.md`](KERNEL-CANON.md)
   - 能力相关 → [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md)
   - 目标规格 → [`MASTER-SPEC-v3.0.md`](MASTER-SPEC-v3.0.md)
   - 执行规则 → [`../CODEX-CONTRACT.md`](../CODEX-CONTRACT.md)

2. **历史文档中的引用保持原样**（archive 内不改），但读者须知其依据不可查。

3. **`src/kernels/*` 的 docstring 暂不改**（那是代码历史的一部分），
   但在 `[`KERNEL-CANON.md`](KERNEL-CANON.md)` 中已给出权威解释。

---

## 5. Codex 行动指引

| 你遇到… | 怎么做 |
|---|---|
| 代码里写 `依据 DL:§112` | 理解为"该 kernel 属于十二核心 Kernel"，查 `KERNEL-CANON.md` |
| 需要 Kernel 的接口定义 | 查 [`KERNEL-CANON.md`](KERNEL-CANON.md) 与 `docs/architecture/kernels-interface.md` |
| 需要能力的验收标准 | 查 [`CAPABILITY-REGISTRY.md`](CAPABILITY-REGISTRY.md) |
| 想新增 kernel | 先在 `KERNEL-CANON.md` 登记，说明为何不属于现有 14 个 |
| 被告知"按 §xx 实现"但查不到 | **停下来问人**，不要自己编造条款内容 |

---

## 6. 一句话

**宪法丢了，但按宪法建成的城市还在。** 从代码反推出的 §112/§113/§115 已经足够支撑 Kernel 层的所有工作；
其余条款（§1-§111、§114、§116-§122）在恢复原件前，一律以 `KERNEL-CANON` + `CAPABILITY-REGISTRY` + `MASTER-SPEC` 为准。

> **2026-09-06 更新**：原件恢复路径已验证为死路（§4 方案 A 关闭）。
> 已建立 [`UNIFIED-BLUEPRINT.md`](UNIFIED-BLUEPRINT.md) 作为宪法层单一入口，
> 其中 §2 定义了 `DL:` / `MS:` / `UB:` 命名空间规则与稳定锚点（`UB-A1` 等），
> §3 给出 C1–C12 全部冲突裁决。**新写引用请一律用锚点或带前缀节号，禁止裸写 `§N`。**
