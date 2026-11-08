# RELEASE-RUNBOOK — 收口必跑清单

本文件是仓库内可审计的收口清单，也是**唯一事实源**（随 git 跟踪）。
`scripts/release/package-frozen.ps1` 按顺序执行；**任何一项非 0 即停**，不允许
"记一笔继续打包"。`dist\` 是产物目录，收口过程只读，唯一例外是
`dist/release-gate/RELEASE-RUNBOOK.md`——它是本文件的**生成物**，**不得手改**（见 §6）。

## 0. 前置

- Windows + PowerShell 5.1；发布解释器由 `-PythonExe` / `LIUHAO_RELEASE_PYTHON` 指定。
- 不运行 `setup.exe`，不改 `dist\`。
- 工作树必须是冻结后的干净状态。

## 1. 收口必跑

| # | 检查 | 命令 | 通过条件 |
|---|------|------|----------|
| 1 | 环境闸 | `package-frozen.ps1` → `freeze_checks.py env --repo .` | 退出 0 |
| 2 | 依赖链 | `freeze_checks.py deps --repo .` | 退出 0 |
| 3 | 前端源码基线 | `freeze_checks.py frontend-source --repo .` | 退出 0 |
| 4 | payload 同源 | `freeze_checks.py frontend-payload --repo . --payload <app> --version <v>` | 退出 0 |
| 5 | **明文凭据** | `freeze_checks.py log-leak --repo .` | 退出 0 / 2 |
| 6 | **Runbook 同源** | `freeze_checks.py runbook --repo . --sync` | 退出 0（`dist` 副本由根文件重新生成） |
| 7 | **安装后回环写入**（手动必跑） | `.\.venv\Scripts\python.exe scripts\acceptance\production_loopback_write_e2e.py` | 退出 0 |
| 8 | **高 DPI 验收**（手动必跑，需交互式桌面会话，见 §5） | `.\.venv\Scripts\python.exe scripts\acceptance\run_dpi_acceptance.py` | 退出 0；无头 / 服务会话下退出 3（跳过并说明） |
| 9 | **安装副本同源**（打包装机，手动必跑，见 §6） | `.\.venv\Scripts\python.exe scripts\acceptance\installed_copy_drift.py --install-dir D:\LiuHao` | 退出 0 |
| 10 | **本机攻击面回归**（见 §5.7） | `.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py --allow-missing-installed` | 退出 0；负向对照必须 `0/4` 且退出 1 |
| 11 | **成品包合规**（包里有，见 §7） | `python dist\release-gate\verify_package_compliance.py --payload <app> --exe <setup.exe> --upgrade-zip <zip>` | 三者均退出 0；否则不写 `SHA256SUMS.txt` |
| 12 | **安装后合规**（装完本机有，见 §7） | `python dist\release-gate\verify_installed_compliance.py --app D:\LiuHao` | 退出 0：`licenses/` 16/16 + NOTICES + SBOM + LICENSE |
| 13 | **默认零外连**（手动必跑，见 §5.8） | `.\.venv\Scripts\python.exe scripts\acceptance\egress_default_off_e2e.py` | 退出 0；退出 **1 或 2 均阻断**（2 = 环境不具备，不算通过） |

### 5.1 明文凭据扫描（发布闸）

```powershell
# 收口脚本内部（只扫受控目标：仓库 logs\ 与显式 --path，不读开发机本地数据）
.\.venv\Scripts\python.exe scripts\release\freeze_checks.py log-leak --repo .

# 直接跑验收扫描器（可指定数据目录 / 诊断包 / 审计库）
.\.venv\Scripts\python.exe scripts\acceptance\log_leak_scan.py --data-dir D:\LiuHaoData
```

扫描对象：`liuhao.log`、`error.log`、`desktop-gateway.log`（含轮转备份）、诊断包 `*.zip`
（逐成员）、`audit_store.db` 的 `audit_events.details`。

退出码含义（`log_leak_scan.py` 与 `freeze_checks.py log-leak` 一致）：

| 退出码 | 含义 | 收口动作 |
|--------|------|----------|
| `0` | 扫到至少一个对象，且无明文命中 | 通过 |
| `1` | 命中明文凭据（含"脱敏只遮一半"的残留） | **阻断发布**，按命中文件修复脱敏后重跑 |
| `2` | 没有任何可扫描对象 | 发布闸视为 PASS-with-note（新工作树本来就没有 logs）；不能据此宣称"证据干净" |

### 5.2 安装后真实数据目录复扫（必做）

打包机上的扫描只覆盖仓库 `logs\`。安装包装完后，先制造一点真实流量（打开控制台、
保存一次模型配置、故意让一次上游请求返回 401），再对**安装后的数据目录**复扫：

```powershell
# 默认数据目录
.\.venv\Scripts\python.exe scripts\acceptance\log_leak_scan.py `
    --data-dir "$env:LOCALAPPDATA\LiuHao\data"

# 用户自定义数据目录时，指到实际路径
.\.venv\Scripts\python.exe scripts\acceptance\log_leak_scan.py --data-dir D:\LiuHaoData
```

要求 `EXIT=0`。非 0 时**禁止**把该机器上的日志或诊断包发给客服，先按命中文件修脱敏/升级版本。

> 说明：开发/托盘调试时 `desktop-gateway.log` 落在仓库 `logs\`（由
> `src/desktop/__init__.py` 的 `LOG_DIR` 决定），发布闸默认会扫到；安装态请用
> `--data-dir` 指定用户数据目录。

## 2. 诊断包导出闸（降级导出，自动，不靠人记）

`/v1/diagnostics/bundle` 与桌面「生成诊断包」在**写 zip 之前**调用
`src/common/leak_scan.py` 扫描全部成员，两条路径共用同一张规则表。命中时不再
"一刀切拒绝"，而是**降级导出**——用户最需要求助时不能什么都发不出去：

- **剔除而不失败**：仍含明文凭据的成员不进包，其余成员照常打包；包内新增
  `REDACTED-MEMBERS.txt`（文件名 + 命中条数，不含命中内容）与
  `scan_rule_hit.json`（规则 × 文件 × 次数，不含命中内容），`README.txt` 首页写明
  「为保护你的密钥，N 个日志文件未包含」以及「本次包含 N 个文件，未包含 M 个」。
- **内部审计事件**：整体拒绝时写 `evt=diagnostic.bundle.blocked source=<gateway|desktop>
  files=<成员名> rules=<规则名=次数>`（logger `liuhao.audit`，落 `liuhao.log` / `error.log`），
  只记规则名与命中类别、不记内容——这条事件出现就说明大概率是我们的脱敏/扫描规则误判。
  此时不带 `scan_rule_hit.json`（没有包可发），支持人员按事件里的 `files`/`rules` 定位。
- **HTTP 200（降级成功）**：响应头 `X-LiuHao-Diagnostics-Omitted`（数量）与
  `X-LiuHao-Diagnostics-Omitted-Members`（ASCII JSON 文件名列表）；控制台据此提示
  「有 N 个日志文件（或诊断文件）因含密钥未包含」。
- **HTTP 409 + `code=DIAGNOSTIC_BUNDLE_BLOCKED`**：只有"真实诊断成员一个都留不下"
  才出现，属罕见兜底（扫描规则误判 / 日志残留无法脱敏的片段），文案明确写"不是常见故障"。
  不含"请重试"，下一步是可执行动作：退出鎏灏 → 把数据目录 `logs\` 下的
  `liuhao.log`、`error.log`、`desktop-gateway.log`（以及 `.log.1` 轮转文件）**改名备份**
  （`.log` → `.log.bak-日期`，不直接删除，保留排查证据）→ 重新打开再导出；
  仍失败则把提示截图发给技术支持。

出现 409 时不要绕过（改白名单、改后缀都不行）。生成后的 `verify_bundle` 还会做一次
二次自检，二次自检失败同样不产出"看起来正常"的包。

## 3. 扫描器年检

```powershell
.\.venv\Scripts\python.exe scripts\acceptance\log_leak_scan.py --self-test
```

必须 `SELF-TEST PASS`：负向对照（明文 `?key=` / `Bearer sk-`）必须报命中且
`EXIT=1`，半截残留（`Bearer sk-***REDACTED***abc123`）也必须报命中。任何一条变红，
说明规则表被改坏或脱敏链路回退，禁止发布。

## 4. 安装后回环写入（必跑）

安装版以 `APP_ENV=production` 启动后，浏览器从 `http://127.0.0.1:<实际端口>` 发出的
注册、登录、建员工、发消息等**写请求必须全部放行**；非本机回环来源、或回环但端口
不符的跨站写请求必须仍然 403。这是"装完能不能打开登录进去"的发布阻断级验收项，
每次候选构建都要在打包机上跑一遍（暂未接进 `package-frozen.ps1`，按手动必跑执行）：

```powershell
.\.venv\Scripts\python.exe scripts\acceptance\production_loopback_write_e2e.py
```

脚本会自起一台 `APP_ENV=production` 的真实网关进程（随机端口 + 临时数据目录），
依次完成「自助注册 → 登录 → 建员工 → 发消息」，断言没有一个写请求被 403，并从
服务端日志核对回环同端口 Origin 的 `SECURITY_REJECTED reason=cross_site_write`
计数为 0；随后用 `http://evil.example` 与「回环但端口不符」两组负向对照必须 403。
跑完杀进程并删除临时目录，不写 `D:\LiuHaoData`，不依赖 `dist\`，不跑 `setup.exe`。

退出码：

| 退出码 | 含义 | 收口动作 |
|--------|------|----------|
| `0` | 全部检查通过（当前 14 项），且负向对照确实产生了 `SECURITY_REJECTED` | 通过 |
| `1` | 任一项 `[FAIL]`：某个写请求被 403、某组负向对照没被拦、或日志证据不符 | **阻断发布**，按 `[FAIL]` 逐条修复后重跑 |

> 判定口径看**实际 HTTP 状态 + 服务端日志**，不看"进程起来了"这类间接信号；
> 负向对照也必须留下 `SECURITY_REJECTED`，否则说明安全闸根本没生效。

## 5. 高 DPI 验收（必跑，需交互式桌面会话）

覆盖：100% / 125% / 150% / 175% / 200% 单屏缩放；双屏 1.25 ↔ 1.5 拖动；
1920×1080 → 1366×768 → 1280×720 → 断开远程桌面回本机 150%；控制台在
`devicePixelRatio=2` 下的首屏可用性。逐条 PASS/FAIL，证据图落 `docs/dpi-evidence/`。

```powershell
.\.venv\Scripts\python.exe scripts\acceptance\run_dpi_acceptance.py
# 分项重跑：--desktop-only / --console-only
```

**必须在交互式桌面会话（登录后的 WinSta0、session ≠ 0）里跑。** 无头 CI、
Windows 服务会话（session 0）或任何窗口映射不出来的环境，脚本会打印
`[SKIP] 高 DPI 验收需要交互式桌面会话：…` 并把旧报告标记为 `skipped`，
**不给出任何绿**；此时收口记录写"未验证"，不要把退出码 3 当通过。

退出码：

| 退出码 | 含义 | 收口动作 |
|--------|------|----------|
| `0` | 桌面用例与控制台用例全部通过 | 通过 |
| `1` | 任一项 FAIL（窗口出工作区 / 按钮被裁 / 滚动条缺失 / 字号未按比例缩放等） | **阻断发布**，按 `[FAIL]` 逐条修复后重跑 |
| `3` | 当前不是交互式桌面会话，桌面用例**跳过、未判定** | 不算通过：到登录的桌面会话重跑；或在收口记录里明确写"高 DPI 未验证" |

### 5.1 安装副本复核（确认装出来的 desktop 代码没落后）

对**安装目录里的代码**跑同一套用例（只把安装根放进 `sys.path`，不回退源码），
证据图加 `installed-` 前缀，报告写 `docs/dpi-evidence/installed-copy-report.json`：

```powershell
$env:LIUHAO_DPI_TARGET_ROOT = 'D:\LiuHao'
.\.venv\Scripts\python.exe scripts\acceptance\dpi_scaling_acceptance.py
Remove-Item Env:\LIUHAO_DPI_TARGET_ROOT
```

报告里的 `verdict` 会直接给出结论：全部 PASS 是"安装副本已包含本轮 DPI 修复"，
否则是"安装副本为旧版本，本轮修复未进包"——后者不能拿来当源码的绿，修复必须
先重新出包再复跑。该复核只读安装目录，不动 `dist\`、不跑 `setup.exe`。

### 5.7 本机攻击面回归闸（每包必跑；**任何 FAIL 阻塞出包**）

把 2026-11-08 修掉的本机攻击面 P0/P1、本机身份证明管道、备份导出、卸载残留
固化成可重复跑的探针。它不是一次性手工验收；打包前置与装机后都必须跑。

```powershell
# 构建前置（安装副本可能尚未存在）：⑤ 自动跳过，其余全跑
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py --allow-missing-installed

# 装机完成后复跑：安装副本必须存在
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py `
  --installed-data-dir "$env:LOCALAPPDATA\LiuHao\data"

# 负向对照（必须 0/4、EXIT=1，证明探针不是永远绿）
.\.venv\Scripts\python.exe scripts\acceptance\local_attack_surface_e2e.py --negative-control
```

覆盖：

| # | 场景 | 关键断言 |
|---|------|----------|
| ① | 改 `base_url` 外带已存 Key | 未重输 Key → 409 `MODEL_KEY_TARGET_CHANGED`；假攻击者收不到任何请求/Authorization |
| ② | 重输 Key / `confirm_key_resend=true` | 放行；审计 `key.base_url_changed` 含前后 target 与两种外发标记 |
| ③ | 同 host 换端口 | 同样视为新目标 → 409 |
| ④ | 回环多端口（LM Studio / vLLM） | 可保存、可测试，且各自只收到自己的 Key |
| ⑤ | 安装副本 ACL | `app.env` / `auth_secrets.json` / `model_config.key` 去继承、仅当前用户 |
| ⑥ | 本机身份证明管道 | 管道 DACL 仅当前用户；受限令牌连管道 `winerror=5`；handoff token 换进程 403、属主 200、取用即焚、不落盘 |
| ⑦ | 备份/导出不含密钥 | `scope=all` / `scope=self` 真解包后无禁用文件名、无明文 Key；主密钥不随包、Key 以口令密封携带 |
| ⑧ | 卸载残留 | **安装器规格硬断言**：`[UninstallRun]` 先 `-m src.desktop stop`、再 `-m src.desktop autostart disable`（不手写注册表）、`CloseApplications=yes`、全文无自启注册表删除；**行为断言**：默认保留数据 / 明确删除数据两种卸载后，程序目录、`data-dir.txt`、快捷方式、卸载键、自启项、托盘进程、端口均无残留 |

**负向对照**：⑥ 仅授 `BUILTIN\Users` 的管道必须被判"非仅当前用户"；⑦ 含
`model_config.key` 与明文 Key 的包必须被扫描器命中；⑧ 故意留下自启项与托盘进程
必须被判红；⑨ 把 `[UninstallRun]` 的停进程/关自启两行注释掉后，安装器规格闸必须
判红（反向验证这条闸不是永远绿）。

**通过标准**：末行 `结果：78/78 通过`、失败 0、退出码 0；`--negative-control`
必须 `结果：0/4 通过`、退出码 1。任一条 FAIL 都**阻断出包**。

**方法与边界**：⑥ 用受限令牌（把当前用户 SID 设为 deny-only）等价模拟"同机另一个
无授权主体"，不创建真实第二 Windows 账户；⑧ **不跑 `setup.exe`**，在临时目录搭
安装足迹并用探针专属注册表根（`HKCU\Software\LiuHaoAcceptance\<token>`）与临时
桌面/开始菜单目录做受控模拟，不碰真实 `HKCU\...\Run` 与真实快捷方式。安装器规格
是**硬断言**：读不到 `stop` / `autostart disable` / `CloseApplications=yes` 就 FAIL。

**必须装机复核（探针不跑 `setup.exe`，以下 3 项无法由探针替代）**：
1. **卸载断进程**：托盘运行时，分别用 `/SILENT` 与交互模式卸载一次；确认 `runtime\pythonw.exe` / `python.exe` 已退出、网关端口已释放、`D:\LiuHao` 删除成功且无“文件占用 / 重启后删除”。
2. **自启项**：卸载前先 `autostart enable`，卸载后 `HKCU\...\CurrentVersion\Run` 不再有 `LiuHaoAIOS`，重启后不会自动拉起。
3. **数据目录**：默认卸载保留 `D:\LiuHaoData` 且内容完好；勾选“删除数据”后目录消失；两种模式都不得误删或漏删。

退出码：`0` 全部通过；`1` 有 FAIL（阻断）；其它非 0 为脚本自身异常。

### 5.8 默认零外连回归闸（每包必跑；**退出 2 也阻断**）

```powershell
.\.venv\Scripts\python.exe scripts\acceptance\egress_default_off_e2e.py
```

它起一个隔离实例（临时数据目录 + 临时端口）和一个**只记录、绝不转发**的本地
代理，跑完整链路：启动 → 首屏（含本地 JS/CSS）→ 登录 → 发消息 → 检索（未开启）
→ 导出诊断包 → 检查更新。同时对网关进程（含子进程）做 100–250ms 的 TCP 采样，
两条证据都必须为 0：非回环连接、代理请求。

| 退出码 | 含义 | 收口动作 |
|--------|------|----------|
| `0` | 验证完成；非回环连接 0 条、代理请求 0 条 | 通过 |
| `1` | 验证完成，但发现出站（打印目标 IP/域名/端口），或流程断言失败 | **阻断发布**，按目标定位新增代码 |
| `2` | **环境不具备，未判定通过**：代理无法监听、端口无法分配、psutil 无法观测连接 | **阻断发布**；换具备条件的机器重跑，**不得记为通过** |

配套的静态兜底：`tests/security/test_egress_registry.py` 会扫描 `src/` 里出现的
每个 `http(s)://` 主机，未在 `src/security/egress_registry.py` 登记即测试失败。
新增任何出站域名，先登记（功能、触发时机、字段、默认状态、披露文案），再写代码。

## 6. Runbook 单一事实源

仓库根的 `RELEASE-RUNBOOK.md`（本文件）是**唯一事实源**，随 git 跟踪；
`dist/release-gate/RELEASE-RUNBOOK.md` 是**生成物**，两侧内容不一致时以本文件为准。

- 不手改 `dist` 副本：改完本文件跑一次同步，副本会被整体重写。
- 收口脚本（`package-frozen.ps1` 步骤 1.8b）会自动执行 `runbook --sync`，
  也就是"先按根文件生成副本，再校验一致"。
- 人工复核可只读校验（不写 `dist`）：

```powershell
# 只校验（副本缺失视为通过；内容漂移退出 2）
.\.venv\Scripts\python.exe scripts\release\freeze_checks.py runbook --repo .

# 需要更新副本时（只写 dist/release-gate/RELEASE-RUNBOOK.md 这一个生成物）
.\.venv\Scripts\python.exe scripts\release\freeze_checks.py runbook --repo . --sync
```

退出码：`0` = 一致或尚无副本；`2` = 副本漂移或根文件缺失，按提示重新同步。

## 7. 合规材料：包里有 + 装完本机有

> §1 第 11 项证明「包里有」，第 12 项证明「装完本机有」。二者不能互相替代：
> 材料在包里但安装器漏装、或卸载误删，用户照样拿不到许可证记录。

**包里有**（成品包校验，只读：payload 直扫 / exe 用 innounp 解包 / zip 用 zipfile 解包）：

```powershell
python dist\release-gate\verify_package_compliance.py --payload dist\payload\app
python dist\release-gate\verify_package_compliance.py --exe <setup.exe> --json-out <out>\compliance-installer.json
python dist\release-gate\verify_package_compliance.py --upgrade-zip <liuhao-*.zip> --json-out <out>\compliance-upgrade.json
```

升级包内的合规材料必须放 `docs\compliance\`，不得放根目录：升级器
`src/upgrade/manager.py::INSTALL_ROOTS` 只允许白名单目录，根目录 `licenses\` 会被整包拒绝。

**装完本机有**（安装后自检，只读，不启动程序）：

```powershell
$app = "D:\LiuHao"   # 换成实际安装目录
python dist\release-gate\verify_installed_compliance.py --app $app --json-out "$out\installed-compliance.json"
```

材料落位（打包线自动完成：`packaging\stage_payload.py` + `installer\liuhao.iss`）：

| 安装目录里的路径 | 来源 | 卸载行为 |
|---|---|---|
| `{app}\licenses\`（16 项全文） | `packaging\licenses\` | **保留**（`uninsneveruninstall`） |
| `{app}\THIRD-PARTY-NOTICES.txt` | 按实装分发树现生成 | **保留** |
| `{app}\sbom-cyclonedx.json` | 本次 payload 现算（`liuhao:audit-source-tree` 改写为 `.`，构建机路径不入安装目录） | **保留** |
| `{app}\LICENSE` | 仓库根 `LICENSE`/`LICENSE.txt`/`COPYING`（有才装） | **保留** |

**当前状态（2026-11-08）**：仓库根 `LICENSE` 已定为**短版专有声明**（著作权归属 + 保留所有权利 +
禁止未经书面许可再分发/转售/反向工程 + 第三方组件见 `licenses\` 与 `LGPL-SOURCE-OFFER.txt`），
`packaging/brand_guard.py` 已纳入该文件（exit 0）。`docs/legal/EULA-DRAFT-pending-review.md`
是 AI 起草、待法务确认的草稿，**不进任何交付物、不进安装包、不进升级 zip、不对外发布**
（`make_release.py` 的 `APP_TREE_SKIP_DOC_DIRS=("legal",)` 保证不进升级包）。


通过标准：退出码 0，四项全 `[OK]`；`licenses/` 为 16/16；SBOM 可解析且 `components` 非空；
`LICENSE` ≥ 500 字节。退出码 1 = 有缺项（按 `缺项 N` 清单补齐，不得对外分发）；
退出码 2 = 安装目录不存在/不可读（路径问题，不是材料缺失）。

## 8. 0.1.1 内部基线发版说明（不外发）

> 本包是**内部基线**，只用于验证「能装、能卸、数据不丢」。三个 P0 未修，不得对外发布。

1. **磁盘写满并清理空间后无需重启**：旧版本会停在
   `Capability not found: kernel.execution_pipeline`，必须重启软件；本版清理空间后可直接重试成功
   （`src/kernels/capability/__init__.py` 注册失败不再把半成品留在全局注册表）。
2. **满盘不再刷英文堆栈**：磁盘写满时不再出现 `--- Logging error ---` 英文堆栈，
   改为一条中文提示（`src/gateway/__main__.py` 关闭 `logging.raiseExceptions`）。
3. **满盘对话返回可行动 503**：磁盘写满时对话返回 `503 storage_unavailable` 中文提示，
   非流式与流式（含 SSE 错误帧）一致，不再是英文 `500`（`src/gateway/chat.py::_storage_http_error`）。
4. **启动可写性探测不再卡数十秒**：在 ACL 拒绝写入的目录里，`tempfile.mkstemp` 会被系统
   过滤驱动挂住数十秒；改为 `open(path, "xb")` 后 0.001 秒即报错。用户侧表现为
   「启动很慢、转很久才报错」，本版起恢复为立即报错。


卸载保留的已知差异：**0.1.0 旧版本**的安装清单没有这组标记，卸载仍会删掉它自己装过的文件；
0.1.1 起的包卸载保留，非静默卸载会在删除前弹一次说明，静默卸载不打扰。

## 9. 已知未修（内部基线，不影响运行）

出包前门禁 `ruff check --select F821,F811,F401 src/ packaging/ scripts/acceptance/`
要求 **F821/F811 为 0**；**F401 固定为 17 条，本轮不修**。它们全部是未使用的
import，不影响运行；所在文件多属仍在收口的活跃目录，为 17 个删行去改盘不划算，
风险高于收益。下面是完整清单——以后若再被扫出，**直接对照本表，不要当新问题重查**：

| # | 位置 | 未使用的 import |
|---|------|----------------|
| 1 | `packaging/smoke_payload.py:22` | `sys` |
| 2 | `scripts/acceptance/a11y_console_acceptance.py:33` | `sys` |
| 3 | `scripts/acceptance/account_tree_edges_e2e.py:25` | `typing.Tuple` |
| 4 | `scripts/acceptance/cli_backup_restore_e2e.py:32` | `typing.Optional` |
| 5 | `scripts/acceptance/console_dpr2_acceptance.py:35` | `sys` |
| 6 | `scripts/acceptance/egress_default_off_e2e.py:48` | `typing.Sequence` |
| 7 | `scripts/acceptance/local_attack_surface_e2e.py:931` | `win32security` |
| 8 | `scripts/acceptance/prune_payload_verify.py:40` | `typing.Optional` |
| 9 | `src/ai/liuhao.py:42` | `.providers.GENERATION_FAILURE_MESSAGE` |
| 10 | `src/gateway/diagnostic_bundle.py:29` | `os` |
| 11 | `src/gateway/local_proof.py:31` | `..common.local_proof_ipc.HANDOFF_HEADER` |
| 12 | `src/gateway/local_proof.py:32` | `..common.local_proof_ipc.RESET_HEADER` |
| 13 | `src/observability/boot_report.py:25` | `errno` |
| 14 | `src/observability/boot_report.py:36` | `typing.Iterable` |
| 15 | `src/security/secret_store.py:33` | `contextlib.contextmanager` |
| 16 | `src/security/secret_store.py:35` | `typing.Iterator` |
| 17 | `src/upgrade/filemanifest.py:22` | `typing.Mapping` |


