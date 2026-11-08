# 品牌口径（BRAND CANON）

> 一句话：**主名只认 `LiuHao AI OS · 鎏灏智能中枢`；改名只改 `src/brand.json`。**

## 1. 唯一来源

| 角色 | 文件 |
| --- | --- |
| 字面来源（唯一） | `src/brand.json` |
| Python 读取层 | `src/brand.py`（导出 `BRAND_NAME_EN` / `BRAND_NAME_ZH` / `BRAND_LOCKUP`） |
| 前端读取层 | `apps/console/console/src/brand.ts`（引用同一份 JSON） |

`src/brand.json` 的 `name_en` 与 `name_zh` 用 ` · ` 拼成 lockup：

```text
LiuHao AI OS · 鎏灏智能中枢
```

任何安装器、快捷方式、安装目录说明、控制台、PWA、关于页、诊断包、`/docs`、
Nginx、云打包脚本都必须引用上述常量；**禁止另写一份主名**。

## 2. 允许的短名

短名不是第二主名，只能出现在指定位置。

| 短名 | 允许位置 | 说明 |
| --- | --- | --- |
| `鎏灏智能中枢` | PWA `short_name`、Apple / application name、favicon `aria-label` | 中文主名的短写 |
| `鎏灏管家` | `butler` 导航项 | 既有产品功能名 |
| `鎏灏 OS` | 网络图 `hubLabel` | 既有图形短标签 |
| `LiuHao` | 安装器 `AppPublisher`、数据目录 `%LOCALAPPDATA%\LiuHao` | 厂商 / 发布者名，不是产品主名 |

单独的 `鎏灏` 只允许作为日志 / 代码注释 / 桌面通知里的口语简称；
**窗口标题、托盘提示、关于页、安装器、快捷方式、诊断包、`/docs` 等品牌位必须用 lockup**，
新增的用户可见品牌位不得只写 `鎏灏`。

## 3. 禁止项

以下内容不得出现在任何**现行**品牌文本里（历史文档与历史证据除外）：

- 旧主名：`LIUHAO X`、`鎏灏 X`（只能作为“旧称”在命名说明里出现）
- 错字：`镭灏`
- 死域名：`api.liuhao-ai-os.com`
- 对外版本号：`1.0.0`（必须等于 `src/version.py` 的 `APP_VERSION`）

## 4. 渲染点

| 渲染点 | 读取方式 | 期望文本 |
| --- | --- | --- |
| 安装器 `AppName` / 快捷方式 / 卸载显示名 | `installer/liuhao.iss` 的 `AppName` | `LiuHao AI OS · 鎏灏智能中枢` |
| `START-HERE.txt` | `packaging/stage_payload.py` | `LiuHao AI OS · 鎏灏智能中枢 0.1.1 —— 先看这里` |
| `BEFORE-INSTALL.txt` | `installer/assets/BEFORE-INSTALL.txt` | `LiuHao AI OS · 鎏灏智能中枢 —— 安装前请先读这 6 条` |
| `THIRD-PARTY-NOTICES.txt` | `packaging/third-party-notices.template.txt` | `LiuHao AI OS · 鎏灏智能中枢 —— 第三方软件许可声明（THIRD-PARTY-NOTICES）` |
| 控制台页头 / 登录页 | `Shell.tsx` / `Login.tsx` 引用 `brand.ts` | lockup |
| 浏览器标题 / PWA | `index.html` / `manifest.webmanifest` | `<title>` 与 `name` = lockup；`short_name` = `鎏灏智能中枢` |
| 窗口标题 / 关于页 / 诊断包 README / `about.json` | `src/desktop/about.py` 的 `APP_NAME` | lockup |
| 托盘提示 | `src/desktop/app.py` 的 `_tooltip_text()` | `lockup · 状态…`（**必须以 lockup 开头**，状态只做后缀） |
| `/docs` 标题 | `src/gateway/main.py` | `lockup Gateway - Swagger UI`（` Gateway` 是角色词，不属于品牌字面） |
| `openapi.yaml` `info.title` / `info.version` | `scripts/verify_openapi_contract.py` 生成 | `lockup Gateway API` / `APP_VERSION` |
| Nginx / 云打包脚本 | 构建期统一为 lockup | lockup |

角色词（`Gateway` / `Gateway API`）只用于区分「网关」这一运行时角色，不构成第二主名；
除此之外不得给 lockup 增删任何字符。

## 5. 校验方式

### 5.1 构建期硬门禁（推荐，编译安装器前自动执行）

```powershell
.venv\Scripts\python.exe packaging\brand_guard.py --payload dist\payload\app --iss dist\installer\build\liuhao.iss
```

出现旧主名、错字、死域名、`1.0.0`，或主名/版本不一致时，退出码为 `1`，构建中止。
`packaging/stage_payload.py` 与 `packaging/build_windows_installer.ps1` 都已接入。

扫描范围：

- **仓库全量**（`installer/`、`docs/`、`apps/console/`、`src/`、`packaging/`）逐行扫描禁止项，
  输出 `文件:行号`；命中即红。`node_modules/`、`dist/`、`__pycache__/`、`.venv/` 不扫。
- **固定产物**逐字核对主名与版本：`START-HERE.txt`、`THIRD-PARTY-NOTICES.txt`、
  `console/index.html`、`manifest.webmanifest`、`sw.js`、编译用 `liuhao.iss`、`openapi.yaml`。
- **payload 全量文本**扫描禁止项（`.py/.txt/.md/.yaml/.json/.html/.js/.css/.bat/.toml/...`，
  跳过 `runtime/`、`assets/` 等第三方与二进制目录），旧主名与错字不能藏在源码、脚本或文档里。
- `*.egg-info` 不进入 payload（生成元数据，可能残留旧字面），由 `stage_payload.py` 的忽略规则保证。

#### 门禁自证（故意注入 → 红 → 恢复 → 绿）

```powershell
Add-Content docs\quickstart.md '安装旧称：鎏灏 X（故意注入）'
.venv\Scripts\python.exe packaging\brand_guard.py   # 退出码 1，指出 docs/quickstart.md:行号
git checkout -- docs\quickstart.md
.venv\Scripts\python.exe packaging\brand_guard.py   # 退出码 0
```

这条流程已固化成 `tests/test_brand_guard.py::test_repo_tree_scan_reports_file_and_line`。

### 5.2 测试

```powershell
.venv\Scripts\python.exe -m pytest tests\test_brand_consistency.py tests\test_brand_guard.py -q
cd apps\console\console
npx vitest run src/brand.test.ts
```

### 5.3 OpenAPI 快照

```powershell
.venv\Scripts\python.exe scripts\verify_openapi_contract.py
```

## 6. 改名流程

1. 只改 `src/brand.json` 的 `name_en` / `name_zh`。
2. 运行 5.1 与 5.2；必须全绿。
3. 重新构建安装包；构建期门禁会再挡一次。
4. 只有版本变化时才重新生成 `openapi.yaml`。

## 7. 允许清单（历史文本为什么可以保留旧称）

门禁的允许清单在 `packaging/brand_guard.py` 的 `REPO_ALLOW` 里，**每一项都必须写理由**：

| 允许项 | 理由 |
| --- | --- |
| `docs/BRAND-CANON.md` | 规范自身必须列出禁用写法，才能被引用和测试 |
| `docs/archive/` | 历史归档：保留当时原文，改写会破坏证据 |
| `docs/spec/` | 规范/框架原始定义（含 v3.0 历史命名），作为迁移与审计证据保留 |
| `docs/architecture/` | 架构基线与既有代码审计原件，属于历史事实 |
| `docs/operations/` | 运维 runbook 历史记录，保留原样 |
| `docs/product/` | 产品 PRD 历史版本，保留原样 |
| `docs/roadmap/` | 路线图历史记录，保留原样 |
| `docs/l10k/` | 测试设计历史记录，保留原样 |
| `docs/decisions/` | 决策记录：记录当时的决策语境 |
| `docs/*-20261108.md` | 本次审计/冻结的证据快照，按日期封存 |
| `docs/CODEX-CONTRACT.md` | 与外部 Codex 的契约原文，单方面改会失真 |
| `docs/REPO-LINE-CLOSURE.md` | 仓库线收口记录原文，属于历史事实 |
| `docs/ARCHITECTURE.md` | 现行架构文档中的「曾用名/历史代号」说明段 |
| `packaging/brand_guard.py` | 禁用词表实现本身必须包含这些字面 |

新增例外必须在 `REPO_ALLOW` 里显式写理由；**默认是改名，不是放行**。
