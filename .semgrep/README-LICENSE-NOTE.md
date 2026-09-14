# Semgrep 规则集 —— 许可证边界说明

本目录只包含 **YAML 规则文本**（`liuhao.yml`），不含也不派生 Semgrep 的任何代码。

## Semgrep 自身的许可证

Semgrep 本体是 **LGPL-2.1**（弱 copyleft）。本项目对它的使用方式 deliberately
限定为「**以独立工具在 CI 进程里调用**」，因此落入 **mere aggregation（单纯聚合）**
范畴，**不触发** LGPL-2.1 的 copyleft 义务：

- ✅ 允许：在 CI 里 `pip install semgrep` 后作为独立 CLI 进程扫描代码树；
- ❌ 禁止：**fork、内置、或以任何形式把 Semgrep 代码打进鎏灏的分发物**
  （包括但不限于 `pyproject.toml` / `requirements.txt` 依赖、打包进 wheel、
  作为库 `import semgrep` 调用）。一旦这么做，我们的改动就可能被要求开源。

## 为什么 rules 文件不带许可证头

规则文本（pattern + paths + message）是配置而非代码，不构成对 Semgrep 的衍生作品，
因此无需（也不应）以 LGPL 条款分发。若未来引入任何从 Semgrep 复制/改编的代码，
则必须回到上面的边界判断。

## 调用约定（与 `.github/workflows/ci.yml` 的 architecture-gate 作业对齐）

- 仅在 CI 作业内临时 `pip install semgrep`，不在任何依赖清单里声明；
- 必须带 `--error`，否则命中时仍 `exit 0`，门禁形同虚设（实测：默认 exit 0，
  加 `--error` 后命中即 exit 1）；
- 规则集须保持「当前代码树 0 命中」。新增规则若做不到 0 命中，放进 advisory 而非
  本文件 —— 一个从第一天就红的门禁等于没有门禁。
