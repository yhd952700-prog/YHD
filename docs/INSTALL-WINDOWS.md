# Windows 安装与卸载

> 适用：Windows 10 / 11 x64。
> 安装包**不需要管理员权限**，不写系统目录，不改系统环境变量。

## 一、当前交付状态（如实说明）

| 项目 | 状态 |
| --- | --- |
| payload 组装（含离线 Python 运行时、托盘依赖 pywin32/tkinter/Tcl） | 🟢 已实跑：12,732 文件 / 183,639,031 字节（约 175.1 MiB，未压缩） |
| payload 冒烟（启动→首页→注册→登录→发消息） | 🟢 本轮实跑通过（payload 内，mock 提供商） |
| Inno Setup 安装器脚本 | 🟢 已交付 `installer/liuhao.iss` |
| **`.exe` 安装包** | 🟢 0.1.0 交付包：34,838,298 字节（约 33.2 MiB）<br>SHA256 `6b0179d8372bb8830f55b6cb9835fcacd9a54642f7d5ebb16db5946359ad9e88`<br>本次最终包以转正后的 `SHA256SUMS.txt` 为准 |
| 升级发布包 `liuhao-0.1.0.zip` | 🟢 0.1.0 交付包 2,529,546 字节<br>SHA256 `29fecdf050d6c6feef7ba914ce20f12b43f05eb092ae805b8920a01fea9b6e12` |
| 升级发布清单 `LIUHAO_RELEASE.json` | 🟢 已生成，`python -m src.upgrade check --manifest/--release-dir` 均可读取（本轮为「已是最新 0.1.0」） |
| 安装后开机自启（托盘） | 🟢 已在真实安装目录验证：注册表值指向安装目录内的 `runtime\pythonw.exe` + `scripts\liuhao_desktop.py`，托盘窗口可创建 |
| 安装后的整机冒烟（安装 exe→启动→登录→发消息） | 🟢 0.1.0 交付包实跑通过（服务层，真实 Ollama 回复）；渲染后前端表单点击未做 |
| 卸载器「保留数据 / 删除数据」实机行为 | 🟢 静默卸载已验证：程序与快捷方式移除、数据保留；交互式“删除数据”分支已单独实测 |
| 代码签名 | 🔴 未签名（Windows 会提示「未知发布者」） |

> 前端（`apps/console`）正在重写，本轮产物会过时；最终安装包需在前端稳定后
> 重新执行一次 `packaging\build_windows_installer.ps1`。

## 二、用户安装步骤

1. 双击 `LiuHao-Setup-<版本>.exe`。
2. Windows SmartScreen 提示「Windows 已保护你的电脑 / 未知发布者」：
   点「更多信息」→「仍要运行」。这是**未签名安装包**的正常提示，不是病毒告警。
3. 安装前会显示一页中文说明（未签名、不含模型、数据保留策略）。
4. 选择附加任务：
   - ☑ 创建桌面快捷方式（默认勾选）
   - ☑ 安装完成后立即启动鎏灏（默认勾选，可取消）
5. 程序默认装到 `D:\LiuHao`（数据 `D:\LiuHaoData`），不需要 UAC 提权；
   D 盘不可用时自动回退到 `%LOCALAPPDATA%\Programs\LiuHao` / `%LOCALAPPDATA%\LiuHao\data`，
   实际位置见程序目录 `data-dir.txt`。老版本用户覆盖安装保持原位置不变。
6. 完成后双击桌面快捷方式或开始菜单「鎏灏 LiuHao AI OS」。

首次启动是一个命令行窗口：先进入向导（建管理员 → 选 AI Provider，可 `0) 跳过`），
成功后服务继续启动，浏览器会尝试打开 `http://127.0.0.1:8080`（没打开就手动访问，
端口被占时以窗口打印的地址为准）。运行期间不要关闭该窗口。向导细节与 Key 申请地址
见 [USER-GUIDE.md](USER-GUIDE.md)。

## 三、目录与数据

| 路径 | 内容 | 升级 | 卸载 |
| --- | --- | --- | --- |
| `D:\LiuHao`（回退：`%LOCALAPPDATA%\Programs\LiuHao`） | 程序、运行时、驾驶舱静态资源、配置模板 | 覆盖替换 | 删除 |
| `D:\LiuHaoData`（回退：`%LOCALAPPDATA%\LiuHao\data`） | 账号、API Key（加密）、对话、审计、工作区 | **保留** | **默认保留** |

实际数据目录以程序目录下的 `data-dir.txt` 为准。

卸载流程：

1. 设置 → 应用 → 「鎏灏 LiuHao AI OS」→ 卸载（或开始菜单「卸载」）。
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

1. `apps/console/console`：`npm install`（首次）→ `npm run build`
2. `packaging/stage_payload.py`：复制源码/配置/驾驶舱/脚本，组装离线运行时
3. 凭据泄漏检查：payload 中出现 `auth_secrets.json`、`human_identities.json`、
   `.env`、`*.pem`、`*.key` 会直接中止
4. `packaging/smoke_payload.py`：真实起进程做冒烟（`-SkipSmoke` 时跳过并在产物里标注未验证）
5. `packaging/make_release.py`：生成升级发布包 `liuhao-<版本>.zip` 与
   `dist\release\LIUHAO_RELEASE.json`（规范见 [self-upgrade.md](self-upgrade.md) §2.2），
   并回读自校验（整包 SHA256、字节数、包内版本、artifact 名）
6. 生成 UTF-8 BOM 的编译副本 → `ISCC.exe` 编译
7. 输出 `SHA256SUMS.txt`、安装包与升级包的体积/哈希

常用参数：

```powershell
# 前端已构建好，跳过 npm build
... -SkipFrontend

# 只出 payload，不编译安装器
... -SkipInstaller

# 跳过冒烟（不推荐；构建报告会标注未验证）
... -SkipSmoke

# 跳过升级发布包（只出安装器）
... -SkipRelease

# 把源码里的开发目录路径字面量当作构建失败（源码清理完成后建议打开）
... -StrictReleaseDevPaths
```

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

```powershell
.venv\Scripts\python.exe packaging\make_release.py
```

- 产物只允许 `src/`、`docs/`、`pyproject.toml`、`requirements.txt`、`oss-registry.yaml`
  与根目录包内清单 `LIUHAO_RELEASE.json`（`schema=liuhao.release-package/1`）；
- `sha256` 对**整包字节**计算，`size` 为字节数；算完后回读 ZIP 复核；
- 清单版本、包根清单版本、包内 `pyproject.toml` 版本三者必须完全相等，否则构建失败；
- **没有配置签名私钥时不写 `signature` 字段**（不会写空值或假签名）；
- 配置私钥即启用 Ed25519 签名（对 ZIP 完整字节签名，base64 写入清单）：

```powershell
$env:LIUHAO_UPGRADE_SIGNING_KEY = "D:\keys\liuhao-release-ed25519.pem"   # 未加密的 PKCS8 PEM
.venv\Scripts\python.exe packaging\make_release.py
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
iscc /DAppVersion=0.1.0 installer\liuhao.iss
```

（`installer\liuhao.iss` 默认从 `..\dist\payload\app` 取内容；如需自定义路径，
用构建脚本生成的 `dist\installer\build\liuhao.iss` 副本。）

## 五、已知限制与未验证项

1. **未签名**：安装包与升级发布包都**没有代码签名**。首次运行会弹
   SmartScreen「未知发布者」，需点「更多信息 → 仍要运行」。正式分发前需要代码签名证书。
2. **不含任何模型**：安装包与升级包都不含模型权重。首次使用需本机 Ollama 拉模型
   （约 2GB，自行安装 Ollama 并 `ollama pull`）或填云 API Key。
3. **首次启动有命令行窗口**：向导需要交互输入；关闭窗口即停止服务（托盘入口除外）。
4. **默认端口 8080**：被占用时启动器会自动顺延，控制台会打印实际地址。
5. **本轮已做真实安装冒烟（服务层）**：0.1.0 交付包（34,838,298 字节，
   SHA256 `6B0179D8…9E88`）已实跑「静默安装到临时目录 → 启动服务 → 取得控制台首页
   → 注册并登录 → 配置本机 Ollama → 模型连通性探测 → `POST /v1/chat` 拿到真实回复
   → 静默卸载」。未点击渲染后的前端表单：登录/发消息走的是控制台同一套 HTTP API，
   前端视觉仍在改动，UI 点击回归留待前端冻结后做。
6. **卸载器**：静默卸载已验证——程序目录、桌面/开始菜单快捷方式、卸载注册表项移除，
   数据目录（按 `data-dir.txt` 实际位置，默认 `D:\LiuHaoData`）原样保留；
   交互式「是否删除数据」的“删除”分支已单独实测（静默卸载按安全默认保留）。
7. **升级链路只验证到「检查更新」**：清单可读，`up_to_date` / `update_available` /
   `invalid_manifest` 三种结果均已实测（`--release-dir`、`--manifest` 两种入口都跑过）；
   但「下载→校验→备份→应用→回滚」未在安装版上实跑。
8. **开发目录路径**：运行文件命中 0（`-StrictReleaseDevPaths` 门禁通过）。
   `docs/` 里仍有开发机历史链接（如 `D:\LiuHao-AI-OS`、`.venv\Scripts\python.exe`），
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
    「API Key 无效：请检查是否填错或复制不完整」，无异常栈。未点击渲染后的表单，
    走的是设置页同一套 HTTP 接口。
12. **首分钟基准（机器耗时）**：安装 31.27 秒；启动到 `/v1/health` 可用 14.77 秒
    （空数据目录冷启 15.42 秒）；保存 Key 0.05 秒；首条回复 1.39 秒。即从安装
    完成到能对话，除去用户输入，系统侧约 17–20 秒；卡点主要是后端冷启动（约 15 秒），
    前端加载约 2 秒，未发现数据库迁移卡顿。用户手工输入账号/密码/provider/
    base_url/模型/Key 另需约 20–35 秒。
