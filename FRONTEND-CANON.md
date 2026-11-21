# FRONTEND-CANON — 前端正典登记（仓库级，冲突时以本文件为准）

> 依据：《JARVIS-前端两代对齐审计》（2026-11-18）裁决与第二季 V4 落地。
> 任何前端任务派单必须写明目标树；写错树 = 返工。

## 1. 正典

- **发布前端 = `console/`**：wb 世代 dist（`console/index.html` + `console/assets/`，主标记
  `wb-shell`，位于 Workbench 懒加载 chunk）+ 主题叠加层 `console/theme/` 四文件
  （`jarvis-theme.css/js`、`jarvis-future-theme.css/js`）+ 叠加层资产。
  一切用户可见前端行为以这棵树为准；前端改动只走主题叠加层 / 后端，**不许从
  apps 源码重建 dist**（2026-11-24 对齐窗口关闭、A3 重建启动前，此令不解除）。
- **`apps/console` = 非发布参考树（os 世代）**：只许版本号与测试维护，禁新增功能
  （见 `apps/console/FROZEN.md`）。其 vitest 测试是「os 世代源码测试，非发布物覆盖」，
  任何报告引用必须带此标注。
- wb dist 的可维护源码不在本仓库（在旧账号工作区）；A3 计划 = 按
  `docs/frontend-dom-contract.md` 的 71 选择器 DOM 契约重建源码并回植 2 项丢失件
  （首启时区向导、导出确认范围文案），清单见 `docs/frontend-a3-backlog.md`。

## 2. 护栏（机器检查：`scripts/check_frontend_guardrails.py`）

| # | 护栏 | 检查 |
|---|---|---|
| 1 | 正典登记（本文件） | — |
| 2 | 禁重建断言：可达资产集含 `wb-shell` + 主题四文件齐全 | `canon` |
| 3 | 资产可达性：孤儿 chunk 报警，打包默认剔除 | `reachability --strict` |
| 4 | 指纹对账：dist 变更必须重新签章 + 在本文件第 4 节留变更说明 | `fingerprint` |
| 5 | 发布冒烟：包内 dist 冷启 + 三屏截图零 pageerror | `scripts/smoke_release_ui.py` |
| 6 | 测试口径纠偏：dist 契约测试 `tests/test_console_dist_contract.py`；vitest 标注见上 | pytest 子集 |
| 7 | 成果落点双查：含前端改动的交付逐项写「落点文件 + 是否在发布物内 + 发布物内验证方式」 | 发布清单 |
| 8 | os 树冻结 | `apps/console/FROZEN.md` |

## 3. 发布清单（打包前逐项过，缺一不发）

1. `python scripts/check_frontend_guardrails.py all --strict` 全 PASS。
2. `python scripts/verify_openapi_contract.py` PASS（后端契约与运行时一致）。
3. 打包后以包内 dist 跑 `scripts/smoke_release_ui.py`：未来全景 / 经典 / 登录三屏
   截图与基线比对、零 pageerror。
4. 本次交付含前端改动时，逐项列落点双查表（护栏 7）。
5. dist 有任何字节变更：先在本文件第 4 节登记变更说明（来源、原因、验证证据），
   再用 `src/upgrade/console_contract.py::build_stamp` 重新签章
   `console/console-contract.json`（保留 `api_contract` 与 `frontend_version`，
   只刷新指纹/文件数/built_at），最后重跑第 1 项。

## 4. dist 变更账（护栏 4 留痕）

### 2026-11-18 · 第二季 V4 · 剔除 26 个 os 世代孤儿 chunk + 签章对账

- **来源**：0.5.0 人工组装时混入的 os 世代重复 chunk（每页一份 wb 正典 + 一份 os
  孤儿），审计报告已盘点（26 个 / 985,822 字节）。
- **变更**：删除 26 个自 `console/index.html` 引用图传递不可达的 assets 文件
  （删除前复核：文件名不出现在 index.html、主题层、manifest、sw.js 任何引用面）；
  `console/assets` 70 → 44 个文件，与 wb 正典 44 可达集一致。
- **签章**：原签章自 2026-11-16 构建后从未刷新（签 54 文件 vs 实际 97，主题叠加层
  与 PWA 资产加入后一直未签），本次按官方算法重签（71 文件，指纹
  `020cd5281bfd930d…`）；`api_contract=1`、`frontend_version=0.4.6` 沿用原值
  （wb bundle 本体未动，不改其出身版本）。
- **验证**：`check_frontend_guardrails.py all --strict` 五项全 PASS；网关以本 dist
  冷启冒烟三屏零 pageerror（证据 `upgrade2-evidence/shots/v4-smoke-*`，在本仓库外）。

### 2026-11-18 · 第二季 V4 · 主题层补「关于」区下载入口 + 随签

- **来源**：V1.5 遗留项（设置规格卡口径「关于承接下载桌面版」；顶栏下载钮已在
  V1.5 顶栏减肥时移除，入口一直缺位）。
- **变更**：仅 `console/theme/jarvis-theme.js`——新增 `fixAbout()`，在「关于与隐私」
  页常驻一张下载卡（文字链指向与登录页下载链同一地址：仓库 GitHub Releases
  最新版）。dist bundle 本体未动。
- **签章**：随变更重签（71 文件，指纹 `f8009c58108bb67f…`）。
- **验证**：实机渲染验证——卡片可见、链接地址正确（证据
  `upgrade2-evidence/shots/v4-about-download.png`，在本仓库外）；
  `check_frontend_guardrails.py all --strict` 复跑全 PASS。
