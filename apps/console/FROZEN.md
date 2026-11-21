# 本树已冻结（os 世代，非发布参考树）

依据仓库根 `FRONTEND-CANON.md` 与《JARVIS-前端两代对齐审计》裁决：

- 发布前端正典是仓库根 `console/`（wb 世代 dist + 主题叠加层）。
- 本树（`apps/console`，os 世代源码）**只许版本号与测试维护，禁止新增功能**；
  新前端需求一律记入 `docs/frontend-a3-backlog.md`，等 A3 正典源码重建后实施。
- 本树的 vitest 测试在报告与 CI 输出中必须标注：
  **「os 世代源码测试，非发布物覆盖」**——被测的没发布，发布的没被测，
  引用数字时不许省略此标注。
- **严禁**用本树构建产物覆盖 `console/`（会同时杀掉双主题叠加层与 71 选择器
  DOM 契约，见 `docs/frontend-dom-contract.md`）。
