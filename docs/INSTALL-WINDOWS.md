# Windows 安装与卸载

> 适用：Windows 10 / 11 x64。
> 本页的安装、启动与卸载行为以 **0.1.1 及以上**为准；旧版本请先升级。
> **版本差异**：0.1.0 的包只有命令行首启向导（黑窗口里依次问管理员账号、选模型 `1`/`2`/`0`），没有下面说的图形「鎏灏首次设置」窗口；用法见 [END-USER-GUIDE.md](END-USER-GUIDE.md) 开头的版本差异说明。
> 安装包**不需要管理员权限**，不写系统目录，不改系统环境变量。

## 一、当前交付状态（如实说明）

| 项目 | 状态 |
| --- | --- |
| payload 组装（含离线 Python 运行时、托盘依赖 pywin32/tkinter/Tcl） | 🟢 已实跑：文件数与体积**以 `dist/payload/manifest.json` 为准**（2026-11-09 核对：`version=0.1.1`、10,855 文件 / 150.9 MB，未压缩）；每次构建都变，不在文档里写死 |
| payload 冒烟（启动→首页→注册→登录→发消息） | 🟢 本轮实跑通过（payload 内，mock 提供商） |
| Inno Setup 安装器脚本 | 🟢 已交付 `installer/liuhao.iss` |
| **`.exe` 安装包** | 🟢 顶层有 `LiuHao-Setup-0.1.1.exe`（2026-11-08 04:24 构建）。路径、字节数、SHA256 **以同目录 `SHA256SUMS.txt` 为准**，本文不写死（2026-11-09 核对：29,567,136 字节 / `49BC50C8…B4DF`）。<br>源码已是 `0.1.2`（`pyproject.toml:3`），但 **0.1.2 包尚未产出**，`dist/` 下没有任何 0.1.2 产物 |
| 升级发布包 `liuhao-0.1.1-upgrade.zip` | 🟢 顶层有该包（2026-11-08 04:23 构建）。字节数/SHA256 以 `dist/installer/SHA256SUMS.txt` 为准，本文不写死（2026-11-09 核对：32,178,761 字节 / `9EABA197…3224`）；**升级包不能单独运行**，只用于已安装的机器 |
| 升级发布清单 `LIUHAO_RELEASE.json` | 🟢 已生成，`python -m src.upgrade check --release-dir <目录>` 与 `python -m src.upgrade check --manifest <文件>` 均可读取；`releases[0].signature=true`（ed25519）。2026-11-09 实跑：`current_version=0.1.2`、`latest_version=0.1.1`、`status=up_to_date`——这是「源码版本高于清单版本」的正常结果，不是失败 |
| 安装后开机自启（托盘） | 🟢 默认**关闭**，需 `python -m src.desktop autostart enable` 开启；开启后的注册表登记已在真实安装目录验证：注册表值指向安装目录内的 `runtime\pythonw.exe` + `scripts\liuhao_desktop.py`，托盘窗口可创建 |
| 安装后的整机冒烟（安装 exe→启动→登录→发消息） | 🟢 0.1.1 安装包已有实跑记录：`dist/installer/README-DELIVERY.txt` 自述 S-01～S-05、S-10～S-14 PASS；S-06/S-07/S-08 原记「未取证」，2026-11-08 04:26–04:40 用真实 DeepSeek Key 转 PASS（`dist/release-gate/smoke/20261108-real-key/CONCLUSION.md`）；S-09（托盘/关窗）需人机交互，仍为 NEEDS_HUMAN。渲染后前端表单点击未做 |
| 卸载器「保留数据 / 删除数据」实机行为 | 🟢 静默卸载已验证：程序与快捷方式移除、数据保留；交互式“删除数据”分支已单独实测 |
| 代码签名 | 🟡 项目自签名证书已实装并验证（导入信任后 `Get-AuthenticodeSignature` 为 `Valid`）；非公开权威 CA，未导入信任仍提示「未知发布者」，SmartScreen 仍可能因信誉不足提示 |

> 前端（`apps/console`）正在重写，本轮产物会过时；最终安装包需在前端稳定后
> 重新执行一次 `packaging\build_windows_installer.ps1`。

## 二、用户安装步骤

1. 双击 `LiuHao-Setup-<版本>.exe`。
2. Windows SmartScreen 提示「Windows 已保护你的电脑 / 未知发布者」：
   点「更多信息」→「仍要运行」。安装包是**项目自签名**（2026-11-09 实测 `Get-AuthenticodeSignature` = `Status=Valid`，主体 `CN=鎏灏智能中枢, O=LiuHao AI OS, C=CN`），不是公开权威 CA；未导入信任脚本的机器仍会提示「未知发布者」，这不是病毒告警。
3. 安装前会显示一页中文说明（签名状态、不含模型、数据保留策略）。**注意**：该页文案当前仍写「本安装包未做代码签名」（`installer/liuhao.iss:1054`、`:1060`），与产物实际的自签名状态不一致——属安装器文案滞后，已登记 `known-gaps.md` E-24，本轮不手改 `installer/`。
4. 选择附加任务：
   - ☑ 创建桌面快捷方式（默认勾选）
   - ☑ 安装完成后立即启动鎏灏（默认勾选，可取消）
5. 程序默认装到 `D:\LiuHao`（数据 `D:\LiuHaoData`），不需要 UAC 提权；
   D 盘不可用时自动回退到 `%LOCALAPPDATA%\Programs\LiuHao` / `%LOCALAPPDATA%\LiuHao\data`
   （用户级目录，普通双击安装不会弹 UAC；只有主动“以管理员身份运行”安装包时，
   程序才回退到 `%ProgramFiles%\LiuHao`），实际位置见程序目录 `data-dir.txt`。
   安装向导里还有一页「数据目录」让你确认（已预填回退位置），点「下一步」即可。
   老版本用户覆盖安装保持原位置不变。
6. 完成后双击桌面快捷方式或开始菜单「LiuHao AI OS · 鎏灏智能中枢」。

首次启动会出现「LiuHao AI OS · 鎏灏智能中枢」状态窗口（显示启动状态，托盘同时出现鎏灏图标）
和标题为 **「鎏灏首次设置」** 的窗口（五步：数据目录 → 管理员账号 → 选择模型（可跳过）→
完成前请确认 → 自检结果）。第 4 页点 **完成并自检**，第 5 页点 **进入控制台**，
原生窗口（WebView2）打开登录页，用刚建的账号登录；网页模式才用 `--web` 开浏览器。
在第 1～4 步点「取消」或直接关窗＝放弃本次设置（不建账号、不启动控制台），重新双击图标可再来一次；
点过 **完成并自检** 后再关窗不会丢设置。
**程序是托盘常驻的**：关掉状态窗口只是隐藏到托盘，网关继续运行；真正退出用托盘菜单「退出（停止网关并关闭托盘）」。

需要命令行模式（排障用）时，在程序目录运行 `LiuHao.bat --console`。

> 界面文字以当前版本 0.1.1 的代码为准；如果你看到的按钮/提示与此处不同，请截图反馈。

- 端口默认 `8080`。**首启向导里可以换端口；日常启动如果端口被占，会告诉你占用原因并给出处理办法**：
  窗口会写出占用者（PID / 程序名），可以关掉占用程序再试，或在启动入口填一个空闲端口（例如 8081）后重新启动。
  换端口不用改快捷方式；地址随之变成 `http://127.0.0.1:<你选的端口>`。源码 / 命令行启动改用 `--api-port 8081`。
- 实际地址只看状态窗口里的 **「控制台：」** 一行。`<数据目录>\config\current_url.txt` 只是某次启动的快照
  （安装版 `LiuHao.bat` 启动会写，双击桌面图标启动不写），可能过期，不要以它为准。
- 起不来时按“三步”排查：看窗口最后几行 → 看 `<数据目录>\logs\launcher.log`
  与 `liuhao.log` → 在控制台 **系统设置 → 遇到问题 → 一键诊断包 → 导出诊断包**，把 zip 发给支持。

首次设置窗口细节、Key 申请地址、忘记密码与白屏处理见 [USER-GUIDE.md](USER-GUIDE.md)
与 [END-USER-GUIDE.md](END-USER-GUIDE.md)。

## 三、目录与数据

| 路径 | 内容 | 升级 | 卸载 |
| --- | --- | --- | --- |
| `D:\LiuHao`（回退：`%LOCALAPPDATA%\Programs\LiuHao`） | 程序、运行时、控制台（驾驶舱）静态资源、配置模板 | 覆盖替换 | 删除 |
| `D:\LiuHaoData`（回退：`%LOCALAPPDATA%\LiuHao\data`） | 账号、API Key（加密）、对话、审计、工作区 | **保留** | **默认保留** |

实际数据目录以程序目录下的 `data-dir.txt` 为准。

实际访问地址看状态窗口里的 **「控制台：」**。启动器还会写
`<数据目录>\config\current_url.txt`（`url=` / `port=` / `pid=` 快照；安装版 `LiuHao.bat` 启动会写、双击桌面图标不写），
但它可能过期，不要以它为准。
日志在 `<数据目录>\logs\`：
`launcher.log` 记录启动器与向导，`liuhao.log` 记录服务本身。

卸载流程：

1. 设置 → 应用 → 「LiuHao AI OS · 鎏灏智能中枢」→ 卸载（或开始菜单「卸载」）。
2. 卸载器先删程序，然后询问「是否同时删除你的数据？」——**默认「否」**。
3. 只有你选择「是」，才会弹出**二次确认**；两次都确认才永久删除
   `data-dir.txt` 记录的数据目录（默认 `D:\LiuHaoData`）。

覆盖安装（同版本或更高版本）不会触碰数据目录；`AppId` 固定，版本信息取自
`pyproject.toml`。

## 四、从源码构建安装包

前置条件：

- Node.js 18+ / npm
- Python 3.12 + 项目 `.venv`（已安装依赖）
- Inno Setup 6（仅编译 `.exe` 时需要）：<https://jrsoftware.org/isdl.php>

一条命令：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows_installer.ps1
```

脚本依次执行：

1. `apps/console/console`：`npm install`（首次）→ `npm run build` → `packaging/build_console.py --skip-build` 写入控制台源码/产物指纹
2. `packaging/stage_payload.py`：复制源码/配置/控制台/脚本，组装离线运行时
3. 凭据泄漏检查：payload 中出现 `auth_secrets.json`、`human_identities.json`、
   `.env`、`*.pem`、`*.key` 会直接中止
4. `packaging/smoke_payload.py`：真实起进程做冒烟（`-SkipSmoke` 时跳过并在产物里标注未验证）
5. `packaging/make_release.py --app-root dist\payload\app`：生成升级发布包
   `liuhao-<版本>.zip` 与 `dist\release\LIUHAO_RELEASE.json`（规范见
   [self-upgrade.md](self-upgrade.md) §2.2），并回读自校验（整包 SHA256、字节数、
   包内版本、artifact 名、控制台来源指纹与当前源码一致）
6. 生成 UTF-8 BOM 的编译副本 → `ISCC.exe` 编译
7. 输出 `SHA256SUMS.txt`、安装包与升级包的体积/哈希

常用参数（追加在上面的 `powershell …` 命令末尾，可组合）：

| 参数 | 作用 |
| --- | --- |
| `-SkipFrontend` | 前端已构建好，跳过 npm build |
| `-SkipInstaller` | 只出 payload，不编译安装器 |
| `-SkipSmoke` | 跳过冒烟（不推荐；构建报告会标注未验证） |
| `-SkipRelease` | 跳过升级发布包（只出安装器） |
| `-StrictReleaseDevPaths` | 把源码里的开发目录路径字面量当作构建失败（源码清理完成后建议打开） |

产物（均在 `dist\`，已被 `.gitignore` 忽略，不提交）：

```
dist\payload\app\                    安装内容
dist\payload\manifest.json           内容清单（发行包版本、文件数、体积）
dist\smoke-report.json               冒烟结果
dist\release\liuhao-<版本>.zip       升级发布包（ZIP 根即安装树）
dist\release\LIUHAO_RELEASE.json     升级发布清单（schema liuhao.release-manifest/1）
dist\release\dev-path-report.txt     开发目录路径扫描结果（排障用，不参与升级）
dist\installer\LiuHao-Setup-*.exe    安装包（ISCC 可用时）
dist\installer\SHA256SUMS.txt        校验和
dist\installer\NOT-COMPILED.txt      无 ISCC 时生成的说明（会明确写“未生成”）
```

没有 ISCC 时脚本以退出码 `2` 结束，并打印手动编译命令；**不会**创建假的 `.exe`。

### 发布包与升级清单（单独重跑）

必须显式指向安装树。**不带 `--app-root` 会直接拒绝**：仓库根模式漏掉根级
`error_types.py` / `capability-registry.yaml`，安装后自检会失败并回滚。

```powershell
# 1) 按当前源码构建前端并写入来源指纹（已构建过可加 --skip-build）
.venv\Scripts\python.exe packaging\build_console.py --dest dist\payload\app\console --version <版本>
# 2) 从安装树出包（ZIP 根即安装树）
.venv\Scripts\python.exe packaging\make_release.py --app-root dist\payload\app
```

- 产物包含安装树的 `src/`、`console/`、`config/`、`scripts/`、`assets/`、`docs/`
  与根级模块（`error_types.py`、`capability-registry.yaml` 等），以及包内清单
  `LIUHAO_RELEASE.json`（`schema=liuhao.release-package/1`）；
- 控制台必须带 `console/console-build.json`，且其源码指纹、产物指纹与当前
  `apps/console/console` 一致；旧 dist 重新盖章或构建后被改动都会拒绝出包；
- `sha256` 对**整包字节**计算，`size` 为字节数；算完后回读 ZIP 复核；
- 清单版本、包根清单版本、包内 `pyproject.toml` 版本三者必须完全相等，否则构建失败；
- **没有配置签名私钥时不写 `signature` 字段**（不会写空值或假签名）；
- 配置私钥即启用 Ed25519 签名（对 ZIP 完整字节签名，base64 写入清单）：

```powershell
$env:LIUHAO_UPGRADE_SIGNING_KEY = "D:\keys\liuhao-release-ed25519.pem"   # 未加密的 PKCS8 PEM
.venv\Scripts\python.exe packaging\make_release.py --app-root dist\payload\app
```

自检（会顺带执行一次真实升级检查）：

```powershell
.venv\Scripts\python.exe -m src.upgrade check --app-root . --release-dir dist\release
# 等价写法：直接指向清单文件
.venv\Scripts\python.exe -m src.upgrade check --app-root . --manifest dist\release\LIUHAO_RELEASE.json
```

> `--release-dir`、`--manifest`、`--manifest-url` 三者互斥，选一个即可；
> `--manifest` 读取指定文件，并从该文件所在目录解析 `artifact`。

手动编译：

```powershell
iscc /DAppVersion=<版本> installer\liuhao.iss
```

（`<版本>` 必须与 `pyproject.toml` 的 `project.version` 一致：源码当前 `0.1.2`，
已发布产物 `0.1.1`；写错会导致包内版本与源码不符。`installer\liuhao.iss`
默认从 `..\dist\payload\app` 取内容；如需自定义路径，
用构建脚本生成的 `dist\installer\build\liuhao.iss` 副本。）

## 五、验证与信任安装包签名

安装包 `LiuHao-Setup-*.exe` 使用本项目的**自签名代码签名证书**签名，证书主体
`CN=鎏灏智能中枢, O=LiuHao AI OS, C=CN`，有效期 10 年。它能让 Windows 在属性里
显示真实签名者，但**不是**公开权威 CA 签发的证书，也不具备公信力信誉。

### 验证签名

- 图形界面：右键安装包 → 属性 →「数字签名」→ 选中签名条目 →「详细信息」，
  可以看到签名者名称与指纹。
- 命令行（PowerShell）：

```powershell
Get-AuthenticodeSignature "D:\LiuHao-Setup-0.1.1.exe" | Format-List Status, SignerCertificate
```

未导入信任时，`Status` 可能是 `UnknownError`（证书链终止于不受信任的根证书）；
导入信任后应为 `Valid`。

### 安装信任（可选，一键）

`dist\installer` 与仓库 `signing\` 目录随包提供：

- `信任签名证书.cmd`：双击即可，只信任当前 Windows 用户，**不需要管理员权限**；
- `trust-liuhao-signing.ps1`：命令行版本，`-AllUsers` 需要管理员权限（写入机器级存储）；
- `LiuHao-CodeSigning.cer`：公钥证书，脚本会自动导入
  `Cert:\CurrentUser\TrustedPublisher` 与 `Cert:\CurrentUser\Root`。

脚本幂等，可重复运行；执行前后都会打印证书指纹，失败会给出中文原因。

### 诚实说明

- **未运行信任脚本时仍会看到提示**：这是自签名证书的正常现象，不是安装包损坏；
  请先核对发布页的 SHA256，再选择「更多信息 → 仍要运行」。
- **导入信任后**：属性 → 数字签名里能看到真实签名者，`Get-AuthenticodeSignature`
  的 `Status` 变为 `Valid`；但 SmartScreen 仍可能因「文件信誉不足」给出提示 ——
  自签名证书不会获得公开 CA 的信誉积累，这并不代表信任脚本没装好。
- 自签名不附带 RFC3161 时间戳；证书到期（2036 年）后需要重新生成证书并重新分发
  信任脚本，届时签名指纹会变化。

### 重新签名（维护者）

```powershell
# 首次生成/复用证书（私钥落在 signing\private，口令由 DPAPI 保护，均被 .gitignore 忽略）
powershell -ExecutionPolicy Bypass -File packaging\new_signing_cert.ps1
# 对现有安装包签名，并打印 Status / 签名者 / 指纹
powershell -ExecutionPolicy Bypass -File packaging\sign_installer.ps1
```

完整打包脚本 `packaging\build_windows_installer.ps1` 会在编译出 exe 后自动签名，
再计算 `SHA256SUMS.txt`；没有可用证书时会明确打印「本次未签名」，不会静默跳过。

## 六、已知限制与未验证项

1. **代码签名是项目自签名证书**：安装包用本项目的自签名证书签名（主体
   `CN=鎏灏智能中枢, O=LiuHao AI OS, C=CN`），**不是**公开权威 CA 证书。
   未运行信任脚本的机器仍会看到「未知发布者 / Windows 已保护你的电脑」，这是
   自签名证书的正常现象；运行 `信任签名证书.cmd` 后，属性里可以看到真实签名者。
   详见上文「五、验证与信任安装包签名」。
2. **不含任何模型**：安装包与升级包都不含模型权重。首次使用需本机 Ollama 拉模型
   （约 2GB，自行安装 Ollama 并 `ollama pull`）或填云 API Key。
3. **首次启动是托盘常驻**：出现状态窗口与托盘图标，窗口只显示启动状态、不需要输入；建账号与选模型在「鎏灏首次设置」窗口里完成。**关掉状态窗口只是隐藏到托盘，网关继续运行**；要退出用托盘菜单“退出”。命令行模式需显式运行 `LiuHao.bat --console`（0.1.1+）。
4. **默认端口 8080**：**首启向导里可以换端口；日常启动如果端口被占，会告诉你占用原因并给出处理办法**（关掉占用程序再试，或改端口后重新启动），见第二节。
5. **已做真实安装冒烟（服务层）**：0.1.0 交付包（34,731,647 字节，
   SHA256 `01BE18BB…64DA2`）实跑过「静默安装到临时目录 → 启动服务 → 取得控制台首页
   → 注册并登录 → 配置本机 Ollama → 模型连通性探测 → `POST /v1/chat` 拿到真实回复
   → 静默卸载」。0.1.1 安装包另有真实 DeepSeek Key 复验：不重启后端（PID 16404→16404）
   保存 Key 后 `POST /v1/chat` 200 / 1.90s，证据
   `dist/release-gate/smoke/20261108-real-key/CONCLUSION.md`。未点击渲染后的前端表单：
   登录/发消息走的是控制台同一套 HTTP API，前端视觉仍在改动，UI 点击回归留待前端冻结后做。
6. **卸载器**：静默卸载已验证——程序目录、桌面/开始菜单快捷方式、卸载注册表项移除，
   数据目录（按 `data-dir.txt` 实际位置，默认 `D:\LiuHaoData`）原样保留；
   交互式「是否删除数据」的“删除”分支已单独实测（静默卸载按安全默认保留）。
7. **命令行升级器只验证到「检查更新」**：清单可读，`up_to_date` / `update_available` /
   `invalid_manifest` 三种结果均已实测（`--release-dir`、`--manifest` 两种入口都跑过）；
   但「下载→校验→备份→应用→回滚」未在安装版上实跑。
8. **开发目录路径：运行文件命中 2（不是 0）**：`scripts/collect_diagnostics.bat:12`、`src/logging_config.py:189`；证据 `dist/release/dev-path-report.txt`（2026-11-08T12:23:16Z）。`-StrictReleaseDevPaths` 会把这类命中当作构建失败，本次构建**未开**该开关，所以包仍然出得来。
   `docs/` 里另有 484 处开发机历史链接（如 `D:\LiuHao-AI-OS`、`.venv\Scripts\python.exe`），
   不影响安装与升级，构建报告会列在“文档引用命中”里。
9. **清单版本号规则已与代码对齐**：`packaging.version.Version` 接受并归一化
   `v0.2.0`、`0.2.0-beta`；`docs/self-upgrade.md` §2.2.4 已改成一致描述，
   真正非法的是无法解析的字符串（如 `not-a-version`）。
10. **清单不能用 UTF-8 BOM**：升级器按 UTF-8 直接解析，带 BOM 会被判为非法清单；
    打包脚本写出的清单不带 BOM。
11. **安装版「填 Key 即用」已验证（单进程、无重启）**：安装到临时目录后只启动一次
    后端，通过控制台设置页同一组接口保存 DeepSeek Key（保存 0.047 秒），不重启直接
    发消息，1.39 秒拿到真实回复；全程同一 PID（14148）存活。保存响应、列表回读、
    聊天响应、服务日志与数据目录均无明文 Key（全量扫描命中 0）；列表只显示
    `sk-b…b47c` 这类脱敏串。故意写错一位 Key 时返回 `invalid_key` 与中文提示
    `API Key 无效或已失效，请到「系统设置 → 模型管理」更新密钥。`，无异常栈。未点击渲染后的表单，
    走的是设置页同一套 HTTP 接口。
12. **首分钟基准（机器耗时，三个「冷启动」数字口径不同，分开记）**：
    - 网关 `/v1/health` 就绪：3.58 秒（0.1.0 安装版，中文与空格路径专项；见 `END-USER-GUIDE.md`）。
    - 控制台页面可用：一般 8～15 秒（首次启动，双击到页面可交互，含前端加载）。
    - 完整冷启（空数据目录 → `/v1/health` 就绪）：15.42 秒（0.1.0 实测）；本轮 0.1.1 源码路径热启动实测 4.95～7.6 秒。
    安装 25 秒（默认路径；中文与空格路径 28 秒，0.1.0 交付包）；保存 Key 0.05 秒；首条回复 1.39 秒。即从安装
    完成到能对话，除去用户输入，系统侧约 17～20 秒；卡点主要是后端冷启动（约 15 秒），
    前端加载约 2 秒，未发现数据库迁移卡顿。用户手工输入账号/密码/provider/
    base_url/模型/Key 另需约 20～35 秒。
