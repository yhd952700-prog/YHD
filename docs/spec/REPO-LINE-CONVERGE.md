# 仓库线收口（REPO LINE CONVERGE）

> 2026-09-12 · Round 78 · 总工程师执行
> 本文记录「仓库里存在两条互不相干的历史」这一事实、处置决策与回滚方法。

## 1. 事实

| 项 | 值 |
| --- | --- |
| `git merge-base main wip/liuhao-x-evolve` | **空，退出码 1**（无共同祖先） |
| `main` 根提交 | `8678f194` `Initial commit`（2026-08-24） |
| `wip/liuhao-x-evolve` 根提交 | `d5562e38` `feat: 固化 LIUHAO X (鎏灏) V3.0 完整成果` |
| `git rev-list --left-right --count main...wip/liuhao-x-evolve` | `28 / 108` |
| GitHub 仓库 | `yhd952700-prog/YHD` |

即：仓库里同时躺着两个项目。`main` 是 2026-08 的早期 "AI Employee" 线
（MCP 配置、Phoenix 观测、LangGraph、Mem0、指标持久化）；`wip/liuhao-x-evolve`
是鎏灏的真实工作线，两者历史**从未交汇**。

## 2. 关键补充事实：wip 的文件树是 main 的超集

```
git diff --diff-filter=D main wip/liuhao-x-evolve
```

只有 **7 个文件**在 wip 中不存在：

| 文件 | 判定 |
| --- | --- |
| `README.md` | 旧项目说明，已失效（本文档同级新增鎏灏 README） |
| `DELIVERY_GATES.md` | 旧项目交付门禁 |
| `STAGING_CHECKLIST.md` | 旧项目 staging 清单 |
| `docs/OPENSOURCE_INTEGRATION_PLAN.md` | 旧项目计划 |
| `docs/Y1_REQUIREMENT_TRACEABILITY.md` | 旧项目追溯矩阵 |
| `docs/staging-validation-report.md` | 旧项目验证报告 |
| `src/ai/test_providers.py` | **有意删除**（Round 74 清理废弃孤儿，见 MEMORY） |

⇒ 切换到 wip 不会丢失任何仍在使用的资产。

## 3. 决策：不硬合，改默认分支

**不合并两条历史**，理由：

1. 无共同祖先的合并需要用 `--allow-unrelated-histories`，会产出一个不可读的
   monster merge，且冲突集中在 `pyproject.toml`、`src/`、`docs/`。
2. `main` 上的 28 个 commit 属于**已被取代的旧技术栈**（MCP / Phoenix / LangGraph /
   Mem0）。把它们的历史织进鎏灏，只会污染 `src/` 的演进记录与 `git blame`。
3. 早期线已被完整包含在 wip 的文件树里，没有需要"捞回来"的代码。

**因此采取**：归档旧线 + 把默认分支切到鎏灏工作线。这是最小风险路径，且完全可逆。

## 4. 已执行

| 步骤 | 命令 / 结果 |
| --- | --- |
| 归档 tag | `legacy/ai-employee-2026-09-03` → `43d464928252774c1a3638175ac3c8431fd3afe2` |
| 推送 tag | `git push` 失败（沙箱清 ref，见 §5）→ 改用 `gh api POST /git/refs` **成功** |
| 切默认分支 | `gh api -X PATCH .../YHD -f default_branch=wip/liuhao-x-evolve` → 已生效 |

验证：`gh repo view --json defaultBranchRef` → `wip/liuhao-x-evolve`

## 5. 执行中的坑（已入 PITFALLS）

- **本地 tag 会被沙箱清掉**：`git tag` 创建成功、`git tag -l` 也立刻查不到，
  随后 `git push origin <tag>` 报 `src refspec ... does not match any`。
  与已知的「沙箱清空 `.git/refs/heads/wip`」属同一类问题。
  **兜底**：直接用 Git Data API 建 ref——
  `gh api -X POST repos/<owner>/<repo>/git/refs -f ref=refs/tags/<name> -f sha=<sha>`。

## 6. 回滚方法

```bash
# 切回旧默认分支
gh api -X PATCH repos/yhd952700-prog/YHD -f default_branch=main

# 找回旧线（tag 已在远端）
git fetch origin --tags
git checkout legacy/ai-employee-2026-09-03
```

## 7. 遗留

- 3 个**僵尸 PR** 属旧线，尚未处置：`#17`（p0/opentelemetry-adapter）、
  `#5`（patch-2）、`#4`（patch-1）。它们基于旧 `main`，需单独决定是否关闭。
- 分支 `develop`、`master`、`master`/`main` 多套并存，后续可再清理。
- 根 `README.md` 已重写为鎏灏版本（旧项目的 README 本就不在 wip 上）。
