# 安装包 / 升级包内容核对（2026-11-08）

> 只读审计。未修改 `src/`、`apps/`、`installer/`、`packaging/` 下任何文件，未重新打包。
> 审计对象：`dist\installer\LiuHao-Setup-0.1.0.exe`、`dist\installer\liuhao-0.1.0.zip`。
> 审计方式：把 exe 静默安装到 `D:\_audit\pkg-inspect`（`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /MERGETASKS="!desktopicon,!launchnow" /DIR= /DATA=`）后逐文件核对，核对完删除；zip 用 7-Zip 解到 `D:\_audit\zip-inspect` 后逐文件核对。
> 未触碰 `D:\LiuHao`、`D:\LiuHaoData` 与桌面快捷方式；为让 `/DIR` 生效临时导出并恢复了 HKCU 卸载登记项（恢复值与审计前一致）。

审计对象指纹（与 `DELIVERY.txt` 一致）：

| 文件 | 字节数 | SHA256 | 构建时间 |
| --- | ---: | --- | --- |
| `LiuHao-Setup-0.1.0.exe` | 34,731,647 | `01BE18BBE4C07923486FB27BA59C23C23F42A6E86F89C8967B1F86C0DEC64DA2` | 2026-11-07 11:14:33 |
| `liuhao-0.1.0.zip` | 2,638,893 | `0A04AC6ADCC299323B4B92A9BC1040A682F5C1DC3601A5FEF94A8C81FFA48512` | 2026-11-07 11:13:47 |

**口径说明（重要）**：这两个包是 **0.1.0 / 提交 f74fa14e** 的产物；当前仓库已经是 **0.1.1**（`installer/liuhao.iss` 默认 `AppVersion=0.1.1`，工作树 228 个未提交路径）。下文“应该有什么”以**当前** `installer/liuhao.iss`、`packaging/stage_payload.py`、`packaging/PAYLOAD-CONTENT.md` 为准；同时注明“f74fa14 当时就是这样”的项，避免把版本漂移误判成打包事故。

---

## 0. 结论（先看这里）

1. **0.1.0 的 exe 与 zip 是同一份 app 快照**：zip 里 576 个可比文件中 **575 个逐字节一致**，唯一差异是 `console\console-contract.json` 的 `built_at` 时间戳（`console_fingerprint` 相同）；`LIUHAO_RELEASE.json` 只在 zip 里。但 **zip 不是“整包”**：它没有 `runtime\`、`licenses\`、`THIRD-PARTY-NOTICES.txt`、卸载器、`data-dir.txt`，单独解压**不能运行**。
2. **按当前打包脚本口径，静态文件缺 14 项**：3 个脚本（`first_run_gui.py`、`purge_installed_config.py`、`collect_diagnostics.bat`）、9 个许可证文本（adodbapi/LGPL/iconv/Tix/Unicode/zlib/expat/libmpdec 等）、`README`（只有 `START-HERE.txt`）、zip 里的 `docs/`。另有 **1 项交付件缺失**（外部 `LIUHAO_RELEASE.json` 发布清单）、**3 项元数据/规格不达标**（exe `FileVersion`/`Copyright`/`OriginalFilename` 未设、图标只有单帧 192×192、`data-dir.txt` 无 BOM 与文档说法不符）。
3. **多出来 8 个配置文件 + 344 个第三方测试文件**：`config\development.yaml`、`testing.yaml`、`crontab`、`supervisor.conf`、`nginx.conf`、`prometheus.yml`、`grafana-dashboard.json`、`alert-rules.yaml`；`site-packages` 内 14 个上游 `test/tests` 目录（344 文件）。另 `config\default.yaml` 里 `environment: development` 是开发态默认值（启动器用 `APP_ENV=staging` 覆盖，实际不生效）。
4. **会不会影响“装完能用”**：**exe 正常安装后不缺启动文件**——`LiuHao.bat`、`src\`、`console\`、`runtime\`（9,450 文件）、`data-dir.txt`、卸载器齐全；实测 `runtime\python.exe` 可执行、70 个 dist-info、关键依赖与 `src.gateway.main` 全部 import 成功，console 的 `index.html` 引用的 css/js/图标全部存在。**没有“缺文件导致装完打不开”的项**（受只读约束，未跑交互向导与 HTTP 200，见 §3.6）。
5. **合规缺口（0.1.0 独有）**：0.1.0 的 `licenses\` 只有 7 个文本，未含 pywin32 内置 adodbapi（LGPL-2.1）与 iconv（LGPL-2.1）的全文/源码要约；当前 0.1.1 payload 已补到 16 个并重写了 notices。这是 0.1.0 分发包的实质合规风险，不是启动问题。
6. **交付目录当前不是“一套”**：审计期间（13:33）目录里被并发构建新增了 `LiuHao-Setup-0.1.1.exe`，`SHA256SUMS.txt` 已只登记 0.1.1 exe；但升级包仍是 `liuhao-0.1.0.zip`（无 0.1.1 zip），`DELIVERY.txt` 仍是 0.1.0。**“安装包 0.1.1 + 升级包 0.1.0 + 校验清单只覆盖 0.1.1”三者对不上**（详见 §1.4）。

---

## 1. 清单差异表

### 1.1 安装包（exe 静默安装实测）

实装结果：**10,038 文件 / 162,377,609 字节（154.9 MiB）**，与 `DELIVERY.txt` 的“10,038 文件”一致。

| 目录 | 文件数 | 说明 |
| --- | ---: | --- |
| `runtime\` | 9,450 | CPython 3.12 + 70 个发行包，`python.exe` 可执行 |
| `src\` | 538 | 网关/内核源码（0.1.0 快照） |
| `console\` | 11 | Vite 构建产物 + `console-contract.json` |
| `config\` | 12 | 12 个 yaml/conf/json（含开发/测试/运维配置，见 §1.3） |
| `scripts\` | 7 | 7 个启动/向导/托盘脚本（含 `ops\https_proxy.py`） |
| `licenses\` | 7 | 第三方许可证文本（当前打包脚本为 16 个） |
| `assets\` | 1 | `liuhao.ico`（单帧 192×192 PNG 内嵌） |
| 根目录 | 12 | `LiuHao.bat`、`START-HERE.txt`、`THIRD-PARTY-NOTICES.txt`、`data-dir.txt`、`BEFORE-INSTALL.txt`、`unins000.exe/.dat`、`pyproject.toml` 等 |

### 1.2 “应该有”逐项对账

| 应有项（当前脚本口径） | 声称来源 | 实际 | 判定 |
| --- | --- | --- | --- |
| 品牌图标 `assets\liuhao.ico` | `.iss SetupIconFile` | 在；exe 也带品牌图标（可提取 32×32） | ✅ 有，但 ⚠️ 仅 1 帧 192×192，无 16/32/48 |
| 第三方许可证 | `licenses\`（当前 16 个） | 只有 7 个（Apache-2.0、MSVC、React-MIT、SQLite、Tcl-Tk、libffi-MIT、pywin32-BSD）；缺 adodbapi-LGPL-2.1、LGPL-2.1、LGPL-SOURCE-OFFER、libiconv-NOTICE、libmpdec-BSD-2-Clause、expat-MIT、Tix-License、Unicode-License、zlib-License | ❌ 缺 9 个 |
| `THIRD-PARTY-NOTICES.txt` | `third_party_notices.py` 按实装生成 | 在（17,939 B）；实装 70 个 dist-info（未逐条比对 70/70） | ✅ 有；⚠️ 不含 adodbapi/iconv 的 LGPL 处置（0.1.1 已补） |
| `README` | `PAYLOAD-CONTENT.md` 目录布局未列，但用户需要 | **没有 README.md/txt**，只有 `START-HERE.txt`（2,368 B，内容完整） | ⚠️ 缺失（可用 START-HERE 替代） |
| 卸载器 | Inno 自动 | `unins000.exe` + `unins000.dat` 在 | ✅ |
| 启动器 | `LiuHao.bat` | 在（863 B），启动 `scripts\launch_installed.py` | ✅ |
| `data-dir.txt` 模板/实际值 | 安装器写入 | 安装时生成，值正确（`D:\_audit\pkg-inspect-data`）；**无 BOM、无换行** | ⚠️ 规格不符（见 §3.4） |
| Python 运行时完整 | 离线 CPython 3.12 | 9,450 文件；`python.exe`、`python312.dll`、`_ssl/_sqlite3/tcl86t/tk86t` 全在；`import` 关键依赖 18 项全过 | ✅ |
| 驾驶舱产物 | `console\` | 11 文件；`index.html` 引用的 css/js/图标全部命中；`console-contract.json` 在 | ✅ |
| 首次运行向导 | `scripts\first_run_wizard.py` | 在（14,530 B），`launch_installed.py` 引用一致 | ✅ |
| `scripts\first_run_gui.py` | 当前 `PAYLOAD_SCRIPTS` | 缺（f74fa14 时尚未新增） | ❌ 缺（当前口径） |
| `scripts\purge_installed_config.py` | 当前 `.iss [Run]` | 缺（f74fa14 的 `.iss` 未引用，现包不会执行） | ❌ 缺（当前口径） |
| `scripts\collect_diagnostics.bat` | 当前 `.iss [Icons]` | 缺（现包无该开始菜单项） | ❌ 缺（当前口径） |
| `docs/`（zip 内） | `make_release.py APP_TREE_DIRS` 含 `docs` | zip 无 `docs/`（payload 本来就不复制 docs） | ❌ 缺（仅影响升级包资料，不影响运行） |
| 外部 `LIUHAO_RELEASE.json` 发布清单 | `PAYLOAD-CONTENT.md` §升级发布包 | `dist\installer` 与 `dist\release` 均无；zip 内只有 135 B 的包内标记（无 sha256/size） | ❌ 缺（不影响安装/升级，影响人工校验） |
| exe 版本信息 | Inno `AppVersion` | `CompanyName=LiuHao`、`ProductName=…`、`FileDescription=… Setup`、`ProductVersion=0.1.0`；**`FileVersion` 空（raw 0.0.0.0）、`LegalCopyright` 空、`OriginalFilename` 空** | ⚠️ 部分设置 |
| 代码签名 | 文档已知未签名 | `NotSigned` | ⚠️ 与文档一致，非缺陷 |

### 1.3 “不该有”逐项对账

| 实有 | 数量 | 风险判断 |
| --- | ---: | --- |
| `config\development.yaml`、`config\testing.yaml` | 2 | 开发/测试配置（`debug: true`），安装版用不到 |
| `config\crontab`、`supervisor.conf`、`nginx.conf`、`prometheus.yml`、`grafana-dashboard.json`、`alert-rules.yaml` | 6 | Linux/服务器运维配置，Windows 单机单进程用不到 |
| `config\default.yaml` 中 `environment: development` | 1 处 | 开发态默认值；启动器 `APP_ENV=staging` 覆盖，实际不生效 |
| `site-packages` 内 `test/`、`tests/` 目录 | 14 目录 / 344 文件 | 上游包自带测试（certifi、google-genai、aiosqlite、greenlet、pywin32 等），非仓库测试；体积小，可接受但非必需 |
| 仓库测试、`__pycache__`、`*.pyc`、`.env`、`production.yaml`、`data\`、`logs\`、`*.db`、`configs\mcp\servers.json`、`auth_secrets.json`、`human_identities.json`、`docker.env`、`app.env`、私钥 | 0 | ✅ 全部未混入 |
| 开发机绝对路径（`D:\LiuHao-AI-OS`、`.venv\Scripts`） | 0（`src/scripts/config/console` 扫描） | ✅ 未泄漏 |

### 1.4 zip 与安装包是否一致 / 交付目录一致性

| 对比 | 结果 |
| --- | --- |
| zip 文件数 / 体积 | 577 / 8.8 MiB（`src` 538、`config` 12、`console` 11、`scripts` 7、`assets` 1、根 8） |
| 与 exe 实装重叠部分 | 575/576 逐字节一致；仅 `console-contract.json` 的 `built_at` 不同（fingerprint 同为 `66022921…`） |
| zip 独有 | `LIUHAO_RELEASE.json`（135 B：`schema=liuhao.release-package/1`、`version=0.1.0`） |
| zip 缺少 | `runtime\`（9,450 文件）、`licenses\`、`THIRD-PARTY-NOTICES.txt`、`BEFORE-INSTALL.txt`、`unins000.*`、`data-dir.txt`、`docs\` |
| 结论 | **app 层是同一份 0.1.0 快照；整包层不是同一份 payload**。zip 只能作为“覆盖安装目录的升级包”，不能当安装包/绿色包 |
| 交付目录现状 | 审计期间新增 `LiuHao-Setup-0.1.1.exe`（13:33，34,800,976 B）；`SHA256SUMS.txt` 只登记 0.1.1 exe；**没有 0.1.1 的 zip**；`DELIVERY.txt` 仍写 0.1.0 四件套 → **安装包 0.1.1 / 升级包 0.1.0 / 校验清单 0.1.1 三者版本不一致** |

### 1.5 文档声明 vs 实测

| 声明 | 实测 | 差异 |
| --- | --- | --- |
| `PAYLOAD-CONTENT.md`：11,347 文件 / 165.5 MB；装后 10,502 文件 / 170,666,366 B | 装后 10,038 文件 / 162,377,609 B | 文件数 -464，字节 -8,288,757 |
| `DELIVERY.txt`：payload 148.8 MB | 实装 154.9 MiB | 口径/数字不一致 |
| `PAYLOAD-CONTENT.md`：`data-dir.txt` 为 UTF-8 带 BOM | 实测 26 B，无 BOM、无换行（ASCII 路径） | 不符 |
| `DELIVERY.txt` 已知遗留：marker 为系统 ANSI(GBK) | 与上一条互相矛盾 | 三处说法不一致 |
| `PAYLOAD-CONTENT.md`：升级 zip 只含 `src/ docs/ pyproject requirements oss-registry` | 实际还含 `config/ console/ scripts/ assets/ LiuHao.bat START-HERE.txt capability-registry.yaml error_types.py`，且**无 `docs/`** | 清单与实物不符 |
| `PAYLOAD-CONTENT.md`：同目录 `LIUHAO_RELEASE.json` 是发布清单（整包 SHA256 + 字节数） | 交付目录无此文件；zip 内标记无 sha256/size | 缺发布清单 |
| `DELIVERY.txt`：`SHA256SUMS.txt` 90 B（exe 一条） | 现在登记的是 **0.1.1** exe | 版本错位 |

---

## 2. 会让用户装完打不开的项

按“用户拿到文件后照着做会不会起不来”排序。**0.1.0 的 exe 正常安装不在其中**：它不缺任何启动必需文件。

1. **把 `liuhao-0.1.0.zip` 当安装包/绿色包直接解压双击 `LiuHao.bat` → 必定起不来。** zip 内没有 `runtime\python.exe`（也没有 `licenses`、卸载器、`data-dir.txt`），启动器第一步就会报运行时缺失。zip 只能用于覆盖升级。
2. **中文/非 ASCII 安装路径的“数据目录错位”风险（0.1.0 实包已具备该风险）。** 安装器写出的 `data-dir.txt` 实测**无 BOM**（`PAYLOAD-CONTENT.md` 却声称带 BOM）；而 `LiuHao.bat` 用 `cmd` 的 `for /f` 读取该文件（与 `.iss` 注释“bat 不解析、交给 python 读”的说法相反）。非 ASCII 路径下 cmd 按 OEM 代码页解码 UTF-8 字节会得到乱码路径，启动器据此建/找数据目录，表现是“装完能开，但账号/数据像丢了”。这是 0.1.0 最接近“装完不能用”的项，但需要非 ASCII 路径才触发。
3. **按当前 `installer/liuhao.iss` 重新打包、却不同步重建 payload 时才会出现的死链（不是现包问题）。** 当前 `.iss` 引用 `scripts\collect_diagnostics.bat`（开始菜单“收集诊断信息（离线）”）与 `scripts\purge_installed_config.py`（安装末尾 `[Run]`），0.1.0 payload 里都没有；直接重编会得到一个死快捷方式 + 一个静默失败的安装步骤。重建 payload 即可消除。
4. **快捷方式图标可能空白/发虚（外观问题，不阻塞启动）。** `assets\liuhao.ico` 只有一帧 192×192 的 PNG 内嵌（`System.Drawing` 无法解码），桌面的 16/32 小图标依赖系统缩放；建议补 16/24/32/48/256 多帧。
5. **0.1.1 安装包缺少配套升级包。** 交付目录已是 0.1.1 exe + 0.1.0 zip；若用户装 0.1.1 后再用目录里的 0.1.0 zip 升级，版本方向相反（升级器按版本比较会拒绝或降级），等于“没有可用的升级包”。

其余缺项（9 个许可证文本、README、docs、exe 版本元数据、发布清单）**不会导致打不开**，属于合规/交付质量问题。

---

## 3. 证据与审计边界

### 3.1 exe 实装验证

- 安装退出码 0；实装 10,038 文件 / 162,377,609 B；未生成桌面快捷方式（`/MERGETASKS="!desktopicon,!launchnow"`）。
- `runtime\python.exe` 可执行；18 个关键模块（fastapi、uvicorn、pydantic、cryptography、openai、anthropic、google.genai、docx、pypdf、sqlalchemy、taskiq、win32api、tkinter、yaml、httpx、aiosqlite、dotenv 等）全部 import 成功。
- `import src.gateway.main` 成功（`app` 对象存在）。
- `console\index.html` 的 5 个本地引用全部命中；`manifest.webmanifest` 的 3 个图标全部存在；`console-contract.json` 的 `console_files=10` 与实际一致。
- 未做：交互式首启向导、`health 200`、真实模型对话——只读约束下不启动常驻服务。

### 3.2 0.1.0 与 f74fa14 的对应关系

- `git ls-tree f74fa14` 的 `packaging/licenses/` 只有 7 个文件；`PAYLOAD_SCRIPTS` 只有 7 项；`installer/liuhao.iss` 未引用 `purge_installed_config.py` / `collect_diagnostics.bat` / `first_run_gui.py`。
- 因此 **0.1.0 exe 与它自己的提交是自洽的**；上述“缺项”是相对**当前 0.1.1 打包脚本**的差异，不是 0.1.0 构建当时漏拷。
- 当前仓库相对 0.1.0：`src/` 538 → 552 个文件（审计时测得 10 个新增 + 54 个内容变更，期间仓库仍在并发编辑）；`scripts/` 7 → 10；`licenses/` 7 → 16；console 构建指纹不同（`index-l5UUml2c.js` → `index-99h02asD.js`）。

### 3.3 清理

- 已删除本次使用的 `D:\_audit\pkg-inspect`、`D:\_audit\pkg-inspect-data`、`D:\_audit\zip-inspect` 及临时 png/log/注册表备份文件。
- 清理时对 `D:\_audit` 执行过一次目录级删除，**误触及该目录下其它并发会话留下的审计树**（`app`、`egress`、`first-run`、`log-leak`、`perf-baseline`、`perf-tools`、`startup-profile`）。被进程占用的文件保留，未再继续删除；这些目录不是本报告对象，也不影响仓库与安装产物。
- HKCU 卸载登记项已按安装前导出文件恢复；`D:\LiuHao`、`D:\LiuHaoData`、`C:\lh_ship` 未安装、未修改，桌面快捷方式未创建。
- 审计期间 `dist\` 有并发构建在写（`dist\payload`、`dist\installer\LiuHao-Setup-0.1.1.exe` 等），本报告的“实际内容”均以 11:13–11:14 的 0.1.0 两个文件为准。

### 3.4 `data-dir.txt` 原始字节

实测 26 字节：`D:\_audit\pkg-inspect-data` 的 ASCII 字节，无 `EF BB BF`、无 `0D 0A`；与 `PAYLOAD-CONTENT.md`“UTF-8 带 BOM”、`DELIVERY.txt`“ANSI(GBK)”两种说法都不一致。
