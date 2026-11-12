# 安装体积与启动耗时归因（只读审计）

- 审计日期：2026-11-08
- 审计对象：**当前已安装版本**（`D:\LiuHao`）+ **当前源码快照**（`d:\LiuHao-AI-OS`）
- 约束遵守：**只读**。未修改任何源码、打包脚本、`D:\LiuHaoData`；运行时数据只落在 `D:\_audit\startup-profile\audit-6ae9f041\`

## 0. 先说清楚"测的是哪一份"

| 项目 | 值 |
| --- | --- |
| 仓库 HEAD | 测量时 `8e40d985`；落稿时 **`870d89b8`**（15:41 另一条线的提交，**把本轮的 `main.py` 改动一并带走了**） |
| 工作树未提交改动 | 测量开始时 233 项，写报告时 273 项（**其他线仍在改，快照在漂移**） |
| 已安装版本入口 `D:\LiuHao\src\gateway\main.py` SHA256 | `E7070A8F…B623125` |
| 测量时快照 `src/gateway/main.py` SHA256 | `6AA0844C…EA08D82` |
| 结论 | **已安装版本 ≠ 当前源码快照**；下文的"启动耗时"来自已安装版本，体积/import 归因来自已安装运行时 + 当前快照的 import 图 |
| 已安装目录实测 | 10,303 文件 / **162.00 MiB**（14:05 采样）；报告落盘时 10,341 文件 / 162.51 MiB（并发写入 + 审计自身产生的 `.pyc`） |
| 当前 build payload | `dist/perf-20261108/payload`：11,469 文件 / **167.72 MiB** |
| 安装包 | `LiuHao-Setup-0.1.1.exe` **33.22–33.46 MiB**（Inno `lzma2/max`） |

**关于"148MB"**：本次在三个口径下都没有测到 148MB——已安装 162.5 MiB（170.4 MB 十进制）、payload 167.72 MiB、安装包 33.2 MiB。148 更可能是更早 build 的 payload 或某次只统计了 `runtime\` 的口径。下文一律用实测值，不沿用 148MB。

**测量环境（会放大绝对值）**：CPU 99–100%，机器上约 55 个 python 进程并发（仓库多条线同时在跑测试/构建）。因此同一阶段的多次采样方差很大（例如"main 顶层依赖导入"在 4.97s–7.01s 之间）。本文给出区间与中位口径，**不要拿绝对秒数与空闲机器对比**，看占比和排序即可。

另外有一次端口冲突需要记录：**14:06:44 起 `127.0.0.1:19011` 被另一条线的进程（PID 24616，`python -m src.gateway`）占用**，此后 `launch-warm` 只用了 479ms 返回且 `profile=false`，属于污染样本，已丢弃。本文只采用 14:05:12 之前的两份有效样本（`startup-launch-fresh.json`、`startup-direct-warm1.json`）和一份劣化样本（`startup-direct-warm2.json`，仅用于展示方差）。未杀该进程。

---

## 1. 表一：体积 Top 20（已安装目录，MiB）

| # | 文件（相对 `D:\LiuHao`） | MiB | 是否必需 | 判定依据 |
| --- | --- | --- | --- | --- |
| 1 | `runtime\Lib\site-packages\cryptography\hazmat\bindings\_rust.pyd` | 9.48 | **必需** | 密钥/JWT/本地加密的后端；删了登录与密钥初始化直接失败 |
| 2 | `runtime\DLLs\libcrypto-3-x64.dll` | 7.62 | **必需** | OpenSSL，`ssl` / `cryptography` 的底层 |
| 3 | `runtime\python312.dll` | 6.65 | **必需** | 解释器本体 |
| 4 | `runtime\Lib\site-packages\pythonwin\mfc140u.dll` | 5.40 | 非必需 | PyWin32 自带 GUI IDE（pythonwin）的 MFC 运行时；应用不 import pythonwin |
| 5 | `runtime\Lib\site-packages\pydantic_core\_pydantic_core.cp312-win_amd64.pyd` | 4.92 | **必需** | Pydantic v2 / FastAPI 请求校验 |
| 6 | `unins000.exe` | 4.15 | **必需** | 卸载器；与 `unins000.dat` 合计 7.61 MiB，删了无法卸载 |
| 7 | `runtime\Lib\site-packages\lxml\etree.cp312-win_amd64.pyd` | 3.85 | **必需** | `python-docx` 的 XML 后端（知识导入） |
| 8 | `unins000.dat` | 3.46 | **必需** | 卸载数据 |
| 9 | `runtime\Lib\site-packages\PyWin32.chm` | 2.52 | 非必需 | 离线帮助文档，运行期零引用 |
| 10 | `runtime\DLLs\tcl86t.dll` | 1.78 | **必需** | tkinter 首启 GUI（`first_run_gui.py`） |
| 11 | `runtime\Lib\site-packages\lxml\objectify.cp312-win_amd64.pyd` | 1.66 | 非必需 | `lxml.objectify` 只有显式 import 才加载；本仓库 0 处引用 |
| 12 | `runtime\DLLs\sqlite3.dll` | 1.53 | **必需** | 本地数据库 |
| 13 | `runtime\DLLs\libssl-3-x64.dll` | 1.51 | **必需** | HTTPS / TLS |
| 14 | `runtime\DLLs\tk86t.dll` | 1.47 | **必需** | tkinter |
| 15 | `runtime\Lib\site-packages\pythonwin\scintilla.dll` | 1.43 | 非必需 | pythonwin IDE 的编辑器控件 |
| 16 | `runtime\DLLs\unicodedata.pyd` | 1.08 | **必需** | 标准库 |
| 17 | `runtime\Lib\site-packages\pythonwin\win32ui.pyd` | 1.00 | 非必需 | pythonwin IDE 的 UI 扩展 |
| 18 | `runtime\Lib\site-packages\google\genai\types.py` | 0.84 | **必需** | Gemini provider 的类型定义，`import google.genai` 必需 |
| 19 | `runtime\Lib\site-packages\google\_upb\_message.pyd` | 0.69 | **必需** | protobuf 运行时，google-genai 的传递依赖 |
| 20 | `runtime\Lib\site-packages\pywin32_system32\pythoncom312.dll` | 0.66 | **必需** | pywin32 COM 运行时，托盘/系统集成路径 |

紧随其后（21–23）：`zstandard\_cffi.pyd` 0.64（非必需，见清单）、`watchfiles\_rust_notify.pyd` 0.61（非必需，仅 `uvicorn --reload` 用）、`pypdf\_codecs\adobe_glyphs.py` 0.55（必需，`import pypdf` 即加载）。

**结论：Top 20 里约 60% 是硬依赖（加密/解释器/Pydantic/SQLite/Tk/Google SDK），单纯删文件省不出大空间。真正可回收的是"开发工具面"：pythonwin IDE、CHM 帮助、PyWin32 Demos、第三方 tests、类型桩、Tcl/Tix 示例。**

体积构成（按目录）：

| 目录 | 文件数 | MiB |
| --- | --- | --- |
| `runtime\Lib`（其中 `site-packages` 约 100.9 MiB / 7,730 文件） | 8,404 | 114.62 |
| `runtime\DLLs` | 37 | 17.12 |
| `runtime\`（根 DLL + unins 等） | 8 | 7.06 |
| `runtime\tcl` | 1,265 | 6.75 |
| `src` | 575 | 8.35 |
| `console`（前端产物） | 11 | 0.61 |
| 其他（scripts/config/licenses/assets 等） | 约 30 | 0.27 |

关于用户提到的几个怀疑点，实测结论：

- **重复 DLL**：>200KB 的 DLL/PYD/EXE **0 组重复**（SHA256 全量比对），不存在"同名多份二进制"。
- **多份字体**：**0 个** `.ttf/.otf/.woff/.woff2`。
- **`__pycache__`**：当前 303 个 `.pyc` / 6.23 MiB（其中 264 个在 `runtime\Lib`，39 个是审计期间 `src` 下新生成的）。**它是启动加速缓存，不是垃圾**——删掉会让每次冷启动重新编译，反而更慢（见 §5）。
- **测试数据**：`site-packages` 下 `tests/test` 目录 400 个文件 / 2.86 MiB，运行期零引用。
- **SDK 传递依赖**：`google-genai` 的 `_upb`、`aiohttp`（被 `taskiq` 硬性 import）、`lxml`（被 `python-docx`）都属于"看起来能删、实际删了会坏"的一类。

---

## 2. 表二：启动阶段耗时（进程创建 → `/v1/ready`）

样本 A = 热数据直启（`startup-direct-warm1.json`，内部 8.84s）；样本 B = 首启（`startup-launch-fresh.json`，内部 12.97s）；样本 C = 同配置劣化样本（`startup-direct-warm2.json`，内部 11.54s），仅用于说明方差。

| 阶段 | A 热态 (ms) | B 首启 (ms) | 占 A 内部总时长 | 说明 |
| --- | --- | --- | --- | --- |
| 进程创建 → T0（解释器 + site + 启动器） | ~716 | ~3,112 | 8.1%（外部分母） | 外部计时减内部计时；含 PowerShell 轮询粒度 |
| T0 → main 顶层依赖 import 完成 | **4,966.8** | **7,006.5** | **56.2%** | fastapi / security / prometheus；**最慢一步** |
| get_app 构建小计 | 2,368.5 | 2,054.0 | 26.8% | 见下 5 行 |
| ├ 运行配置加载 | 135.9 | 176.3 | 1.5% | yaml + APP_ENV |
| ├ 中间件注册（CORS/GZip/Tracing/Host 等） | 652.6 | 473.8 | 7.4% | 含 `jsonable_encoder` 等首次触达 |
| ├ 路由：health / observability | 46.2 | 43.6 | 0.5% | |
| ├ 路由：diagnostics | 48.9 | 73.6 | 0.6% | |
| ├ 路由：knowledge | 284.3 | 258.9 | 3.2% | 触发知识库 ORM/引擎 |
| ├ 路由：chat | 69.5 | 48.3 | 0.8% | |
| ├ 路由：ai_management / dashboard / roster / profile / kernels | **714.9** | 569.6 | 8.1% | 单块最大路由 |
| ├ 路由：models | 226.8 | 176.1 | 2.6% | |
| ├ 路由：upgrade / accounts / memory | 185.7 | 229.1 | 2.1% | 33.0 + 115.5 + 37.2 |
| ├ 静态驾驶舱挂载 | 3.7 | 5.0 | 0.04% | 前端静态**不慢**，只是 mount |
| 入口模块导入（uvicorn） | 239.8 | 315.4 | 2.7% | |
| 入口配置解析（server.timeouts 等） | 92.7 | 337.1 | 1.0% | |
| uvicorn.run → lifespan 开始（含 bind 前间隙 238ms） | 280.1 | 626.5 | 3.2% | 有约 238ms 未打点间隙 |
| lifespan 启动合计 | 443.3 | 3,825.6 | 5.0% | 见下 |
| ├ 密钥 / JWT / RBAC 初始化 | 14.2 | **828.9** | 0.2% | 首启生成密钥，一次性成本 |
| ├ 执行器围栏安装 | 39.5 | 429.0 | 0.4% | |
| ├ 策略执法快照 | 4.3 | 5.7 | 0.05% | |
| ├ 身份存储加载 | 3.8 | 4.5 | 0.04% | |
| ├ 恢复码交接迁移 | 11.7 | 43.8 | 0.1% | |
| ├ 老记忆迁移 | 4.3 | 5.6 | 0.05% | |
| ├ 内核 `initialize_all` | 1.8 | 2.6 | 0.02% | |
| ├ 执行 → 告警绑定 | **286.1** | 407.3 | 3.2% | C 样本劣化到 **1,428** |
| ├ 审计 checkpointer | 44.5 | 45.8 | 0.5% | |
| ├ 生产告警规则注册 | 20.3 | 27.8 | 0.2% | |
| ├ 内置插件注册 | 4.1 | 6.2 | 0.05% | |
| ├ 指标评估循环启动 | 4.4 | 11.1 | 0.05% | |
| └ 就绪 + core ready 打点 | 4.9 | 12.3 | 0.05% | |
| core ready → `/v1/ready` 可服务 | ~386 | ~721 | 4.4% | uvicorn 事件循环切换 + 首次探活 |
| **内部总计（T0 → core ready）** | **8,460.8** | **12,254.1** | 100% | 外部观测 9.56s / 16.08s |

**读法（重要）：**

1. **最慢的是"import 依赖"，不是数据库、迁移、资源解压或前端**。`main` 顶层 import 独占 56%，加上路由模块的首次 import，import 类成本占启动的 **80% 以上**。
2. **数据库打开与迁移合计 < 60ms**（恢复码迁移 11.7 + 老记忆迁移 4.3，首启 43.8+5.6）；没有"迁移慢"的问题。
3. **没有内置资源解压**：打包是明文目录，不存在运行时解压 payload；`console` 静态资源只是 `StaticFiles` 挂载（3.7–5.0ms）。
4. **前端静态资源加载不是服务端成本**：服务端只挂载目录，真正的加载发生在浏览器。
5. **启动时没有做**：全量文件扫描、模型列表预加载、向量索引构建。已核对：扫描函数只在测试/守卫里调用；对话链预热走后台线程（仅记录 OTel 6.3ms）；模型列表按请求读取配置，不在 lifespan 预载。
6. 首启比热态多出的 ~3.8s 主要是：pre-T0 的 2.4s（杀软扫描新 exe / 首次载入 DLL）、密钥生成 0.8s、执行器围栏 0.4s。**首启的 16s 不能当作日常体验。**

---

## 3. import 阶段：谁最耗时

`python -X importtime` 冷样本（`importtime-cold-getapp.log`，867 个模块，`get_app()` 全程 10,147ms；该数值含解释器自身 import 开销，与表二的口径不同，只看排序）：

| 模块 | 累计 (ms) | 自身 (ms) | 归因 |
| --- | --- | --- | --- |
| `src.gateway.main` | 9,957 | 232 | 全部子导入的容器 |
| `fastapi` | 3,791 | 15 | `routing` 3,553（自身 167）、`params` 2,031、`exceptions` 1,237（自身 127）、`openapi.models` 744（**自身 413**）、`security` 652 |
| `src.security` | 2,668 | 6 | `vault_crypto` 2,040 → `integrations.vault.client` **2,037** → `hvac` 1,213 + `requests` 716 |
| `src.integrations` | 818 | 8 | 28 个适配器在 `__init__` 里被一次性导入，合计约 0.71s（shopify 86.8、box 85.1、confluence 81.3、wordpress 57.2、salesforce 49.9…） |
| `src.observability.metrics` | 815 | 4 | `prometheus_client` 791；`platform_collector` 544（**自身 538，单模块最贵**） |
| `src.kernels._registry` | 411 | 50 | `trust` 79.6、`execution` 79.4 |
| `pydantic_core.core_schema` | 170 | 156 | Pydantic v2 的 schema 编译 |
| `email_validator.rfc_constants` | 131 | 131 | 账号模块的校验依赖 |
| `typing_extensions` | 120 | 120 | 纯模块加载（磁盘/杀软） |

自身耗时 Top 5：`prometheus_client.platform_collector` 538ms、`fastapi.openapi.models` 413ms、`src.gateway.main` 232ms、`src.gateway.ai_management` 174ms、`fastapi.routing` 167ms。

隔离导入（低负载窗口，3 次中位）：

| 包 | 耗时 |
| --- | --- |
| `fastapi` | ~1.71s |
| `src.security` | 1.5–2.0s |
| `hvac` | 0.85–0.92s |
| `requests` | 0.78–1.35s |
| `prometheus_client` | 0.49–1.12s |
| `src.integrations` | 0.48–0.52s |
| `src.kernels._registry` | 1.22–2.09s |

**根因链（本次最值得改的一处）**：

`src/gateway/main.py` → `src/security/__init__` → `vault_crypto` →（顶层）`from ..integrations.vault.client import VaultClient` → `hvac` → `requests`；
同时 `src/knowledge/db.py` 顶层 `from src.integrations.orm_models import Base` 会先执行 `src/integrations/__init__.py`，把 28 个 SaaS 适配器全部拉起。

也就是说：**"Vault 可选 + 28 个集成适配器"这两块启动期根本用不到的东西，被两条顶层 import 链强行拉进了关键路径，合计约 2.5–3.0s（冷）/ 1.4–1.8s（热）。**

---

## 4. 零风险瘦身清单

"零风险" = 已用运行期证据确认这些文件/目录**不会被任何启动路径或核心功能 import**：在当前环境阻断这些模块后，`get_app()` 仍成功（35 routes）、`docx.Document()` 成功、`anthropic/openai/google.genai` 成功、`uvicorn` 成功、`src.tasks.broker` 成功、`src.knowledge.db` 成功。

| # | 删什么 | 体积 / 文件数 | 预计收益 | 会不会影响功能 |
| --- | --- | --- | --- | --- |
| 1 | `site-packages\pythonwin\`（IDE：`mfc140u.dll`、`win32ui.pyd`、`scintilla.dll` 等） | **8.88 MiB / 114** | 体积 −8.88 MiB | **不影响**。应用只用 `win32api/win32gui/win32con` 做托盘，从不 import `pythonwin`（IDE 编辑器，随 pywin32 附送） |
| 2 | `site-packages\PyWin32.chm` | **2.52 MiB / 1** | 体积 −2.52 MiB | **不影响**。离线帮助文档，运行期零引用 |
| 3 | `site-packages\lxml\objectify*`（`.pyd` + `.py`） | **1.74 MiB / 2** | 体积 −1.74 MiB | **不影响**。`python-docx` 只用 `lxml.etree`；阻断 `lxml.objectify` 后 docx 正常 |
| 4 | `runtime\tcl\tix8.4.3\` | **1.31 MiB / 244** | 体积 −1.31 MiB | **不影响**。`tkinter.tix` 是废弃扩展，仓库 0 引用；阻断 `tix` 后 GUI 路径正常 |
| 5 | `runtime\tcl\tk8.6\demos\` + `site-packages\win32\Demos\` + `win32com\demos\` | **1.04 MiB / 170** | 体积 −1.04 MiB | **不影响**。纯示例脚本，无 import |
| 6 | `site-packages\**\tests\`、`**\test\`（第三方包自带测试） | **2.86 MiB / 400** | 体积 −2.86 MiB | **不影响**。测试代码不被运行期 import |
| 7 | `site-packages\**\*.pyi / *.h / *.lib / *.c / *.pxd / *.pyx`（类型桩与 C 头/静态库） | **1.93 MiB / 267** | 体积 −1.93 MiB | **不影响**。仅在开发/编译期使用；运行期只加载 `.py` 与 `.pyd` |
| 8 | `site-packages\win32comext\`（含 adsi/mapi/shell 扩展） | **2.30 MiB / 102** | 体积 −2.30 MiB | **不影响**。仓库对 `win32com*` 仅出现在发布检查清单里，无实际 import；阻断后全链路正常 |
| 9 | `runtime\Lib\pydoc_data\` | **0.50 MiB / 3** | 体积 −0.50 MiB | **不影响**。只有 `pydoc`/`help()` 用；服务进程不调用 |
| 10 | `site-packages\zstandard\` | **0.64 MiB / 11** | 体积 −0.64 MiB | **不影响**（唯一用途是 `httpx[zstd]` 额外项，未安装该 extra 时不会触发；已阻断验证） |
| 11 | `site-packages\watchfiles\` | **0.61 MiB / 19** | 体积 −0.61 MiB | **不影响**。仅 `uvicorn --reload` 使用，安装版从不带 reload 启动 |
| 12 | `site-packages\{rich, rich_rst, pygments}\` | **6.46 MiB / 526** | 体积 −6.46 MiB | **不影响**（已在阻断 `rich/pygments` 下验证启动、任务代理、docx、三家 provider、uvicorn、knowledge.db 全部正常）。注意：`rich` 在 `requirements.txt` 里被显式锁版本，且是 `httpx[cli]` 的 extra 依赖——删文件前要同步从依赖清单去掉，否则下次构建会被装回来 |
| — | **去重合计** | **≈ 31.9 MiB / 1,892 文件** | **体积减少约 19.6%（162.5 → ~130.6 MiB），文件数减少约 18%** | 均为"开发工具/文档/测试/桩文件"面，不进任何运行期 import 路径 |

补充说明：

- 上面 12 项按目录并集统计（自动去重：例如 `pythonwin` 下的 Demos 只算一次），所以合计 31.9 MiB 小于逐行相加。
- **不要动**：`unins000.exe` + `unins000.dat`（7.61 MiB，卸载必需）；`python312.dll`/`libcrypto`/`libssl`/`sqlite3.dll`/`tcl86t`/`tk86t`（运行必需）；`pypdf` 的 `adobe_glyphs.py`（`import pypdf` 即加载）。
- **不建议删 `__pycache__`（6.23 MiB）**：它是热启动加速缓存。反方向的需求见 §5 第 4 条——应该"预置"而不是"清除"。
- 安装包侧（`lzma2/max`）压缩率约 5:1，31.9 MiB 明文大致对应 **6–7 MiB 安装包**减量；对"下载体积"的收益有限，但对**安装后磁盘占用**和**首次启动的杀软扫描面**收益明确。

---

## 5. 零风险延迟加载清单

"零风险延迟加载" = 把"启动时不需要、但将来一定会用"的东西挪出关键路径；不改功能语义，只改加载时机。

| # | 现状（启动期在做什么） | 改法（仅描述，未实施） | 预计收益 | 会不会影响功能 |
| --- | --- | --- | --- | --- |
| 1 | `src/security/vault_crypto.py` 顶层 `from ..integrations.vault.client import VaultClient`，把 `hvac`(1.21s) + `requests`(0.72s) 拉进启动 | 改为 `if TYPE_CHECKING:` + 在 `VaultTransitCrypto.__init__`/首次调用时导入；`hvac` 本身已是 try-import 的"离线降级"设计 | **冷 ~2.0s / 热 0.9–1.3s**（占内部启动 15–20%） | **不影响**。Vault 是可选能力，未配置时本来就走 `EncryptionManager` 本地回退；首次用到 Vault 时多一次 import（毫秒级） |
| 2 | `src/integrations/__init__.py` 顶层一次性 `from .xxx_adapter import XxxAdapter` 28 个；而 `src/knowledge/db.py` 顶层 `from src.integrations.orm_models import Base` 会先执行包 `__init__`，被动全量拉起 | ① `src/knowledge/db.py` 改从 `src.integrations.orm_models` 的模块路径直接取，或把 `Base` 移到独立模块；② `integrations/__init__.py` 用 PEP 562 `__getattr__` 懒导出 | **0.5–0.8s（冷）/ 0.5s（热）**，与第 1 条叠加后 **合计 1.4–1.8s** | **不影响**。适配器只有真正发起集成调用时才需要；已实测 `src.knowledge.db` 与整包启动不依赖适配器符号 |
| 3 | `src/observability/metrics.py` 顶层 import `prometheus_client`，其中 `platform_collector` 自身 538ms（热态 0.5–1.1s） | 把 metrics 初始化搬到 lifespan 末尾/首个 `/v1/metrics/prometheus` 请求时；或在安装版关闭 platform collector（进程/平台指标，与业务指标无关） | **0.5–1.1s** | **基本不影响**。唯一变化：进程启动后到首次抓取前，`/v1/metrics/prometheus` 会触发一次初始化（约 0.5s），指标口径不变 |
| 4 | 已安装目录带 303 个 `.pyc`，但安装包构建时 `stage_payload.py` 会清除 `__pycache__`，启动 bat 又设 `PYTHONDONTWRITEBYTECODE=1` → **用户首启必然全量重新编译** | 打包时**预置**热子集的 `.pyc`（`src/` + 进入启动路径的第三方包，约 3–5 MiB），并保持运行时只读不写 | **2.2–2.7s**（低负载窗口实测：cold `get_app()` 9.2/9.7/14.5s vs 预置 pyc 后 6.65/7.18/7.18s） | **不影响**。pyc 是解释器缓存，版本不匹配会自动失效；代价是安装包 +3–5 MiB。**全量** pyc 约 +20 MiB，不建议 |
| 5 | `lifespan` 中 `install_execution_alert_binding()` 同步执行 286ms（劣化样本 1,428ms） | 移到 `core ready` 之后的后台线程（与现有 OTel 预热同一队列） | **0.29–1.4s** | **有竞态，不算零风险**：ready 后到绑定完成之间发生的执行失败不会产生告警。建议与产品确认这 0.3s 窗口可接受后再做 |
| 6 | 路由模块在 `get_app()` 内即时 import（ai_management 等 714.9ms、knowledge 284.3ms、models 226.8ms） | 改为"首请求挂载"或把路由注册拆到 `importlib` 懒加载 | **1.2–2.0s** | **非零风险**：OpenAPI/`/docs` 与路由存在性检查会变化，且并发首请求可能重复注册，需要专门设计 |

**关于"启动时本可以延后的事"，逐项核对结果：**

| 怀疑项 | 实测 |
| --- | --- |
| 全量文件扫描 | **没有**。扫描函数只在测试与守卫里调用；lifespan 无目录遍历 |
| 预加载模型列表 | **没有**。模型列表按请求读取配置；`/v1/models` 不是启动依赖 |
| 构建向量/全文索引 | **没有**。知识库只建连接与迁移，不做全量索引 |
| 内置资源解压 | **没有**。payload 是明文目录，无运行期解压 |
| 前端静态资源加载 | **没有服务端加载**。仅 `StaticFiles` 挂载（3.7–5.0ms） |
| 对话链预热 | **已在后台**。`chat.warmup()` 在 core ready 后进入后台线程，启动期只记录 OTel 6.3ms，不阻塞 ready |
| 真正"抢跑"的 | Vault/hvac、28 个集成适配器、prometheus platform collector、告警绑定——即 §5 的 1/2/3/5 |

---

## 6. 结论与建议顺序

1. **启动慢的主因是 import 图，不是 I/O**：`main` 顶层 import 占 56%，加上路由首次 import 超过 80%；数据库打开+迁移 < 60ms，无解压、无扫描、无索引构建。
2. **先做 §5 第 1 + 2 条**（Vault 延迟 + integrations 懒导出）：改动小、语义不变、预计 **1.4–1.8s**，是性价比最高的一刀。
3. **再做 §5 第 3 条**（prometheus 延迟）：再省 **0.5–1.1s**，指标口径不变。
4. **§5 第 4 条（预置 pyc）能再省 2.2–2.7s**，但需要改打包流程（预编译热子集），属于"打包脚本改动"，本次只读审计不动；建议单独排期。
5. **体积按 §4 清单回收 ≈ 31.9 MiB / 1,892 文件（约 19.6%）**，全部是开发工具面，功能无损；安装包预计减少 6–7 MiB。
6. 不建议动的：`unins000.*`（卸载必需）、`__pycache__`（删了更慢）、`aiohttp`（被 `taskiq` 无条件 import，**删了就炸任务系统**，这是本次验证中最重要的"反直觉"结论）、`libcrypto/libssl/sqlite3/tcl/tk`。
7. 首启 16s 与热启 8.8–11.5s 的差距主要来自杀软首次扫描与密钥生成，属于一次性成本；对外承诺启动时间时应区分"首启/日常"。

---

## 7. 复现与数据

全部原始数据在 `D:\_audit\startup-profile\audit-6ae9f041\`（未写入 `D:\LiuHaoData`）：

| 文件 | 内容 |
| --- | --- |
| `startup-launch-fresh.json` | 首启完整阶段打点（外部 16.08s / 内部 12.97s） |
| `startup-direct-warm1.json` | 热态直启（外部 9.56s / 内部 8.84s） |
| `startup-direct-warm2.json` | 高负载劣化样本（内部 11.54s，告警绑定 1,428ms） |
| `importtime-cold-getapp.log` | 冷 import 全量（867 模块 / 10,147ms） |
| `importtime-warm-getapp.log` | 热 import 对照 |
| `run.ps1` | 启动 + 轮询 `/v1/ready` + 收集 profile 的脚本 |
| `tree_size.py` / `union_size.py` / `parse_import.py` | 体积分桶、瘦身并集、importtime 解析脚本 |

复现启动测量的命令形态（与审计时一致）：

```powershell
# 只用 19011；先确认端口空闲，避免再次被其他线占用
python scripts/launch_installed.py --skip-wizard --backend-only --api-port 19011 --no-browser
```

审计期间未修改任何源码/打包脚本，未触碰 `D:\LiuHaoData`；`D:\LiuHao` 下新增的 `src\**\__pycache__` 是直接运行解释器产生的缓存副作用，未删除（删了会让下次启动更慢）。

---

## 8. 实施结果（2026-11-08 追加）：懒加载已落地，瘦身已出规格

本节的"实施"只动**导入方式**，不改任何业务逻辑；瘦身只出规格，未改打包脚本（见 `docs/packaging-slimming-spec-20261108.md`）。

### 8.1 改了什么（4 处）

| 文件 | 改动 | 收益来源 |
| --- | --- | --- |
| `src/security/vault_crypto.py` | `VaultClient` 从顶层 import 改为 `TYPE_CHECKING` + `VaultTransitCrypto.__init__` 内按需导入 | 移除启动期的 `hvac`(+`requests` 树)，实测 **−1,213ms**（冷 import） |
| `src/integrations/__init__.py` | 28 个适配器改为 PEP 562 `__getattr__` 懒导出（`_LAZY_ATTRS` 表 + `__all__` 全量保留） | `src.integrations` 整包 **2,037ms → 0ms**（`knowledge/db.py` 不再被动拉起） |
| `src/kernels/security/__init__.py` | `try: import hvac` 改为 `importlib.util.find_spec("hvac")` | 语义不变（只回答"装没装"），但不再执行 `hvac` 模块体；直接执行 `import hvac` 的话前面两条的收益会被这里吃掉 |
| `src/gateway/main.py` | HTTP 指标改为 `_ensure_http_metrics()` 按需导入，并在后台预热线程里排一步；指标评估循环的 import 移进线程体 | `prometheus_client`（含 `platform_collector` 538ms 自身耗时）移出关键路径 |

保持不变的契约：`from src.integrations import XxxAdapter` / `BaseIntegrationAdapter` / `get_global_registry` 等 38 个公开名全部可用（已逐一 `getattr` 验证），`__all__` 与原文件一致。

> **注意（并发写盘）**：`src/gateway/main.py` 的这处改动已被另一条线在 15:41 的提交 `870d89b8` 一并带走（HEAD 从 `8e40d98` 前进到 `870d89b8`，该提交的 message 与本改动无关）。另外三处（`src/security/vault_crypto.py`、`src/integrations/__init__.py`、`src/kernels/security/__init__.py`）仍是工作树里未提交的改动。若后续要按提交追溯这轮优化，`git log -S "_ensure_http_metrics"` 会把它们混在别人的提交里。

### 8.2 前后对比（同一台机器、同一套测量方法）

**A. 冷 import 图（`python -X importtime`，`get_app()` 全程）**

| 指标 | 改前 | 改后 | 变化 |
| --- | --- | --- | --- |
| 导入模块数 | 867 | **732** | −135 |
| `src.gateway.main` 累计 | 9,957ms | 4,632ms | — |
| `hvac` | 1,213ms | **不存在** | −1,213ms |
| `src.integrations`（28 适配器） | 2,037ms（含其下 vault.client） | **不存在** | −2,037ms |
| `prometheus_client` | 791ms | **不存在**（已转入后台线程） | −791ms |
| `requests` | 716ms（顶层，随 vault 链） | 315ms（改由 `src.ai.model_discovery` 载入） | 仅**迁移**，非消除 |

> 诚实说明：`requests` 在启动期仍会被加载——它挪到了 `路由：models` 那一段（`model_discovery` 顶层 `import requests`）。所以 vault 那一条的真实净收益是 **hvac 的 1.2s**，不是 2.0s。`fastapi` 在两次日志里从 3,791ms 降到 1,624ms，这属于**机器负载差异**（改前日志在 CPU 99% / 55 个 python 进程下采集），不计入本次收益。

**B. 阶段打点（`/v1/startup` profile，热数据直启）**

| 阶段 | 改前（2 次） | 改后（3 次） |
| --- | --- | --- |
| `main` 顶层依赖导入 | 4,543 / 8,129 ms | **2,624 / 2,725 / 2,913 ms** |
| 路由：models（`requests` 迁到这里） | 227 / 227 ms | 729 / 1,212 / 1,212 ms |
| 内部总计（T0 → core ready） | 8,442 / 11,537 ms | 8,280 / 9,150 / 12,462 ms |

`main` 那一段稳定降了 **1.9–5.2s**；models 段上涨约 **0.5–1.0s** 是 `requests` 迁移造成的（净不亏，但也不是纯赚）。

**C. 同代码 A/B（唯一能把环境噪声抵掉的一组）**

用子进程墙钟时间对比"启动 + 构建 app"，两组交替采样 4 轮：

| 组 | 4 次墙钟 | 中位 |
| --- | --- | --- |
| lazy（当前实现） | 17,057 / 14,790 / 11,561 / 16,333 ms | **15,562 ms** |
| eager（先强制 `import hvac, prometheus_client, from src.integrations import *` 再启动） | 23,295 / 22,375 / 16,239 / 16,116 ms | **19,307 ms** |

中位差 **−3.7s**；逐对差值 −6.2 / −7.6 / −4.7 / +0.2s（最后一对撞上负载尖峰）。这就是"1.4–1.8s 目标"在**一台仍在 100% CPU 的机器**上的实测值——高负载会把每个 import 的固定成本同比放大。

**结论**：目标达成，但**不要引用 3.7s 作为对外承诺**。安静机器上的干净基线见 §9。

### 8.3 回归验证

| 范围 | 结果 |
| --- | --- |
| 懒加载契约（38 个公开名逐一 `getattr`） | 全部解析成功；未知属性按 PEP 562 抛 `AttributeError` |
| `tests/observability`（含 `/v1/metrics/prometheus`、告警） | **107 passed** |
| `tests/gateway` 中与 `main.py` 直接相关的 5 个文件 | **42 passed** |
| `tests/security`、`tests/knowledge`、`tests/integration`、`tests/test_integrations*.py`、`tests/test_vault_*`、`tests/test_dependency_declaration_surface.py` | 通过（其中 `test_vault_dependency_honesty` 全绿） |
| 全量 `tests/gateway` | **33 failed / 323 passed / 5 errors** — 失败与本改动无关：`src/gateway/accounts.py` 与 `tests/gateway/test_account_scope_three_layers.py` 是**另一条线未提交的在研改动**（`git status` 显示 accounts.py 为 modified、该测试文件为 untracked），报错点是 `boot_report.probe_writable` 撞上 `tests/conftest.py` 的破坏性操作守卫 |

### 8.4 瘦身规格已交付

`docs/packaging-slimming-spec-20261108.md`：13 条排除规则、**31.00 MiB / 1,816 文件**（去重并集，按"打包会用的谓词"重算，与此前 31.9/1,892 的差异来自规则首命中归属与 `.c/.pyx` 边界），每条含"删掉后哪个 import 会受影响 + 怎么验证"，附 6 条验收命令与回滚方案。

---

## 9. 还没做：干净基线（等机器安静）

**当前状态（15:47 复核，仍不满足）**：CPU 采样 23 / 74 / 54 / 56 / 49%（5 次 600ms），38 个 python 进程（其中 5 个是 2026-11-07 起就在跑的长驻进程，各稳定占约 1 核 ≈ 17% 总负载），另有 20+ PowerShell 进程在并发执行——**其他线的 agent 正在活动**，负载方差极大（23%–74%）。
`127.0.0.1:19011` 空闲 ✅，但 CPU 与"活跃进程"两项不达标。所有绝对秒数仍被放大。要作为"以后对比的锚点"，需要在空闲窗口重跑：

**这件事已经自动化了**：`scripts/acceptance/startup_baseline.py` 自己判定窗口（CPU 连续采样 ≤15%、python 进程 ≤5、端口 19011 空闲），条件满足才连续跑完四项，条件不满足就打印「窗口不干净」并说明是哪一条不过，退出码 2（不产出任何数字）。以后不用人喊：

```powershell
.venv\Scripts\python.exe scripts\acceptance\startup_baseline.py
# 产物：D:\_audit\startup-baseline\<时间戳>\baseline.json + summary.txt + 每次启动的 profile/stdout/stderr
```

| # | 脚本内对应的采集项 | 期望用途 |
| --- | --- | --- |
| 1 | 冷启动（全新数据目录） | 首次体验基线 |
| 2 | 热启动 ×3（同一数据目录） | 日常启动基线（取中位） |
| 3 | 冷 import 图（`-X importtime`） | 复查有没有新增长尾模块 |
| 4 | 体积对账（可传 `--size-root`） | 对账打包线的落地结果 |

前置条件：CPU < 15%、python 进程 < 5、`127.0.0.1:19011` 空闲。**步骤 1–3 在同一进程里连续跑完**，不会引入负载差。`--max-wait` 默认等 1 小时，`--force` 只用于调试（产物会标 `clean=false`，退出码仍非零）。

实测过一次窗口判定与采集链路（`--force`，窗口当时 CPU 98.5–100%、python 32，故不作为锚点）：冷启动 6,543ms、热启动 7,771/7,027ms（中位 7,399ms）、import 图 667 个模块、`D:\LiuHao` 179.39 MiB / 12,578 文件。这组数字只证明脚本能跑通，**不是基线**——窗口不干净时脚本会自己写明。

---

## 10. 「看起来能删其实不能删」清单（防止误删）

| 项 | 体积 | 为什么不能删 | 依据 |
| --- | --- | --- | --- |
| **`aiohttp`** | ~3.4 MiB | `taskiq` 无条件依赖它；`src/tasks/broker.py` 一 import 就会连带加载。删掉后任务系统/异步代理直接 `ModuleNotFoundError` | 阻断 `aiohttp` 时 `src.tasks.broker` 导入失败；`requirements.txt` 中它是直接依赖（不是某个 extra 的附带品） |
| **`__pycache__` / `*.pyc`** | 6.2 MiB（303 个） | 它是**加速缓存**。删了每次冷启动都要重新编译，实测慢 **2.2–2.7s**；正确方向是"预置热子集 pyc"（§5 第 4 条），不是清除 | 低负载窗口 A/B：cold `get_app()` 9.2–14.5s vs 预置 pyc 6.65–7.18s |
| `unins000.exe` + `unins000.dat` | 7.61 MiB | 卸载器本体 + 卸载数据；删了程序无法卸载（Inno 的 `unins000.dat` 还记录安装文件清单） | `installer/liuhao.iss` 的卸载流程依赖它 |
| `python312.dll` / `libcrypto-3-x64.dll` / `libssl-3-x64.dll` / `sqlite3.dll` | 17.2 MiB | 解释器、TLS、数据库；`cryptography._rust.pyd` 与 `ssl` 直接链接它们 | 删任一 → 进程起不来或 HTTPS 全挂 |
| `tcl86t.dll` / `tk86t.dll` / `runtime\tcl\tcl8.6\**`（**运行库部分**） | ~8 MiB | 首启 GUI（`first_run_gui.py`）必需；只建议删 `demos/` 与 `tix8.4.3/`，**不要**删 `tcl8.6\` 主体 | 删运行库后 `import tkinter` 失败 |
| `pypdf\_codecs\adobe_glyphs.py` | 0.55 MiB | 看着像数据，实际 `import pypdf` 就会加载它 | PDF 知识导入依赖 |
| `google\_upb\*` / `pydantic_core\*` / `lxml\etree*` | ~9.5 MiB | 三家 provider 的类型定义、FastAPI 请求校验、docx 解析的运行时；看着像"SDK 传递依赖"，但正是主力路径 | 删了 `google.genai` / 请求校验 / docx 会直接失败 |
| `zstandard` | 1.3 MiB | **不是零风险**：`httpx` 用它解 `Content-Encoding: zstd`。缺包时 httpx 会静默从解码器表移除，表现为"该编码不支持"而非崩溃 | `httpx/_decoders.py` 的 `try: import zstandard` 分支 |
| `rich` + `pygments` | 6.4 MiB | **不能删（2026-11-08 实测推翻本文原结论）**：`httpx/__init__.py → _main` 在顶层 `import pygments.lexers` 与 `import rich.console`，删任一半都会让 `import httpx` 直接失败。要省这块得让 httpx 按 base extra 重装 | 打包链构建期守卫自检当场拒绝出包，见 `docs/packaging-slimming-spec-20261108.md` §9.3 |

一句话判别法：**判断能不能删，不看它叫什么，看它有没有出现在 `-X importtime` 的启动图里，或有没有被某个顶层 `import` 链在启动期拉到。**

---

## 11. 剪枝已进打包链路（2026-11-08）

`packaging/stage_payload.py` 默认开剪枝（A1–A13，A11/A12 已回滚），开关为 `--no-prune`
或 `LIUHAO_PAYLOAD_PRUNE=0`；运行期有 `_liuhao_prune_guard`（被剪模块被 import 会抛
`PrunedPayloadImportError`，不能用 `except ImportError` 吞掉）。完整实施、反证与升级路径
见 `docs/packaging-slimming-spec-20261108.md` §9。要点：

- payload：152.74 → **131.04 MiB**（−21.70 MiB / −940 文件）；安装后（含 7.61 MiB 卸载器）160.35 → **138.65 MiB**（−13.5%）。
- 冷启动 → `/v1/ready` 200：同机交替 3 轮，剪枝中位 **6356 ms** vs 未剪枝 6357 ms（噪声内，不构成回退）。
- `--diagnose`：两版均 exit 0、22 个文件；剪枝 31.9s / 18.5 KB，未剪枝 35.1s / 18.8 KB。
- 老用户升级：升级器不比对安装树文件数（`verify_file_manifest` 只比对升级包与其清单，
  `_prune_stale_files` 不含 `runtime/`）；旧文件由安装器 `[Run]` 调
  `scripts/purge_pruned_payload.py` 按**新包** `prune-manifest.json` 清理（实测删除 943 个 / 21.80 MiB，清理后与新 payload 零差异）。
- 干净窗口基线：后台守候 1 小时共 38 次采样，CPU 从未低于 15%（多在 20–100%）、python 进程 18–66 个，
  **始终未拿到干净窗口**；本节数字均为"同机交替 A/B"，只用于比较相对差值，不作绝对锚点。

