# 安装包内容清单（Windows）

> 清单由 `packaging/stage_payload.py` 生成；每次构建的实际结果以
> `dist/payload/manifest.json` 为准（含发行包版本、文件数、体积）。
> 本轮实测（交付面修复后）：**11,347 个文件 / 165.5 MB**（未压缩 payload）。
> 安装后为 10,502 个文件 / 170,666,366 字节（0.1.0 交付包实测）；体积随版本变化，
> 最终构建一律以 `manifest.json` 的 `file_count`/`size_mb` 为准。

## 目录布局

```
D:\LiuHao\                    程序目录（升级时整体替换，不含用户数据）
│                             D 盘不可用 → %LOCALAPPDATA%\Programs\LiuHao
├─ runtime\                  离线 CPython 3.12 运行时 + 第三方发行包（含 tcl/tk 数据）
├─ src\                      网关 / 内核源码（含 src\desktop 托盘模块）
├─ config\                   配置模板（已剔除凭据与私钥）
├─ console\                  驾驶舱构建产物（Vite build 输出）
├─ scripts\                  启动器 / 首次运行向导 / 身份注册 / 桌面托盘入口
├─ licenses\                 OpenSSL / libffi / SQLite / Tcl-Tk / MSVC / React / pywin32 许可证文本
├─ capability-registry.yaml
├─ error_types.py
├─ THIRD-PARTY-NOTICES.txt   第三方软件清单与许可证索引
├─ assets\liuhao.ico
├─ LiuHao.bat                命令行排障入口（需显式 --console；失败时 pause 留住窗口），不是快捷方式目标
├─ START-HERE.txt
└─ data-dir.txt               安装器写入的实际数据目录（UTF-8 带 BOM）

D:\LiuHaoData\                用户数据（卸载默认保留）
│                             D 盘不可用 → %LOCALAPPDATA%\LiuHao\data
│                             实际位置以程序目录 data-dir.txt 为准
├─ config\                    human_identities.json / auth_secrets.json / app.env / integrity.key
├─ data\                      model_registry.sqlite3 / model_config.key
├─ workspace\                 员工、目标、项目、工作区文件、JWT 吊销库
├─ conversation_store.db
├─ audit_store.db
└─ logs\
```

> 桌面 / 开始菜单快捷方式与安装完成页「立即启动」的目标是
> `runtime\pythonw.exe` + `scripts\liuhao_desktop.py app`（见 `installer\liuhao.iss`
> 的 `[Icons]` / `[Run]`），不是 `LiuHao.bat`；即便命令行里出现 `--open-browser`
> （历史参数）也不会切换到浏览器模式，网页模式一律用 `--web`。`LiuHao.bat` 只作命令行排障入口。

## 包含 ✅

| 内容 | 说明 |
| --- | --- |
| Python 3.12 运行时 | 构建机的完整 CPython 安装（标准库 + DLL），收件人无需装 Python |
| 第三方依赖 | 按网关/桌面壳真实 import 图命中的发行包（数量以构建日志为准；清单见安装目录 `THIRD-PARTY-NOTICES.txt`，由 `packaging/third_party_notices.py` 按实装现场生成；含 cryptography、pydantic、uvicorn、**pywin32** 等） |
| 桌面壳与托盘 | `src\desktop\**`、`scripts\liuhao_desktop.py`，以及 tkinter + `tcl/tk8.6` 数据目录（托盘与状态窗口依赖） |
| 驾驶舱静态产物 | `npm run build` 输出，单端口由网关直接服务 |
| 首次运行向导 | 建管理员、选 Provider、连通性自检 |
| 身份 / 密钥生成逻辑 | 首次启动在**用户数据目录**生成 JWT 密钥与身份完整性密钥 |
| 安装器 | 桌面/开始菜单快捷方式、版本升级、卸载保留数据选项 |
| 第三方许可证 | `THIRD-PARTY-NOTICES.txt` + `licenses\`（OpenSSL、libffi、SQLite、Tcl/Tk、MSVC、React、pywin32） |

## 不包含 ❌

| 内容 | 原因 / 替代方案 |
| --- | --- |
| **任何模型权重** | 体积与许可证。首次向导三选一：本机 Ollama（自行 `ollama pull`，约 2GB）、OpenAI 兼容云 API Key、或 `0) 跳过`（之后在模型管理补） |
| **任何密钥 / 凭据** | `config/auth_secrets.json`、`config/human_identities.json`、`external_anchor/anchor_key.pem`、`docker.env`、`*.pem`、`*.key` 在组装阶段被显式剔除；构建脚本还会做二次泄漏检查，命中即中止 |
| **源码仓库的 `.env`** | 不进包；安装版在 `data/config/app.env` 首次生成 |
| 开发/测试依赖 | pytest、ruff、mypy、semgrep 等不随包 |
| Docker / 数据库服务 | 安装版是单机单进程 + SQLite，不依赖 Docker/Postgres |
| 代码签名 | 当前**未签名**，Windows 会提示「未知发布者」 |

## 与仓库便携包的区别

仓库里 `scripts/build_cloud_bundle.py --portable` 产出的是「需要用户自备 Python」的
便携目录；本安装包在此基础上多做了三件事：

1. 自带离线 Python 运行时与依赖，双击即用；
2. 程序目录与用户数据目录分离（`app\` vs `data\`）；
3. Inno Setup 安装器（快捷方式、升级、卸载数据保留策略）。

## 升级发布包（与安装包不同）

`dist\release\liuhao-<版本>.zip` 是升级器用的**源码整包**，只含
`src/`、`docs/`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`
与包根 `LIUHAO_RELEASE.json`；不含运行时、驾驶舱产物、用户数据与任何密钥。
同目录的 `LIUHAO_RELEASE.json` 是发布清单（整包 SHA256 + 字节数），
规范见 [docs/self-upgrade.md](../docs/self-upgrade.md) §2.2。

本轮打包扫描：运行文件命中 0（0.1.1 曾命中 2 处；本轮修掉了
`scripts/collect_diagnostics.bat` 的 `.venv\Scripts` 兜底，`src/plugins/registry.py`、
`src/workflow/checkpoint.py` 的默认落盘路径此前已改为按 `LIUHAO_WORKSPACE_ROOT` / 仓库
`data` 解析）；`docs/` 中的历史开发机链接单独归类（本轮 490 个文档文件命中），
不影响安装与升级，详见 `dist\release\dev-path-report.txt`。
