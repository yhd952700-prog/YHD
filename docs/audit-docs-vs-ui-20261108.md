# 文档 vs 控制台 UI 对账审计（2026-11-08）

> **收口状态：本报告 14 项已全部修复或复核完毕（2026-11-08）。** 下文保留“原文/界面事实/建议改成”，仅作对照与追溯；
> 当前文档状态以此前实际改动为准。复验命令：`python scripts/check_docs_ui_terms.py`。
>
> 审计快照：`HEAD ca50c17f`，工作树有 161 个未提交改动路径；本报告只对当前快照中的导航标签、页面标题、按钮文案、路由与默认路径做事实核对。

---

## 0. 结论（先看这里）

- **共确认 14 处真不一致**，其中 **4 处会直接让用户卡住**（按文档操作找不到入口或直接走错路）：
  1. 手机端文档让点「工作台」，但手机底部导航没有这个入口（P0-1）。
  2. “快速开始”让进「智能体管理」点「新建智能体」，控制台里没有这个页面/按钮（P0-2）。
  3. 文档说“本机生成恢复码只显示文件名、可能拿不到码”，实际界面已直接显示恢复码；文档会把用户劝退到“联系支持”（P0-3）。
  4. 使用指南让点“左侧会话 → 新建会话”，左侧没有“会话”菜单，实际是「工作台/管家」里的「＋新会话」（P0-4）。
- **另有一组“整段已过时”的 API 示例**：`/v1/agents`、`/v1/tasks`、`/v1/providers/models` 在网关中都不存在，照抄会 404（P1-1）。
- **用户点名的三条路径全部一致**：程序 `D:\LiuHao`、数据 `D:\LiuHaoData`、回退 `%LOCALAPPDATA%\LiuHao\data`，以及 `data-dir.txt`、`current_url.txt` 的写法均与安装器/启动器一致（见 §6）。
- **没有“文档写两步、实际要五步”**：安装 6 步、首启向导 2 步、模型配置 4 步、恢复码重置 3 步，与实际页面/向导步骤数一致；问题集中在“步骤指向了不存在的控件”（P0-1、P0-2、P0-4）。
- **没有过时截图**：`INSTALL-WINDOWS.md` 未引用任何图片（无 `![`、无 `.png`），不存在截图描述与界面不符的问题。
- 旧版本号/旧品牌仍可见：`installation.md` 健康检查样例写 `"version": "3.0.0"`（当前 `0.1.1`），并仍在使用旧称「鎏灏 X」（P1-2、P2-5）。

严重度口径：

| 级别 | 含义 |
|---|---|
| P0 | 按文档操作会找不到入口/走进死路，用户直接卡住 |
| P1 | 严重误导或照抄即失败，但有可绕过的线索 |
| P2 | 文案/名称/示例不精确，增加排错成本 |
| P3 | 低风险表述缺失或旧称残留 |

---

## 1. 审计范围

已逐条对账的面向用户文档：

- `README.md`
- `docs/INSTALL-WINDOWS.md`
- `docs/END-USER-GUIDE.md`
- `docs/USER-GUIDE.md`
- `docs/faq.md`
- `docs/mobile-access.md`
- `docs/knowledge_base/getting-started/quickstart.md`
- `docs/knowledge_base/getting-started/installation.md`
- `docs/knowledge_base/user-guide/agent_guide.md`
- `docs/knowledge_base/faq/faq.md`
- `docs/tutorials/build_your_first_agent.md`
- `docs/release-announcement.md`（抽查聊天与模型入口描述）

界面事实来源：

- 导航与移动底栏：`apps/console/console/src/lib/nav.ts`、`apps/console/console/src/components/Shell.tsx`
- 页面与按钮：`apps/console/console/src/pages/*.tsx`、`apps/console/console/src/components/*.tsx`
- 默认路径/端口：`installer/liuhao.iss`、`scripts/start_liuhao.py`
- 后端真实文案与路由：`src/gateway/*.py`、`src/version.py`

---

## 2. P0：会直接让用户卡住（4 处）

### P0-1 手机端「工作台」入口不存在

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/END-USER-GUIDE.md:161`：“左侧点 **工作台**（手机端在底部导航）” |
| 界面事实 | 手机底栏由 `nav.ts:167-173` 生成，固定为 `总览 / 我的员工 / 管家 / (审批中心或工作区文件) / (我的或我的 Key)`；`Shell.tsx:164-186` 的 `.os-bottom-nav` 只渲染这些项，**没有 `data-key="workbench"`**。桌面侧栏才有「工作台」（`nav.ts:77-78`）。 |
| 影响 | 手机用户按第 4 步找不到「工作台」，无法完成“发第一条消息”。实际手机端应点底部「管家」。 |
| 建议改成 | “桌面端点左侧 **工作台**；手机端点底部 **管家**，在输入框里发第一句话。”不要写“手机端在底部导航”这种不存在的入口。 |

### P0-2 「智能体管理」页面与「新建智能体」按钮不存在

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/knowledge_base/getting-started/quickstart.md:135-137`：打开 `5173` → 进入「智能体管理」→ 点「新建智能体」。同一错误复制在 `docs/knowledge_base/user-guide/agent_guide.md:62-64`、`docs/tutorials/build_your_first_agent.md:112`。 |
| 界面事实 | 实际页面是 **「我的员工」**（`nav.ts:62-63`），页面内的创建卡片标题是 **「创建员工」**（`apps/console/console/src/pages/MyEmployees.tsx:399`），提交按钮也是 **「创建员工」**（同文件 `454-455`）。在 `apps/console/console/src` 全量检索 `智能体管理`、`新建智能体`，命中数为 **0**。 |
| 影响 | 用户按“3 步创建智能体”找不到入口，快速开始直接断在第二步。 |
| 建议改成 | 三处统一改为：“1. 打开控制台；2. 左侧进入 **我的员工**；3. 在 **创建员工** 卡片填写名字/职责/默认能力，点 **创建员工**。” |

### P0-3 “本机生成恢复码只显示文件名”的已知问题已过时，且会劝退用户

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/END-USER-GUIDE.md:210-218`（`211` 写“界面会提示‘恢复码已写入本机文件：recovery_code.txt’”，`215` 写“界面目前只显示文件名，不显示码……可能拿不到码”，`217-218` 引导联系支持）；`docs/faq.md:144-149`；`docs/USER-GUIDE.md:113-114`。 |
| 界面事实 | 登录页点「没有恢复码？在本机生成」后，界面直接显示真实恢复码：`Login.tsx:1063-1096`（`data-testid="recovery-code-value"`，文案“新的恢复码只显示这一次”），入口按钮在 `Login.tsx:1110-1117`。后端返回的 `recovery_code` 与提示见 `src/gateway/accounts.py:780-789`：“已生成新的恢复码并只显示这一次；本机加密交接文件已随取用删除。”交接文件确实随取用删除（`src/gateway/recovery_handoff.py:167` `unlink`）。 |
| 影响 | 用户被文档告知“这条路可能拿不到码、去找支持”，从而放弃一个实际可用的自救路径；同时按文档去找 `recovery_code.txt` 会找不到（已被删除）。这是最容易演变成“账号锁死”的错误指导。 |
| 建议改成 | 删除“0.1.1 已知问题”整段；改为：“点 **没有恢复码？在本机生成** 后，页面会直接显示新恢复码（‘新的恢复码只显示这一次’），立即抄下并填回上方「恢复码」；本机交接文件在取回后即删除，**不要去找 `recovery_code.txt`**。”`USER-GUIDE.md:114` 与 `faq.md:145-149` 同步删除旧说法。 |

### P0-4 “左侧会话 → 新建会话”不存在

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/USER-GUIDE.md:34`：“控制台左侧会话 → 新建会话 → 输入消息。” |
| 界面事实 | 侧栏没有任何「会话」菜单（`nav.ts:54-120` 全量导航中无“会话”项；`apps/console/console/src` 检索 `左侧会话`、`新建会话` 命中 0）。实际对话在 **「工作台」**（`Workbench.tsx:59-65`，卡片标题「直接对话」）或 **「鎏灏管家」**；新建按钮是 **「＋新会话」**（`apps/console/console/src/components/ChatPanel.tsx:550-551`）。 |
| 影响 | 用户按使用指南找不到“会话”入口。 |
| 建议改成 | “左侧进入 **工作台**（或 **鎏灏管家**）→ 在对话面板点 **＋新会话** → 输入消息。” |

---

## 3. P1：严重误导或照抄即失败（4 处）

### P1-1 快速开始的 API 示例整段失效

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/knowledge_base/getting-started/quickstart.md:24`（`/v1/providers/models`）、`:90`/`:147`（`/v1/agents`、`/v1/agents/.../execute`）、`:157`（`/v1/tasks/...`）、`:135`（`5173`）。 |
| 实际事实 | 模型列表是 `/v1/models`（`src/gateway/models.py:424`），Provider 列表是 `/v1/models/providers`（同文件 `451`）；员工是 `/v1/employees`（`src/gateway/ai_management.py:3989`、`4037`）；目标是 `/v1/goals`（同文件 `3766`、`3775`）。网关没有 `/v1/agents`、`/v1/tasks` 路由（`src/gateway/main.py:1526-1674` 注册的路由清单中不存在；`src/api/agents_api.py:27` 与 `src/api/tasks_api.py:21` 虽有 router 定义，但未被任何 `include_router` 挂载，属未接线模块）。`5173` 是源码开发端口；安装版控制台是 `8080`（`scripts/start_liuhao.py:83-84`）。 |
| 影响 | 按快速开始复制 curl 会直接 404，用户会误判为“安装失败/系统坏了”。 |
| 建议改成 | 整段按当前契约重写：`/v1/employees` 建员工、`/v1/goals` 下目标/派活、`/v1/models/providers` 查 Provider；端口按运行方式注明“源码 5173 / 安装版 8080”。若不想维护两套示例，至少加显著状态头：“本节示例对应旧 API，当前以 `/docs` 与 §API 参考为准”。 |

### P1-2 安装指南的健康检查样例与真实响应/版本不一致

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/knowledge_base/getting-started/installation.md:182-189`：预期 `{"status":"healthy","version":"3.0.0",...}`；同文件 `:217` 让访问 `5173`。 |
| 实际事实 | `/v1/health` 返回 `{"status":"ok","service":"liuhao-gateway","timestamp":...,"version":APP_VERSION,...}`（`src/gateway/health.py:68-75`），当前 `APP_VERSION = "0.1.1"`（`src/version.py:8`）。安装版端口 `8080`（`scripts/start_liuhao.py:83`），`5173` 仅源码开发。 |
| 影响 | 用户照样例比对，看到 `ok` 和 `0.1.1` 会以为“装错了/版本不对”；再按 `5173` 打开会连不上。 |
| 建议改成 | 样例改为 `"status": "ok"`、`"version": "0.1.1"`（或写“以实际响应为准，HTTP 200 即通过”）；访问地址按“安装版 `http://127.0.0.1:8080` / 源码 `5173`”分别标注。 |

### P1-3 手机端访问文档的端口与绑定地址全错

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/mobile-access.md:16-17`（`http://192.168.31.171:8000`、控制台 5173 代理到 8000）、`:24-27`（“API 默认绑定 `0.0.0.0`”、`127.0.0.1:8000/health`）、`:68`（放行 TCP 8000）。 |
| 实际事实 | 启动器默认 `DEFAULT_API_HOST = "127.0.0.1"`、`DEFAULT_API_PORT = 8080`（`scripts/start_liuhao.py:82-83`），注释明确“手机/其它电脑要用时必须显式 `--host 0.0.0.0`”（同文件 `80`）。Vite 代理目标是 `http://127.0.0.1:8080`（`apps/console/console/vite.config.ts:57-61`）。 |
| 影响 | 手机用户按 8000 访问必然失败；文档还把“仅本机可达”误写成“默认为手机访问设计”。 |
| 建议改成 | 全文 `8000 → 8080`；把“默认绑定 0.0.0.0”改为“默认仅本机；手机访问需显式加 `--host 0.0.0.0`（安装版按启动参数/配置处理）”；防火墙放行 `8080`；硬编码的 `192.168.31.171` 改成 `<本机 WLAN IPv4>` 占位并保留“如何查”的方法。 |

### P1-4 “Ollama 必需”与产品实际能力冲突

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/knowledge_base/getting-started/installation.md:20-26`，尤其 `:22` “Ollama（本地 LLM 推理，**必需**）”。 |
| 实际事实 | 首次向导提供 `1) Ollama 本地 / 2) OpenAI 兼容云 / 0) 跳过`（`scripts/first_run_wizard.py:316-319`）；`docs/USER-GUIDE.md:17-20` 也明确支持云端与“OpenAI 兼容”自定义端点；`ModelManager` 支持多种 Provider（`apps/console/console/src/components/ModelManager.tsx`）。 |
| 影响 | 只用云端模型的用户会误以为不装 Ollama 就不能用。 |
| 建议改成 | 改为“Ollama（可选，本地推理）；也可用 OpenAI 兼容云 / DeepSeek / Kimi / Anthropic / Gemini；未配置模型时聊天会明确报错，不会伪造回复”。 |

---

## 4. P2：文案与示例不精确（5 处）

### P2-1 诊断包按钮名写成「导出」，实际是「导出诊断包」

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/END-USER-GUIDE.md:192`、`docs/INSTALL-WINDOWS.md:51`、`docs/faq.md:43` 与 `:193`、`docs/USER-GUIDE.md:107`。 |
| 界面事实 | 设置页卡片「遇到问题」存在（`apps/console/console/src/pages/Settings.tsx:317`）；行标题是「一键诊断包」，按钮实际文案是 **「导出诊断包」**（`apps/console/console/src/components/DiagnosticsExport.tsx:55`、`:61-63`）。 |
| 建议改成 | 统一写“**系统设置 → 遇到问题 → 一键诊断包 → 导出诊断包**”。 |

### P2-2 登录错误提示被引成「凭据无效」

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/END-USER-GUIDE.md:171`：“登录提示‘凭据无效’”。 |
| 界面事实 | 服务端返回的是 **“账号或密码不正确”**（`src/gateway/auth.py:506`）；登录页直接展示该消息（`Login.tsx:188`）。`src/` 全量检索 `凭据无效` 命中 0。 |
| 建议改成 | 改为“登录提示 **账号或密码不正确**”。 |

### P2-3 连通性失败提示表不是界面原文

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/END-USER-GUIDE.md:139-141`。 |
| 界面事实 | 实际文案（`apps/console/console/src/components/ModelManager.tsx:100-114`）为：“API Key 无效或没有权限。请检查 Key 是否填错、是否复制完整；建议重新生成后再试。”、“模型名不存在或该端点不支持这个模型。请核对模型名称后重试。”、“端点不可达。请检查端点地址、端口与网络连接是否正常。”、“连接超时。请检查网络，或稍后再试。”、“请求被上游限流（429）。请稍后重试。” |
| 影响 | 文档引号里的 `（401/403）`、`（404）` 和“端点不可达：检查 base_url…”在界面上并不出现，用户按字符串对照会以为又是别的问题。 |
| 建议改成 | 直接引用上述实际文案；或把表格改成“含义/怎么办”，并注明“以界面显示为准”。 |

### P2-4 “安装指南”只给源码端口 5173，未标注适用模式

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/knowledge_base/getting-started/installation.md:60-67`（启动器打印 5173）、`:217`（访问驾驶舱 5173）。 |
| 实际事实 | 源码启动器确实是 5173（`scripts/start_liuhao.py:83-84`）；但安装版固定 8080（同文件 `83`；`docs/USER-GUIDE.md:42`；`docs/INSTALL-WINDOWS.md:44`）。 |
| 建议改成 | 在两处标注“源码模式 5173；Windows 安装版 8080，见 `INSTALL-WINDOWS.md` / `END-USER-GUIDE.md`”。 |

### P2-5 旧品牌「鎏灏 X」仍残留在面向用户文档

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/knowledge_base/getting-started/installation.md:3`（“LiuHao AI OS（鎏灏 X）”）；`docs/knowledge_base/faq/faq.md:11`（同）。 |
| 实际事实 | `README.md:10` 明确“历史名称 LIUHAO X / 鎏灏 X 已收敛为旧称，不再作为主品牌引用”；安装器快捷方式名为“LiuHao AI OS · 鎏灏智能中枢”（`docs/INSTALL-WINDOWS.md:39`）。 |
| 建议改成 | 统一为“LiuHao AI OS（鎏灏智能中枢）”，删除「鎏灏 X」。 |

---

## 5. P3：低风险（1 处）

### P3-1 未标注「系统设置 / 模型管理」仅根账号可见

| 项 | 内容 |
|---|---|
| 文档位置 | `docs/END-USER-GUIDE.md:8`、`:109`、`:131`、`:150` 等多处直接写“系统设置 → 模型管理”。 |
| 界面事实 | `系统设置` 导航项 `requires: 'root'`（`apps/console/console/src/lib/nav.ts:120`），成员账号的侧栏底部不会出现该入口；模型管理内部另有“当前账号没有模型管理权限”的只读提示（`ModelManager.tsx:409`）。 |
| 影响 | 成员账号照文档找“系统设置”会找不到；首次安装的管理员账号不受影响。 |
| 建议改成 | 在首次出现处加一句“**系统设置 → 模型管理**（仅根账号可见；成员账号需由根账号操作）”。 |

---

## 6. 已核对一致（避免误报）

以下项目逐条对照后**没有发现不一致**，不需要改文档：

| 项目 | 文档 | 界面/代码事实 |
|---|---|---|
| 主导航名称 | `工作台`、`我的员工`、`恢复码`、`备份与迁移`、`成员与权限管理` | `nav.ts:54-120` 全部存在且同名 |
| 「系统设置」位置 | “左下角 系统设置” | `Shell.tsx:107-117` 的 `.os-sidebar-foot` 底部区域；`系统设置` 是 footer 项（`nav.ts:120`） |
| 「模型管理」卡片 | `系统设置 → 模型管理` | `Settings.tsx:182-184`，卡片 `id="settings-models"`，标题“模型管理” |
| 模型行按钮 | `测试连接`、`设为默认` | `ModelManager.tsx:598`、`:719`、`:728` 文案完全一致 |
| 恢复码页按钮 | `生成新的恢复码`、`下载恢复材料`、勾选“我明白轮换后旧恢复码立即失效” | `Recovery.tsx:144`、`:108`、`:136`；文档省略了“（未加密）”后缀，属可接受的简称 |
| 登录页按钮 | `忘记密码`、`没有恢复码？在本机生成`、`重置并进入` | `Login.tsx:430`、`:1116`、`:1104`，文案一致 |
| 诊断包结果提示 | “已保存：<文件名>”或“已开始下载：<文件名>” | `DiagnosticsExport.tsx:35-36`，一致 |
| 程序默认路径 | `D:\LiuHao` | `installer/liuhao.iss:47` `DefaultDirName=D:\LiuHao` |
| 数据默认路径 | `D:\LiuHaoData` | `installer/liuhao.iss:107`、`:409` |
| 数据回退路径 | `%LOCALAPPDATA%\LiuHao\data` | `installer/liuhao.iss:9`、`:391` |
| `data-dir.txt` | 程序目录下的实际数据目录标记 | `installer/liuhao.iss:138`、`:560` 写入；`scripts/launch_installed.py:62-66` 读取 |
| `current_url.txt` | `<数据目录>\config\current_url.txt`，含 `url=`/`port=`/`pid=` | `scripts/start_liuhao.py:174-182` 写入 `<数据目录>/config/current_url.txt` |
| `recovery_code.txt` 位置 | `<数据目录>\config\recovery_code.txt` | `src/gateway/recovery_handoff.py:39-40`，目录解析 `:73` |
| 安装版端口 | `8080`，被占用不自动顺延 | `scripts/start_liuhao.py:83`；`installer/liuhao.iss:74-75` 不自动关闭/重启应用 |
| 源码端口 | `5173`，`/v1` 代理到 `8080` | `vite.config.ts:52-61`；`README.md:36` 的 5173 属于源码模式，正确 |
| 首次向导步骤 | 2 步：建管理员 → 选 Provider（含 0 跳过） | `scripts/first_run_wizard.py:299`（`[1/2]`）、`:316-319`（`[2/2]`） |
| 安装步骤数 | 6 步：双击 → SmartScreen → 说明页 → 任务 → 目录 → 完成 | `installer/liuhao.iss:56`、`:69`（说明页）、`CloseApplications=no`；与文档无步骤数冲突 |
| 截图 | 安装文档未引用图片 | `INSTALL-WINDOWS.md` 检索 `![`/`.png` 命中 0 |

关于用户点名的“配置中心”示例：当前控制台没有「配置中心」，文档也没有把系统设置写成配置中心；二者都以「系统设置」为准，**不是不一致**。

---

## 7. 路径与步骤数专项结论

### 7.1 路径

三条默认路径全部一致，**无需修改**：

- 程序：`D:\LiuHao`（`installer/liuhao.iss:47`）
- 数据：`D:\LiuHaoData`（`installer/liuhao.iss:107`、`:409`）
- 回退：`%LOCALAPPDATA%\LiuHao\data`（`installer/liuhao.iss:9`、`:391`）
- 回退程序目录：`%LOCALAPPDATA%\Programs\LiuHao`（文档与安装器 `:375` 一致）

唯一与“路径”相关的错误是 `docs/mobile-access.md` 的端口 `8000`（见 P1-3），不是目录路径。

### 7.2 步骤数

| 流程 | 文档步骤 | 实际页面/向导步数 | 结论 |
|---|---|---|---|
| Windows 安装 | 6 步（`INSTALL-WINDOWS.md:27-39`） | 双击、SmartScreen、说明页、任务、目录、完成 | 一致 |
| 首次启动向导 | 2 步（建管理员 → 选 Provider） | 向导打印 `[1/2]`、`[2/2]` | 一致 |
| 模型配置 | 4 步（进设置 → 填 → 测试 → 保存/默认） | 表单 + 行内测试/默认 | 一致 |
| 恢复码重置 | 3 步（忘记密码 → 填码/新密码 → 重置并进入） | 同 | 一致 |
| 快速开始“方式三” | 3 步（开 5173 → 进智能体管理 → 新建智能体） | 页面/按钮不存在 | **不一致（P0-2）** |
| 手机发第一条消息 | 第 3 步：底部导航点工作台 | 底栏无工作台 | **不一致（P0-1）** |

---

## 8. 建议修复顺序（只改文档，不动代码）

1. 先修 4 个 P0：`END-USER-GUIDE.md:161`、`quickstart.md:135-137` 及两个复制处、`END-USER-GUIDE.md:210-218` 及 `faq.md`/`USER-GUIDE.md` 的恢复码错觉、`USER-GUIDE.md:34`。
2. 再修 P1：`quickstart.md` 的 API 示例整段、`installation.md` 的 health/version、`mobile-access.md` 的端口与绑定、`installation.md` 的“Ollama 必需”。
3. 最后做 P2/P3 的文案统一：诊断包按钮名、登录错误原文、测试失败提示原文、5173/8080 适用模式、旧品牌、根账号可见范围。
4. 建议在 `docs/knowledge_base` 顶部加一个状态头：明确“本目录部分内容对应旧 UI/旧 API”，避免用户继续按旧文档操作（本次不新建文件，仅建议）。

---

## 9. 附：关键证据索引

| 事实 | 文件:行 |
|---|---|
| 主导航与移动底栏 | `apps/console/console/src/lib/nav.ts:54-120`、`:167-173` |
| 侧栏底部设置项 | `apps/console/console/src/components/Shell.tsx:107-117` |
| 移动底栏渲染 | `apps/console/console/src/components/Shell.tsx:164-186` |
| 我的员工 / 创建员工 | `apps/console/console/src/pages/MyEmployees.tsx:399`、`:454-455` |
| 工作台对话面板 | `apps/console/console/src/pages/Workbench.tsx:59-65` |
| 会话下拉 / ＋新会话 | `apps/console/console/src/components/ChatPanel.tsx:542-551` |
| 恢复码一次性展示 | `apps/console/console/src/components/Login.tsx:1063-1096` |
| 本机生成入口 | `apps/console/console/src/components/Login.tsx:1110-1117` |
| 恢复码后端响应 | `src/gateway/accounts.py:780-789` |
| 交接文件删除 | `src/gateway/recovery_handoff.py:167` |
| 诊断包按钮 | `apps/console/console/src/components/DiagnosticsExport.tsx:55`、`:61-63` |
| 设置页模型管理 / 遇到问题 | `apps/console/console/src/pages/Settings.tsx:182-184`、`:317` |
| 模型测试文案 | `apps/console/console/src/components/ModelManager.tsx:100-114` |
| 模型行按钮 | `apps/console/console/src/components/ModelManager.tsx:598`、`:719`、`:728` |
| 登录错误原文 | `src/gateway/auth.py:506` |
| 健康检查响应 | `src/gateway/health.py:68-75` |
| 当前版本 | `src/version.py:8` |
| 员工/目标路由 | `src/gateway/ai_management.py:3989`、`:4037`、`:3766`、`:3775` |
| 模型/Provider 路由 | `src/gateway/models.py:49`、`:424`、`:451` |
| 启动器默认主机/端口 | `scripts/start_liuhao.py:80-84` |
| Vite 端口/代理 | `apps/console/console/vite.config.ts:52-61` |
| 安装器默认路径 | `installer/liuhao.iss:47`、`:107`、`:138`、`:391`、`:409`、`:560` |
