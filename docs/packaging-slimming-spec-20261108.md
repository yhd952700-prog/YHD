# 打包瘦身规格：零风险排除清单（交给打包线落地）

- 出稿日期：2026-11-08
- 来源：`docs/audit-size-startup-20261108.md`（只读审计）
- 适用对象：**只改 payload 组装**（`packaging/stage_payload.py` + `installer/liuhao.iss` 的 payload 源），**不改运行期源码语义**
- 实测基线：已安装目录 `D:\LiuHao` = 10,303 文件 / **162.00 MiB**；本清单可回收 **31.00 MiB / 1,816 文件（≈19%）**

> 本规格没有实施任何打包改动（审计期只读）。下面每条都给"删掉后哪个 import 会受影响 + 怎么验证它没被用到"，请打包线按顺序落地并跑第 5 节验收。

## 1. 判定方法（为什么叫"零风险"）

用 `importlib` 元路径钩子在**真实运行时**里阻断这些模块，然后跑完整链路，全部通过：

| 冒烟项 | 结果 |
| --- | --- |
| `src.gateway.main.get_app()` | 成功，35 条路由 |
| `src.tasks.broker`（任务系统） | 成功 |
| `src.knowledge.db`（知识库 ORM） | 成功 |
| `docx.Document()`（.docx 导入） | 成功 |
| `anthropic` / `openai` / `google.genai` | 成功 |
| `uvicorn` 启动 | 成功 |

同时全仓 grep 确认：`win32ui`、`pythonwin`、`win32com.client`、`win32comext`、`lxml.objectify`、`tkinter.tix`、`rich`、`pygments` 在 `src/`、`scripts/`、`installer/` 里**零引用**（命中项全是 `embedded`/`added` 这类子串噪声）。

## 2. 排除规则（按首命中归属，逐行相加 = 去重并集）

### A1 · pythonwin IDE 及其 GUI 扩展 —— 8.88 MiB / 114 文件

```
runtime\Lib\site-packages\pythonwin\**          （整目录删除）
```

- **删掉后受影响的 import**：`import pythonwin`、`pythonwin.pywin.*`、`import win32ui`（`win32ui.pyd` 就在这个目录里）、`dde`。
- **为什么不影响**：应用只用 `win32api` / `win32gui` / `win32con` / `win32security` / `win32file` / `win32pipe`（都在 `site-packages\win32\`，本规则不碰），托盘由 `scripts/liuhao_desktop.py` 用这几个模块实现；`pythonwin` 是 pywin32 附送的编辑器 IDE，仓库零引用。
- **怎么验证**：阻断 `pythonwin` 与 `win32ui` 后跑第 5 节 E1–E4；再直接 `python -c "import win32gui, win32api; print('ok')"`。

### A2 · PyWin32 离线帮助 —— 2.52 MiB / 1 文件

```
runtime\Lib\site-packages\PyWin32.chm     （文件名忽略大小写）
```

- **受影响 import**：无（`.chm` 不是模块，只有 `win32help`/`pydoc` 类工具会读它）。
- **验证**：删除后 `python -c "import win32api"` 正常即可；本应用无帮助入口。

### A3 · Demos 示例目录（含 tix/tk 的 demo）—— 1.49 MiB / 302 文件

```
runtime\Lib\site-packages\pythonwin\pywin\Demos\**    （已被 A1 覆盖，规则写在前，不重复计费）
runtime\Lib\site-packages\win32\Demos\**              0.26 MiB / 74
runtime\Lib\site-packages\win32com\demos\**           0.06 MiB / 11
runtime\Lib\site-packages\win32comext\*\demos\**      （已被 A7 覆盖）
runtime\tcl\tix8.4.3\demos\**                          0.24 MiB / 80
runtime\tcl\tk8.6\demos\**                             0.72 MiB / 85
```

- **受影响 import**：无。`Demos`/`demos` 是小写目录里的示例脚本，没有任何 `__init__.py` 之外的入口被运行期 import。
- **验证**：删除后跑 tkinter 首启 GUI（`python -c "import tkinter; tkinter.Tk(); print('ok')"`，无显示环境可只做 `import tkinter` + `tkinter.Tix` 以外的导入）。

### A4 · lxml.objectify —— 1.74 MiB / 2 文件

```
runtime\Lib\site-packages\lxml\objectify.cp312-win_amd64.pyd
runtime\Lib\site-packages\lxml\objectify.py  （如存在）
```

- **受影响 import**：`lxml.objectify`（显式导入才加载，`lxml.etree` 不依赖它）。
- **为什么不影响**：知识导入只用 `python-docx` → `lxml.etree`；仓库零处 `objectify`。
- **验证**：阻断 `lxml.objectify` 后 `docx.Document()` 必须成功（已在审计中做过）；再用一份真实 `.docx` 走一次知识导入。

### A5 · Tcl/Tix 扩展 —— 1.08 MiB / 164 文件（另有 0.24 MiB 已计入 A3）

```
runtime\tcl\tix8.4.3\**    （整目录）
```

- **受影响 import**：`tkinter.tix`。
- **为什么不影响**：`tix` 自 Python 3.7 起废弃，`first_run_gui.py` 只用 `tkinter` + `ttk`；仓库零引用。
- **验证**：删除后 `python -c "import tkinter, tkinter.ttk, tkinter.messagebox; print('ok')"`；再跑一次首启 GUI 的 dry-run。

### A6 · win32comext 扩展包 —— 2.14 MiB / 74 文件

```
runtime\Lib\site-packages\win32comext\**   （整目录）
```

- **受影响 import**：`win32comext.*`（adsi / mapi / shell / taskscheduler / bits / authorization / axscript / propsys / directsound）。
- **为什么不影响**：这些是 COM 扩展，仓库零引用；托盘/文件/管道路径只依赖 `win32*` 核心模块与 `pythoncom312.dll`（后者在 `pywin32_system32\`，本规则不碰）。
- **验证**：阻断 `win32comext` 后 E1–E4 全绿；并确认 `import pythoncom` 仍成功（它只需要 core DLL）。

### A7 · pywin32 的 adodbapi / isapi —— 0.46 MiB / 43 文件

```
runtime\Lib\site-packages\adodbapi\**
runtime\Lib\site-packages\isapi\**
```

- **受影响 import**：`adodbapi`（ADO 数据库桥）、`isapi`（IIS 扩展）。
- **为什么不影响**：本应用数据库是 SQLite（`sqlite3.dll`），从不走 ADO；`isapi` 是 IIS 专用，安装版是独立进程。
- **验证**：删除后 `src.knowledge.db` 连接 + 迁移成功（E3）。

### A8 · 第三方包自带 tests/test —— 1.92 MiB / 317 文件

```
runtime\Lib\site-packages\**\tests\
runtime\Lib\site-packages\**\test\
```

实测命中目录（14 个）：`greenlet\tests`、`win32\test`、`win32com\test`、`adodbapi\test`（已被 A7 覆盖）、`aiosqlite\tests`、`google\genai\tests`、`certifi\tests`、`win32comext\*\test`（已被 A6 覆盖）、`isapi\test`（已被 A7 覆盖）。

- **受影响 import**：无。这些目录没有被任何 `__init__.py` 链在运行期导入。
- **注意**：本规则**不要**扩大到 `sqlalchemy\testing`（那是 `sqlalchemy.testing` 模块，虽然应用不用，但它不是 `tests` 目录，规则自然不命中）。
- **验证**：删除后 E1–E4；再跑 `pytest tests/` 的主干子集（测试跑在 `.venv`，不依赖 payload）。

### A9 · pydoc_data —— 0.50 MiB / 3 文件

```
runtime\Lib\pydoc_data\**
```

- **受影响 import**：`pydoc` / `help()` 的 HTML 文档数据。
- **为什么不影响**：服务进程不生成文档页；`/docs` 是 FastAPI Swagger，不用 pydoc。
- **验证**：删除后 E1（服务能起、`/openapi.json` 可访问）。

### A10 · zstandard —— 1.31 MiB / 7 文件

```
runtime\Lib\site-packages\zstandard\**
```

- **受影响 import**：`httpx._decoders` 的可选 zstd 解压分支。
- **风险边界（这条不是"零风险"，是"低风险"）**：若某上游（LLM provider / webhook）返回 `Content-Encoding: zstd`，httpx 会抛 `DecodingError`；gzip/br 不受影响。实测 `httpx` 对 zstd 是 `try/except ImportError → 从 SUPPORTED_DECODERS 移除`，所以**缺包时表现为"不支持该编码"而非崩溃**。
- **验证**：删除后跑一次真实对话（三家 provider 任一）+ 一次 webhook 投递；若要更稳，保留 `zstandard`、只删 `watchfiles`。

### A11 · watchfiles —— 0.65 MiB / 10 文件

```
runtime\Lib\site-packages\watchfiles\**
```

- **受影响 import**：`uvicorn.supervisors.watchfilesreload`，仅 `uvicorn --reload` 使用。安装版启动器**从不**带 `--reload`。
- **验证**：删除后 E1（`scripts/launch_installed.py` 路径能起服务）；不需要验证热重载。

### A12 · rich + pygments —— 6.38 MiB / 513 文件

```
runtime\Lib\site-packages\rich\**
runtime\Lib\site-packages\pygments\**
runtime\Lib\site-packages\rich_rst\**   （如存在）
```

- **受影响 import**：`rich`、`pygments`（`rich` 无条件依赖 `pygments`，是唯一的无条件反向依赖）。
- **为什么不影响**：仓库零引用；它们只被 `httpx[cli]` / `rich-rst` 这类开发向 extra 使用，不在运行期代码路径。
- **必须在打包清单里同步移除**：`requirements.txt` 里 `rich==15.0.0`、`rich-rst==2.1.0`、`pygments==2.21.0` 是显式锁定的，否则下次构建会被 `pip install` 装回来。
- **验证**：阻断 `rich`+`pygments` 后 E1–E4（审计已做过一轮，全绿）。

### A13 · 类型桩 / C 头 / 静态库 —— 1.91 MiB / 266 文件

```
runtime\Lib\site-packages\**\*.pyi
runtime\Lib\site-packages\**\*.h
runtime\Lib\site-packages\**\*.lib
runtime\Lib\site-packages\**\*.c
runtime\Lib\site-packages\**\*.pxd
runtime\Lib\site-packages\**\*.pyx
```

主要来源：`lxml\includes\**`（libxml/libxslt 头文件 0.81 MiB）、`win32com\libs`、`greenlet\platform`、`google\api`/`google\type` 的 `.pyi`、`cryptography\**\_rust\openssl`。

- **受影响 import**：无。解释器运行期只加载 `.py` 与 `.pyd`；`.pyi` 只在类型检查/补全用。
- **注意**：`google\type\*.pyi` 删除不影响 `google.genai` 的 pb2 运行时（pb2 用 `.py` + `_upb`）。
- **验证**：删除后 E4（三家 provider 导入 + 一次真实调用）。

## 3. 汇总

| 规则 | 内容 | MiB | 文件 |
| --- | --- | --- | --- |
| A1 | pythonwin IDE | 8.88 | 114 |
| A2 | PyWin32.chm | 2.52 | 1 |
| A3 | Demos（win32/tk/tix） | 1.49 | 302 |
| A4 | lxml.objectify | 1.74 | 2 |
| A5 | tcl tix8.4.3 | 1.08 | 164 |
| A6 | win32comext | 2.14 | 74 |
| A7 | adodbapi + isapi | 0.46 | 43 |
| A8 | 第三方 tests/test | 1.92 | 317 |
| A9 | pydoc_data | 0.50 | 3 |
| A10 | zstandard | 1.31 | 7 |
| A11 | watchfiles | 0.65 | 10 |
| A12 | rich + pygments | 6.38 | 513 |
| A13 | 类型桩 / C 头 / 静态库 | 1.91 | 266 |
| **合计（去重并集）** | | **31.00** | **1,816** |

- 安装后体积：**162.00 → ≈131.0 MiB（−19.1%）**
- 安装包（`lzma2/max`，压缩比约 5:1）：**33.2 → ≈27 MiB**
- 附带收益：首次启动的杀软扫描面减少 1,816 个文件（首启 pre-T0 曾实测 3.1s，主要就是扫描新文件）

## 4. 落地方式（建议，不强制实现细节）

1. **目录级**：扩展 `packaging/stage_payload.py` 的 `STDLIB_SKIP_DIRS`（`pydoc_data`）与复制时的 `_IGNORE`（A1/A3/A6/A7/A8/A9/A10/A11/A12 的目录模式）。
2. **扩展名级**：A13 用 `shutil.ignore_patterns("*.pyi","*.h","*.lib","*.c","*.pxd","*.pyx")`，但**必须限定在 `runtime/Lib/site-packages` 子树**，避免误删 `runtime/Lib` 其他位置的偶发 `.c`。
3. **顺序**：先 A1–A9、A11、A13（真正零风险），再 A12，最后评估 A10。每加一批跑一次第 5 节验收。
4. **manifest 断言**：`manifest.json` 里加一条"被排除路径不得出现"，防止后续依赖升级把 `pythonwin`/`rich` 又带回来。

## 5. 验收（每次改完必须全绿，缺一不可）

| # | 命令 | 期望 |
| --- | --- | --- |
| E1 | `python scripts/launch_installed.py --skip-wizard --backend-only --api-port 19011 --no-browser`，轮询 `/v1/ready` | 200，且 `/v1/health` 200 |
| E2 | `python -c "import win32gui, win32api, win32con, pythoncom; print('ok')"`（用 payload 里的 runtime） | 打印 ok（托盘依赖） |
| E3 | `python -c "import src.knowledge.db"` + 触发一次迁移 | 无异常 |
| E4 | `python -c "import docx, pypdf, anthropic, openai, google.genai, sqlalchemy, taskiq; print('ok')"` | 打印 ok |
| E5 | 真实对话一次（任一 provider）+ 一次 `.docx` 知识导入 | 均成功 |
| E6 | 首次启动 GUI（`scripts/first_run_gui.py`）能弹出窗体 | 能（验证 tcl/tk 未被误删） |

> E2/E4 的 `python` 必须是 **payload 里的** `runtime\python.exe`，不是构建机 venv——否则验证的是开发环境，不是安装后环境。

## 6. 明确不要删（反直觉清单，见审计报告 §"看起来能删其实不能删"）

| 项 | MiB | 为什么不能删 |
| --- | --- | --- |
| `aiohttp` | 3.4 | 被 `taskiq` 无条件 import，删了任务系统直接 `ModuleNotFoundError` |
| `__pycache__`（运行期产生的 `.pyc`） | 6.2 | 删了每次冷启动重新编译，实测慢 2.2–2.7s；正确做法是"预置热子集 pyc"而不是删 |
| `unins000.exe` + `unins000.dat` | 7.61 | 卸载器本体与数据，删了无法卸载 |
| `python312.dll` / `libcrypto-3-x64.dll` / `libssl-3-x64.dll` / `sqlite3.dll` | 17.2 | 解释器 / TLS / 数据库 |
| `tcl86t.dll` / `tk86t.dll` / `runtime\tcl\tcl8.6\**` | ~8 | 首启 GUI 必需（只删 demos/tix，不删运行库） |
| `pypdf\_codecs\adobe_glyphs.py` | 0.55 | `import pypdf` 立即加载，删了 PDF 导入失败 |
| `google\_upb\*` / `pydantic_core\*` / `lxml\etree*` | ~9.5 | 三家 provider / 请求校验 / docx 的运行时 |

## 7. 回归与回滚

- 每条规则独立可回滚：它们都只是复制阶段的排除，恢复原 `_IGNORE` 即可。
- 建议在 payload 构建后附一份 `pruned-paths.json`（记录被排除的路径与体积），便于日后对账与回滚定位。
- 若出现"某个可选功能报 `ModuleNotFoundError`"，优先怀疑 A10（zstd 解压）与 A12（rich 输出），把该条从排除表移除即可，其余 11 条不受影响。

## 8. 实测验收（2026-11-08，剪枝只在临时副本上做）

执行：`.venv\Scripts\python.exe scripts/acceptance/prune_payload_verify.py --work-dir D:\_audit\prune-20261108`

副本为 `dist\perf-20261108\payload\app` 的完整拷贝（`dist\` 本体未改动），复制耗时 98.5s。

**体积实测**

| 项目 | 文件数 | MiB |
| --- | --- | --- |
| 剪枝前 | 11,469 | 167.72 |
| 剪枝后 | 9,362 | **134.95** |
| 实际省下 | **−2,107** | **−32.77** |

逐条（首命中归属，与实际删除量一致）：

| 规则 | 实测 MiB | 文件 | 与 §2 预估的差异 |
| --- | --- | --- | --- |
| A1 pythonwin | 10.00 | 218 | 预估 8.88/114：本次按整个 `pythonwin\` 目录删，含 `pywin\` 子目录 |
| A2 CHM | 2.52 | 1 | 一致 |
| A3 Demos | 2.06 | 411 | 预估 1.49/302：本次含 tix8.4.3\demos 与 tk8.6\demos |
| A4 objectify | 1.74 | 2 | 一致 |
| A5 Tix 运行库 | 1.08 | 164 | 一致（demos 已先被 A3 拿走） |
| A6 win32comext | 2.69 | 133 | 预估 2.14/74：本次删整个包（demos 已归 A3） |
| A7 adodbapi/isapi | 0.68 | 67 | 预估 0.46/43：payload 里的包比已安装目录更完整 |
| A8 第三方 tests | 2.43 | 385 | 预估 1.92/317：payload 多带了几个包的 tests |
| A9 pydoc_data | 0.50 | 3 | 一致 |
| A10 zstandard | 1.31 | 6 | 一致 |
| A11 watchfiles | 0.65 | 10 | 一致 |
| A12 rich+pygments | 5.53 | 444 | 预估 6.38/513：部分文件已被 A8/A13 先命中 |
| A13 类型桩/源码 | 1.59 | 263 | 预估 1.91/266：`.pyi` 部分已随 A8 删除 |

合计 **32.77 MiB**，比规格里的 31.00 MiB 多 1.77 MiB。差异原因只有一个：§2 的 31.00 是在**已安装目录**（10,341 文件 / 162.51 MiB）上算的，本次剪的是 **payload**（11,469 文件 / 167.72 MiB）——payload 里多带的 1,000+ 文件正好落在这些规则里。

**E1–E6 结果：全部通过（全量阶段 52.8s）**

| 验收项 | 结果 |
| --- | --- |
| E1 网关 | `/v1/ready` 200（874ms，进程内 uvicorn），19 个就绪检查全 healthy |
| E2 PyWin32 | `win32api / win32gui / win32con / pythoncom` 全部可导入 |
| E3 知识库 ORM | `src.knowledge.db` 可导入，网关启动即完成迁移 |
| E4 关键 import | `src.tasks.broker`、`google.genai`、`lxml.etree`、`pydantic_core`、`docx`、`pypdf`、`httpx`、`hvac` 等 21 项全通过 |
| E5 对话 + 知识库 | Ollama `qwen2.5:3b` 真实回复「可以」；`.docx` 上传 200（1 chunk/1 vector），检索命中 1 条 |
| E6 首启 GUI | `tkinter` 建窗/销毁成功（`tkinter.tix` 已删但不受影响） |

**归因机制已验证**：规则按 A1→A13 累积应用，每加一条就跑一次 `--phase imports`（含 `import src.gateway.main`，覆盖整张启动 import 图）。本次 13 步全部通过，`culprits` 为空；若某步失败，脚本会立刻把该规则的文件从源复制回来（`pruned-paths.json` 记了每条规则删掉的路径），并继续验证其余规则。单独回退用 `--restore A1,A5`。

> 注意：E1 的 874ms 是**功能验证**（进程内起 uvicorn），不是启动基线；启动基线用 `scripts/acceptance/startup_baseline.py`。两者分母不同，不要混着引用。
>
> **更正（2026-11-08 实施阶段发现）**：上一节的 E1–E6 对 A11/A12 的结论不成立。E1 用的是
> `scripts/launch_installed.py`，它按 `--backend-only` 起 uvicorn 时没有走
> `uvicorn.supervisors` 的顶层 import；而真实 `import uvicorn` 会在
> `uvicorn/supervisors/__init__.py` 顶层 `from watchfiles import watch`。
> A12 同理：`httpx/__init__.py → _main` 顶层 `import pygments.lexers` 与 `import rich.console`。
> 这两条在打包链路里由**构建期守卫自检**当场拦下，已回滚（见 §9.3）。

## 9. 剪枝已进打包链路（2026-11-08 实施与验收）

### 9.1 开关与位置

| 项 | 值 |
| --- | --- |
| 默认 | **开**（`build(prune=True)`） |
| 关闭 | `python packaging/stage_payload.py --no-prune --out <dir>`，或环境变量 `LIUHAO_PAYLOAD_PRUNE=0` |
| 回滚粒度 | 回滚单条 = 把 `PRUNE_RULES` 里该条 `enabled` 置 `False`，重建即可（不改复制逻辑） |
| 全量回滚 | `--no-prune`；此时仍会写 `prune-manifest.json`（`enabled=false`），升级清理步骤空转 |
| 产物 | `app/prune-manifest.json`（`enabled/version/rules/paths/modules/stats`）、`app/runtime/Lib/site-packages/_liuhao_prune_guard.py` + `liuhao_prune_guard.pth`、`app/prune-import-ledger.jsonl`（运行时台账，缺失可忽略） |
| manifest.json | 新增 `prune: {enabled, files, freed_mb, by_rule, guard_check}` |

### 9.2 运行期守卫（"被剪文件被 import 必须报错"）

`.pth` 在 `site` 启动时 `import _liuhao_prune_guard`，后者把 `_PruneFinder` 插到
`sys.meta_path[0]`。命中 `prune-manifest.json` 的 `modules` 时：

* `mode="block"` → 抛 `PrunedPayloadImportError(RuntimeError)`：**不是** `ImportError` 子类，
  因此 `try: import x except ImportError:` 吞不掉，也不会被"可选依赖缺失"的降级分支静默；
* `mode="observe"`（A10 zstandard）→ 抛 `PrunedOptionalImportError(ImportError)`，允许调用方降级，
  同时 stderr 打 `evt=prune.module_imported`、写台账。

构建期还有一道 `_guard_self_check`：用 payload 自己的 `runtime\python.exe` 试导一个 block 模块
与一个 observe 模块，**不按预期抛错就 `SystemExit` 拒绝出包**。

实测反证（payload 内解释器，非构建机 venv）：

```
> runtime\python.exe -c "import pythonwin"
_liuhao_prune_guard.PrunedPayloadImportError: 被剪枝的模块 pythonwin 被导入（规则 A1，来源 (目录级规则)），
该文件不在安装包中。这不是可选依赖缺失：请修正打包规则，或用 --no-prune 重新打包。      exit=1

> runtime\python.exe -c "try:\n import pythonwin\nexcept ImportError:\n print('SWALLOWED')"
同一异常直接冒出（没有打印 SWALLOWED）                                                  exit=1

> runtime\python.exe -c "import zstandard"
_liuhao_prune_guard.PrunedOptionalImportError: ...（规则 A10）                          exit=1

> runtime\python.exe -c "import rich, pygments, watchfiles; print('rolled-back OK')"
rolled-back OK                                                                          exit=0

台账 prune-import-ledger.jsonl：
evt=prune.module_imported module=pythonwin rule=A1 source=(目录级规则) mode=block
evt=prune.module_imported module=zstandard rule=A10 source=runtime/Lib/site-packages/zstandard/__init__.py mode=observe
```

### 9.3 必须回滚的两条规则（构建期守卫当场抓出）

| 规则 | 触发点 | 结论 |
| --- | --- | --- |
| A11 watchfiles | `import uvicorn` → `uvicorn/supervisors/__init__.py` 顶层 `from watchfiles import watch` | `enabled=False`，保留 0.65 MiB |
| A12 rich + pygments | `import httpx` → `httpx/__init__.py` → `_main` 顶层 `import pygments.lexers` / `import rich.console` | 整条 `enabled=False`，保留 5.53 MiB。要真省这块得让 httpx 按 base extra 重装（依赖面改动，不在本轮） |

另修一处规则实现缺陷：`_module_of()` 原来会把 `foo/__init__.pyi` 推导成模块 `foo`，
导致删类型桩时误封 `orjson`（fastapi 的 `import_module("orjson")` 会撞守卫）。
现在只有 `.py`/`.pyd` 推导模块名，`.pyi/.pxd/.pyx` 一律不推导。

### 9.4 三项回归（前后对比）

构建口径：payload 从构建机 `.venv` 组装（uv 管理的 venv 会在 wheel RECORD 里带 `.pyc`，
发布链 `prepare_release_venv.ps1` 是空 `venv + pip`，不带）。为贴近发布形态，下文数字
在剔除 `__pycache__`/`.pyc` 后测量；两组相差 5.7 MB 的 `.pyc` 在**两版**里都有，不影响差值。

| 回归项 | 剪枝前（`--no-prune`） | 剪枝后（默认） | 结论 |
| --- | --- | --- | --- |
| payload 文件 / 体积 | 9,969 文件 / 152.74 MiB | **9,029 文件 / 131.04 MiB** | −940 文件 / **−21.70 MiB** |
| 安装后体积（payload + `unins000.exe/.dat` 7.61 MiB） | 160.35 MiB | **138.65 MiB** | **−13.5%** |
| 冷启动 → `/v1/ready` 200（同机交替 3 轮） | 6357 / 5804 / 6397 ms（中位 6357） | 6387 / 6356 / 5818 ms（中位 **6356**） | −1 ms，落在噪声内（另有含 `.pyc` 的首批 2 轮：剪枝 6759/5830 vs 未剪枝 7442/6371） |
| `--diagnose` 出包 | exit 0，35.1s，22 个文件 / 18.8 KB | exit 0，31.9s，22 个文件 / 18.5 KB | 均正常 |

按构建日志，规则命中 1,221 文件 / 24.13 MiB（含将被发布链排除的 2.4 MiB `.pyc`）；
对应发布形态的差值是上表的 940 文件 / 21.70 MiB。逐条：A1 218/10.00、A2 1/2.52、A3 411/2.06、
A4 2/1.74、A5 164/1.08、A6 99/2.57、A7 50/0.44、A8（并入复制阶段）/0、A9 3/0.50、A10 6/1.31、
A13 267/1.92；A11、A12 已回滚不计。

`verify_payload_clean.py` 在剔除 `.pyc` 的两棵树上**全绿**；不剔除时唯一命中项是
`__pycache__/.pyc`（本机构建 venv 的产物，与剪枝无关，两类 payload 命中的都是同一类）。

### 9.5 升级路径（老用户从"未剪枝版"升到"剪枝版"）

两处机制，互不冲突：

1. **升级器不会把"少文件"判成异常**：`src/upgrade/filemanifest.py::verify_file_manifest`
   只比对**升级包与其自带清单**（同一份 `release.files`），不看安装目录里的额外文件；
   `src/upgrade/manager.py::_prune_stale_files` 只遍历 `INSTALL_ROOTS`
   （`scripts/config/console/src/docs/...`，**不含 `runtime/`**），因此既不会误删剪枝文件，
   也不会因为"文件数少了"报错。
2. **旧文件由安装器清**：`installer/liuhao.iss` 的 `[Files]` 只增不删，所以新增了
   `[Run] {app}\runtime\python.exe {app}\scripts\purge_pruned_payload.py --root {app}`。
   它读**新包**的 `prune-manifest.json`（安装时已覆盖到 `{app}`），只删 `runtime/` 下清单内路径，
   只 `unlink` + `rmdir` 空目录，绝不递归删除；`enabled=false` 或无清单时是空转。
   该 `[Run]` 不带 `RunOnceId`，否则后续升级不会再次执行。

实测（模拟升级：旧树 = `--no-prune` 全量 payload → 覆盖新 payload → 跑 purge）：

```
旧树            9,969 文件 / 152.74 MiB
覆盖后          9,969 文件（残留未清）
purge 输出      删除 943 个 / 21.80 MiB；已在旧树中缺失 278 个（对应发布链本就没有的 .pyc 路径）；exit=0
清理后          9,029 文件 / 131.04 MiB
与「新 payload」逐文件 diff：仅存在于旧树的文件 = 0；仅存在于新 payload 的文件 = 0（剔除 purge 自身运行产生的 9 个 __pycache__）
```

### 9.6 "整目录删除"的引用核查

| 规则 | 被删目录 | 引用核查结论 |
| --- | --- | --- |
| A1 | `runtime/Lib/site-packages/pythonwin/**` | `src` / `scripts` / `config` / `installer` / `packaging` 全文 grep `pythonwin|win32ui|pywin` 无 import；托盘只用 `win32api/win32gui/win32con/win32file/win32pipe` |
| A3/A5 | `pythonwin/pywin/demos`、`win32/demos`、`win32com/demos`、`tix8.4.3/demos`、`tk8.6/demos`、`tix8.4.3/**` | 无任何代码路径按目录名访问；首启 GUI 只用 `tkinter`（模块级），不加载 tix |
| A6 | `runtime/Lib/site-packages/win32comext/**` | `win32com/__init__.py` 会 `__path__.append(<site>/../win32comext)`；目录不存在时这次 append 找不到任何模块，无害。无 `import win32com.shell/mapi/axscript` 等直接引用；`win32com.client` 仍在 `win32com/` 内 |
| A7 | `adodbapi/**`、`isapi/**` | `adodbapi/*.py` 之间是相对导入，整包删除无残留引用；无 DSN/ADO 代码路径 |
| A9 | `pydoc_data/**` | 只有 `pydoc`/`pydoc_data` 自身使用；`scripts/start_liuhao.py --diagnose` 走 `src.desktop.diagnostics`，不 import pydoc |
| 第三方插件 | — | 内置插件在 `src/plugins`，无上述 import；**第三方插件**若真去 import 被剪模块，会撞守卫抛 `PrunedPayloadImportError`（这是期望行为：响亮失败） |

诊断/托盘两条关键路径已实测：`--diagnose` 两版均 exit 0（§9.4），托盘依赖 E2 在上一轮已验证。

