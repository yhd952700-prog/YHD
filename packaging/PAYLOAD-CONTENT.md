# 安装包内容清单（Windows）

> 清单由 `packaging/stage_payload.py` 生成；每次构建的实际结果以
> `dist/payload/manifest.json` 为准（含发行包版本、文件数、体积）。
> 本轮实测：**10,958 个文件 / 197.3 MB**（未压缩 payload）。

## 目录布局

```
%LOCALAPPDATA%\LiuHao\
├─ app\                      程序目录（升级时整体替换，不含用户数据）
│  ├─ runtime\               离线 CPython 3.12 运行时 + 85 个第三方发行包（含 tcl/tk 数据）
│  ├─ src\                   网关 / 内核源码（含 src\desktop 托盘模块）
│  ├─ config\                配置模板（已剔除凭据与私钥）
│  ├─ console\               驾驶舱构建产物（Vite build 输出）
│  ├─ scripts\               启动器 / 首次运行向导 / 身份注册 / 桌面托盘入口
│  ├─ capability-registry.yaml
│  ├─ error_types.py
│  ├─ assets\liuhao.ico
│  ├─ LiuHao.bat             快捷方式目标
│  └─ START-HERE.txt
└─ data\                     用户数据（卸载默认保留）
   ├─ config\                human_identities.json / auth_secrets.json / app.env / integrity.key
   ├─ data\                  model_registry.sqlite3 / model_config.key
   ├─ workspace\             员工、目标、项目、工作区文件、JWT 吊销库
   ├─ conversation_store.db
   ├─ audit_store.db
   └─ logs\
```

## 包含 ✅

| 内容 | 说明 |
| --- | --- |
| Python 3.12 运行时 | 构建机的完整 CPython 安装（标准库 + DLL），收件人无需装 Python |
| 第三方依赖 | 按网关/桌面壳真实 import 图命中的 85 个发行包（含 cryptography、pydantic、uvicorn、numpy、**pywin32** 等） |
| 桌面壳与托盘 | `src\desktop\**`、`scripts\liuhao_desktop.py`，以及 tkinter + `tcl/tk8.6` 数据目录（托盘与状态窗口依赖） |
| 驾驶舱静态产物 | `npm run build` 输出，单端口由网关直接服务 |
| 首次运行向导 | 建管理员、选 Provider、连通性自检 |
| 身份 / 密钥生成逻辑 | 首次启动在**用户数据目录**生成 JWT 密钥与身份完整性密钥 |
| 安装器 | 桌面/开始菜单快捷方式、版本升级、卸载保留数据选项 |

## 不包含 ❌

| 内容 | 原因 / 替代方案 |
| --- | --- |
| **任何模型权重** | 体积与许可证。首次向导里二选一：本机 Ollama（自行 `ollama pull`，约 2GB）或 OpenAI 兼容云 API Key |
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

本轮打包扫描：运行文件命中 0（`src/plugins/registry.py`、`src/workflow/checkpoint.py`
的默认落盘路径已改为按 `LIUHAO_WORKSPACE_ROOT` / 仓库 `data` 解析）；
`docs/` 中的历史开发机链接单独归类，不影响安装与升级，详见
`dist\release\dev-path-report.txt`。
