# 仓库线收口（Repo-Line Closure）— 鎏灏 LIUHAO X

> 状态：**已收口（live 实测确认，2026-09-15）**。无需翻转默认分支、无需推送、无需删除 `main`、无需硬合两条无关历史。

## 1. 实测事实（全部经 git / gh 实测，非记忆）

- 远端：`origin = github.com/yhd952700-prog/YHD`
- 两条**互不相干**的历史：
  - `main`：根 `8678f194`（tip `43d46492`，60 个文件）
  - `wip/liuhao-x-evolve`：根 `d5562e38`（tip 本地 `aaacf26e`）
  - `git merge-base main wip/liuhao-x-evolve` 为空（exit 1）→ 历史无关，无法 fast-forward，也无法干净 merge。
- 树关系（权威：`git diff --name-status origin/main wip/liuhao-x-evolve`）：
  - `wip` 是 `main` 的**功能超集**——`main` 的全部内容均含于 `wip`。
  - 仅 **6** 个文件在 `wip` 中被删除，均为文档 / 清单 / 测试，非运行必需：
    - `DELIVERY_GATES.md`
    - `STAGING_CHECKLIST.md`
    - `docs/OPENSOURCE_INTEGRATION_PLAN.md`
    - `docs/Y1_REQUIREMENT_TRACEABILITY.md`
    - `docs/staging-validation-report.md`
    - `src/ai/test_providers.py`
  - 另有 24 个文件两树内容不同（含 `README.md`——`wip` 有自身版本）、1 个重命名、610 个 `wip` 独有文件。
- **GitHub 默认分支 = `wip/liuhao-x-evolve`**（已用 `gh api repos/yhd952700-prog/YHD --jq '.default_branch'` 与 `git ls-remote --symref origin HEAD` 双重实测确认）。
- **远端 `wip/liuhao-x-evolve` tip = `aaacf26e5e1453d26c8e3b407ea92f5ecbab2761`**，与本地硬化后的 `wip` tip **逐字一致**（`git ls-remote` 实时核对）。即可部署 / 可测试状态已在远端，无需推送。

## 2. 结论与推荐

推荐方案 (A)「让默认分支 = `wip`」——**该状态此前已达成且当前仍然成立**：

- `git clone https://github.com/yhd952700-prog/YHD.git` 默认即拉到 `wip/liuhao-x-evolve`，即完整的（664 文件）可运行态。老板开箱即用。
- 不硬合两条无关历史（符合既定决策）；不删除 `main`（`main` 仍冻结保留，作归档）；不 force-rewrite 历史。
- CI 四道守卫（`observability` / `integration` / `smoke` / `gate-check`）以 `if: github.ref_name == github.event.repository.default_branch` 守卫，默认分支 = `wip` 时**在 `wip` 上生效**（不再永久 `skipped`）。

若未来因合规要求默认分支必须为 `main`，则退回方案 (B)：见 §3「从 wip 运行」标准流程，**无需任何远端变更**。

## 3. 老板如何拿到一个可用的鎏灏（标准流程）

方式一（推荐，默认分支已是 `wip`）：

```bash
git clone https://github.com/yhd952700-prog/YHD.git
cd YHD
# 默认即位于 wip/liuhao-x-evolve
```

方式二（显式指定，等价于方式一，便于脚本化）：

```bash
git clone -b wip/liuhao-x-evolve https://github.com/yhd952700-prog/YHD.git
```

后续运行见仓内已有文档：

- 本地起服务：`python scripts/start_liuhao.py`（Windows 可直接 `start-liuhao.bat`）。
  注意：必须先 `export LIUHAO_JWT_SECRET=...`（本仓 `.env` 不进 `os.environ`，详见 PITFALLS 本地起服务坑）。
- 云包：`python scripts/build_cloud_bundle.py` 生成 `deploy/cloud/`，再 `python deploy/cloud/serve.py`。
- 验收基线：`config/production/acceptance_checklist.md`；本仓根 `ACCEPTANCE.md`。

## 4. 留给 lead 的注意事项（非阻塞）

- **本地 `origin/wip/liuhao-x-evolve` 远程跟踪引用是陈旧的**（`d9594026`），但 `git ls-remote` 实时显示远端已是 `aaacf26e`。本地缓存过期，无害；如需刷新：`git fetch origin`
  （注意本仓 `.git/packed-refs` 为 CRLF，可能触发 `ignoring ref with broken name`，先按 PITFALLS #34 把 packed-refs 转 LF 再 fetch）。
- `main` 保留为冻结归档分支（60 文件、无关根），按既定决策不删除、不合并。
- 是否给 `main` 打一个归档 tag（如 `archive/pre-wip`）以便追溯——可选，非必需，待 lead 决定。

## 5. 未执行的操作（遵守 Git 纪律）

- 未翻转默认分支（实测确认默认即 `wip`）。
- 未推送（远端 `wip` 已与本地 tip 一致，无内容可推）。
- 未删除 / 改写任何历史。
- 任何远端变更均由 lead 统一执行。

---

### 附：若需重新确认 / 翻转默认分支的精确命令（当前非必需，仅供 lead 留存）

```bash
# 读取当前默认分支（读，无副作用）
gh api repos/yhd952700-prog/YHD --jq '.default_branch'

# 若默认分支不是 wip/liuhao-x-evolve，需先确保远端 wip 为期望 tip，再翻转：
# 1) 推送本地硬化 tip（非破坏性 fast-forward，仅 lead 执行）：
git push origin aaacf26e5e1453d26c8e3b407ea92f5ecbab2761:refs/heads/wip/liuhao-x-evolve
# 2) 翻转默认分支为 wip（需 repo admin；不改写历史、不删 main）：
gh api -X PATCH repos/yhd952700-prog/YHD \
  -f default_branch='wip/liuhao-x-evolve'
# 或 GitHub Web: Settings → Branches → Default branch 下拉选 wip/liuhao-x-evolve
# 3) 复核
git ls-remote --symref origin HEAD
```
