# 30 分钟真实办公走查报告

**日期**：2026-11-08　**对象**：`D:\LiuHao`（已安装旧版 **0.1.0**；工作区源码 **0.1.1**）
**视角**：普通上班族拿到安装包，走完「装 → 开 → 登录 → 没 Key 用 → 配模型派活 → 知识库 → 提醒 → 退出/数据/卸载」
**方式**：只读走查；未运行 `setup.exe`、未改 `dist\`、未改产品代码、未提交 commit；控制台用无头 Chromium 真机操作，截图全部为实际页面。
**结论**：🔴 **按本次剧本，产品尚不能交付给非技术用户**。8 步里 3 步完全走不通（派活、知识库、提醒），2 步能走但会误导（假成功、恢复码）。

---

## 0. 走查口径（先说清楚，避免误判）

1. 原安装 `D:\LiuHao` 自带的账号密码不可知，**没有冒用原账号**。走查在独立数据目录 `d:\LiuHao-AI-OS\.tmp\audit30\data` 里自建账号 `auditor`，等价于「一个全新用户」；不会影响原安装数据，也不影响下面的功能结论。
2. 没有真实云端 API Key。步骤 5/6 用本机 Ollama 代替「配 Key」：`qwen2.5:3b`（聊天）+ `nomic-embed-text`（向量）。**本机 CPU 时延不能代表云端 Key 的时延**，文内已单独标注；但流式接口崩溃、假成功、提醒失败等问题与用谁的模型无关。
3. 用户要求「不要执行 setup.exe」。因此**安装界面本身没有实跑**，步骤 1 用三类证据交叉判断：安装根目录的 `BEFORE-INSTALL.txt` / `START-HERE.txt`、当前 0.1.1 构建器的安装截图（同一产品族，非 0.1.0）、以及本机实际安装痕迹。结论里会写明「旧版实测 / 只是源码有 / 未验证」。
4. 应用内时钟显示为 11/07 16:xx–17:xx，本文按用户指定日期 2026-11-08 归档。

---

## 1. 一张表：步骤 / 耗时 / 卡点 / 严重度 / 一句话改法

| # | 步骤 | 实际耗时 | 卡点（用户视角） | 严重度 | 一句话改法 |
|---|---|---|---|---|---|
| 1 | 拿 exe 安装 | 未实测（按约束未跑 setup） | 程序路径页只说「装到哪个文件夹」，**不说数据去哪**；数据位置只藏在上一页 6 条长文里。旧装 `data-dir.txt` 实际指向 `C:\Users\...\AppData\Local\LiuHao\data`，与同目录文档写的 `D:\LiuHaoData` 不一致（该机是验收探针 `--data C` 安装，不能代表普通用户，但用户无法自证） | P2 | 路径页直接写两行：程序 `D:\LiuHao`，数据 `D:\LiuHaoData`（卸载默认保留） |
| 2 | 装完第一次打开 | 服务就绪 **5.7 s**（未配向量化）/ **24.1 s**（配本机向量化）；再 **1.7 s** 到登录框；旧装日志 **5.97 s** 就绪。首装另加命令行向导输入时间 | 0.1.0 **没有图形首启向导**：弹黑窗 CMD，顶部还打印 `LIUHAO_AUTH_SECRETS_FILE…` 等内部环境变量告警，再要求输入英文账号/密码；没有「下一步该干什么」。登录页唯一指引是「别关黑窗」 | P1 | 把 0.1.1 的 GUI 首启向导打进包；登录页加「3 步上手」卡片 |
| 3 | 注册 / 登录 / 忘记密码 | 登录 1–2 s；短密码提示即时；建号失败即时 | 密码规则只在 placeholder「至少 8 位，建议 10 位以上」；**注册页完全不提恢复码**，用户不知道要保存；忘记密码却必须恢复码。已有账号时点「创建账户」→ 通用错「操作没有完成…导出诊断包…追踪 ID」，没有原因 | P1 | 注册时强制弹出恢复码并让用户确认已保存，用人话解释；错误前台给原因，诊断包收进「详情」 |
| 4 | 没配 Key 直接发消息 | 约 **22 s** 出现演示回复 | 会明确说「模拟回复 / 去设置→模型配 Key」，有「去配置模型」按钮，三步可配（实测 Ollama 连通 3 s）；但文案混用「模型页 / 设置→模型 / 全部 Key」，还出现 `AI_PROVIDER_TYPE` 这种开发术语；导航没有「我的 Key」 | P2 | 统一入口名「模型与 Key」，删掉环境变量术语，按钮直达 |
| 5 | 让管家派「小王整理本周会议纪要」 | 建员工约 2 s；派活 **454 s（7 分 34 秒）** | 页面显示 **成功 / 完成 5/5 / 进度 100%**，但产物区明写「**没有向任务写入任何文件、没有可交付的产物**」；5 个任务全是 `verification=unverified / score=0.0`；摘要把 GitHub 仓库当成会议纪要；小王只分到 5 个任务里的 2 个。结果**不能直接用** | **P0** | 无产物或未验证 → 最高只能「部分完成/待确认」，禁止 100%/成功；生成文件任务必须落盘并显示路径 |
| 6 | 建知识库、扔文件、问只有文件里才有的问题 | 默认状态：**走不通**；强制启用后：上传即入库，检索 **2.7 s**，问答 **约 5 分钟**（脚本计时 307.9 s） | 空状态让用户「到系统设置选择向量模型」，**系统设置里根本没有向量模型入口**（只有聊天模型）；旧版 0.1.0 也不会自动探测本机 Ollama。用环境变量强制启用后检索正确，但问答先说「未能找到完整信息」又答对了，并显示 `search_memory 无匹配记忆`；状态区「已启用」与「不可用原因：尚未配置向量模型」同时存在 | **P0** | 系统设置补「向量模型」选择（自动列本机 Ollama + 云端）；清掉过期原因；回答带知识来源文件名 |
| 7 | 设「1 分钟后提醒我喝水」 | **306.9 s（约 5 分钟）**，然后失败 | 聊天区只有两段裸 JSON：`{"status":"error","error":"请提供 text…"}` + `调用工具 manage_reminders` 失败；没有助理回复；`reminders.db` **0 条记录**；无提醒 UI/通知；等 1 分钟没有任何弹出 | **P0** | 修工具入参与裸 JSON 兜底；加「提醒」入口和到点通知；不支持就直说「暂不支持」 |
| 8 | 找退出 / 开机自启 / 数据位置 / 卸载删不删数据 | 每页 1–2 s，但答案都找不到 | 控制台只有「退出登录」，**没有退出程序**；0.1.0 快捷方式指向 `LiuHao.bat`，黑窗即服务，**没有托盘图标**；安装任务页只有桌面快捷方式/安装后启动，**没有开机自启**，注册表也没有 LiuHao 启动项（实际没开，也没开关）；数据路径只在安装目录 `data-dir.txt` 和 `BEFORE-INSTALL.txt` 里；卸载保留数据仅文档/源码可证，**未实跑** | P1 / P2 | 系统设置加「关于」区：版本、数据目录、端口、开机自启开关、退出程序 |
| × | 跨步骤：真实模型链路 | 管家 7m34s；单句问答约 5 分钟 | `POST /v1/chat/stream` 至少一次服务端 `RuntimeError: No response returned`（`error.log` 16:53），页面永久「生成中」，**没有超时/重试/降级**；本机 Ollama 下每次回答 3–7 分钟。云端 Key 未验证，但挂死与超时兜底是产品问题 | **P0** | 流式心跳 + 超时重试 + 失败给「重试/换模型」按钮；首启明确推荐可用模型 |
| × | 品牌与文案 | — | 工作台仍是 `JARVIS / Your AI Business Partner`；开场白「老板，我已准备就绪！今天我可以为您分析什么？」；快捷指令是市场分析/销售机会/客户策略/SEO/数据分析，与「会议纪要」办公场景不符 | P2 | 换成办公助手口吻（会议纪要/周报/待办），去掉 JARVIS 残留 |

> 严重度口径：P0 = 普通用户主线走不通或给出错误结论；P1 = 能走但会卡住/被误导；P2 = 体验与文案问题。

---

## 2. 逐步记录（点了什么、等了多久、看到什么、卡在哪）

### 步骤 1　拿 exe，不知道装哪、会不会装 C 盘
**没跑安装程序（约束）。** 用安装根目录随附文档 + 当前 0.1.1 构建器截图核对：

- `BEFORE-INSTALL.txt` 第 2 条写得很清楚：程序 `D:\LiuHao`，数据 `D:\LiuHaoData`，D 盘不可用才回退到 `%LOCALAPPDATA%`，卸载默认保留数据（第 5 条，双确认才删）。
- 但安装向导的「选择目标位置」页（[截图](audit-30min-real-work-20261108/shots/01-installer-select-dir.png)）只显示程序目录 + 浏览按钮；**同页没有任何数据目录说明**。数据说明在上一页「安装前须知」的长文里（[截图](audit-30min-real-work-20261108/shots/01-installer-before-install.png)），6 条里第 2 条才提到，快速点「下一步」的用户会漏。
- 安装任务页（[截图](audit-30min-real-work-20261108/shots/01-installer-tasks.png)）只有「创建桌面快捷方式 / 安装完成后启动」，**没有开机自启选项**。
- 本机实际安装痕迹：`D:\LiuHao\data-dir.txt` 内容为 `C:\Users\Administrator\AppData\Local\LiuHao\data`。但 `launcher.log` 显示这是验收探针用 `install_launch_probe.py --data C:\...` 装的，**不是普通用户双击走默认路径的结果**；不能据此断言「默认装 C」。旧版文档与源码默认仍是 D 盘。
- 旧版 0.1.0 没有「安装位置说明.txt」（0.1.1 才加），所以用户装完后**没有一处能直观确认「数据到底在哪」**，只能翻 `data-dir.txt`。

**卡点**：安装 UI 把「程序位置」和「数据位置」拆成两件事，数据位置不显眼；无签名提示（第 1 条）是对的，但会让首次用户紧张。
**改法**：路径页直接两行大字；安装完成页给「位置说明」链接；把数据目录从长文挪到路径页。

### 步骤 2　装完第一次打开：多久看见登录框？有没有下一步指引？
**实测**：
- 旧装日志：`2026-11-07T11:32:24` 启动 → `网关就绪（5.97s）` → 驾驶舱 HTTP 200（0.0s）。浏览器再加载登录页约 **1.7 s**（本地实测 goto→登录表单）。
- 本次两次冷启动：未配向量化 **5.7 s** 就绪；配置本机 Ollama 向量化后 **24.1 s** 就绪（含模型预热）。都再 +1.7 s 到登录框。
- 首装还要先过命令行向导，用户手动输入时间另算。

**看到什么**：0.1.0 的首启是黑窗 CMD（[截图](audit-30min-real-work-20261108/shots/02-first-start-wizard.png)），顶部先打印：
`忽略外部设置的 LIUHAO_AUTH_SECRETS_FILE=... 重复设置 LIUHAO_ALLOW_ENV_OVERRIDE=1` 等内部告警，然后才是「鎏灏首次运行向导 / 未发现可登录的人类身份 / 管理员账号（英文，如 liuhao）」。
登录页（[截图](audit-30min-real-work-20261108/shots/03-login-page.png)）左侧是产品 Logo，右侧登录框 + 一句「启动后弹出的黑色命令窗口…关掉它控制台断开」。没有「下一步点哪」的图文指引。

**卡点**：普通用户看到英文日志 + 「人类身份」这种词会懵；0.1.0 没有 GUI 向导（源码 0.1.1 的 `scripts/first_run_gui.py`、`src/desktop/dpi.py` 未进旧包）。
**额外证据（旧包/新包差异）**：`dist\walkthrough-20261108` 的 0.1.1 首启曾直接崩 `ModuleNotFoundError: scripts.register_human_identity`；最新 `dist\payload` 已含该文件，但按约束未跑 setup 复验。**不能确认最新安装包已修好，只能说不确定。**

### 步骤 3　注册 / 登录：密码规则、忘记密码、恢复码
**点了什么**：登录页 →「创建账户」；输入 `auditor2` + `123`；再换 `Abc1234567` 提交。
**看到什么**：
- 密码规则只出现在 placeholder：「至少 8 位，建议 10 位以上」；短密码会 toast「密码至少 8 位」（[截图](audit-30min-real-work-20261108/shots/03-register-weak.png)）。
- 注册表单（[截图](audit-30min-real-work-20261108/shots/03-register-empty.png)）**没有一句提到恢复码**，也没有「请保存恢复码」的勾选。
- 已有账号时（本机已有 1 个）提交合法 10 位密码，页面返回通用错误：「操作没有完成，请稍后重试。若持续出现，请导出诊断包并附上提示信息。追踪 ID: …」（[截图](audit-30min-real-work-20261108/shots/03-register-failed.png)）。用户不知道是「不允许再建」还是「密码格式」还是「服务坏了」。
- 忘记密码页（[截图](audit-30min-real-work-20261108/shots/03-forgot-password.png)）要求输入恢复码；下方「没有恢复码？在本机生成」可点。
- 恢复码页（[截图](audit-30min-real-work-20261108/shots/03-recovery-code.png)）写得很全，但像运维手册：「明文只显示一次」「轮换后旧码立即失效」「不要放在同步盘或聊天工具」「备份包只能在新实例首次启动导入」「既没有恢复码、也无法访问本机数据目录时，只能重建身份体系或联系服务方」。

**卡点**：注册不教育、忘记密码才要求 → 用户第一次丢密码时才发现没有恢复码；错误提示不可读。
**改法**：注册最后一步强弹恢复码 + 必须勾「我已抄下」；错误文案给「已有账号，不能再建」这类人话。

### 步骤 4　没配 Key 就想用
**点了什么**：工作台 → 直接对话 → 输入「帮我把今天的工作安排成三条」→ 发送。
**等了多久**：约 **22 s**（首次请求，含预热）出现演示回复。
**看到什么**：
- 页面顶部横幅：「模拟回复 / 尚未接入真实模型…到「模型」页填写密钥并测试连通…可以先跳过」；按钮「去配置模型」。
- 输入框 placeholder：「未配置真实模型：当前发送将得到模拟回复」。
- 助手回复（[截图](audit-30min-real-work-20261108/shots/04-chat-no-model.png)）：
  「[演示数据] 还没有配置模型，因此无法生成真实回答。请到「设置 → 模型」填写 API Key（或配置 `AI_PROVIDER_TYPE`），然后重新发送这条消息。」
- 配模型页（[截图](audit-30min-real-work-20261108/shots/05-model-page.png)）：名称 → 服务商（OpenAI / DeepSeek / 本地 Ollama 等）→ 模型名 → 超时 → 启用/默认 → 测试连接 → 添加模型。**Ollama 实测 3 秒连通**，`qwen-local` 添加成功（[截图](audit-30min-real-work-20261108/shots/05b-model-added.png)）。

**答案**：提示**没有**说去「我的 Key」（导航里叫「全部 Key」）；三步能配上，但术语混用。
**卡点**：`AI_PROVIDER_TYPE` 是开发术语；「全部 Key」对普通用户像权限后台。
**改法**：统一叫「模型与 Key」；把环境变量名换成「填 API Key」；按钮一键直达。

### 步骤 5　配好模型后，让「鎏灏管家」安排小王整理本周会议纪要
**点了什么**：
1. 我的员工 → 建「小王 / 整理会议纪要与周报」，约 2 s（[建号后截图](audit-30min-real-work-20261108/shots/05c-employees-after.png)）。
2. 鎏灏管家 → 输入「让小王整理一下本周会议纪要，产出一份可以直接用的纪要文件」→ 交给管家（[运行中](audit-30min-real-work-20261108/shots/05e-butler-running.png)）。

**等了多久**：**454 s（7 分 34 秒）** 才出现「成功」。
**看到什么**（[结果列表](audit-30min-real-work-20261108/shots/05f-butler-result.png)、[目标详情](audit-30min-real-work-20261108/shots/05g-butler-detail.png)）：
- 顶部：目标 **成功**，**完成 5/5**，失败 0，进度 **100%**。
- 任务：调研会议纪要 / 整理会议纪要 / 写出要点 / 撰写总结 / 生成纪要文件，全部 `completed`；执行人：前 3 项是 `liuhao-default`，只有后 2 项是「小王」。
- 产物区：「本次执行没有向任务写入任何文件。没有可交付的产物。」并提示「如果所有任务标为已完成，却没有可交付的文件，本次结果可能不完整。」
- 后端记录：`state=completed`、`artifacts=[]`、`evaluation.outcome=SUCCESS`，但 task_completed trace 里是 `verification=unverified / score=0.0`。
- 内容：把 GitHub 项目 `ken861222/longhu-checkin`、`Nihilist-ZIO/prompt-library`、`tancheng33/tingo` 当成「本周会议纪要」来源，摘要说「已完成 5 项任务」。
- 工作区文件页（[截图](audit-30min-real-work-20261108/shots/08d-workspace-files.png)）**没有任何纪要文件**。

**结论**：从发到「看见结果」7.5 分钟，但结果**不能直接用**，而且系统自己知道没产物却仍判成功 —— 这是最伤信任的一处。
**改法**：把「产物缺失 / 未验证」作为最高优先级门禁：目标状态最多「部分完成（待确认）」，完成数不得含无产物任务，禁止 100%；「生成纪要文件」这类任务必须写盘并回传路径；派活时尊重「让小王做」的指定人。

### 步骤 6　建知识库、扔文件、问只有文件里才有的问题
**点了什么 / 看到什么**：
- 首次进知识中心（[截图](audit-30min-real-work-20261108/shots/06a-knowledge-empty.png)）：「可用性 未启用 / 向量模型 — / 不可用原因：尚未配置向量模型，因此暂时无法导入或检索。到「系统设置」选择向量模型并填写密钥后再试。」
- 打开系统设置（[截图](audit-30min-real-work-20261108/shots/05h-settings.png)）：只有**聊天模型**（名称/服务商/模型/超时/启用/默认/测试），**没有向量模型入口**。→ 对普通用户，这是死胡同。
- 旧版 0.1.0 也不会自动探测本机 Ollama（源码 0.1.1 已加自动探测，但未进旧包）。我用环境变量 `LIUHAO_EMBEDDING_PROVIDER=ollama` + `nomic-embed-text` 强制启用后：状态「已启用 / 本地 Ollama · nomic-embed-text / 文档数 1」（[截图](audit-30min-real-work-20261108/shots/06d-knowledge-imported.png)），但「不可用原因」仍残留「尚未配置向量模型…」。
- 上传 `本周会议纪要-蓝鲸77.txt`（唯一事实：预算 4,280,000 元 / 负责人林可 / 下次评审 2026-11-20 15:00）：文件一选中即入库，分块 1 个；「同步入库」按钮因为是文本模式而保持禁用，容易让人以为没成功。
- 检索测试（[截图](audit-30min-real-work-20261108/shots/06e-knowledge-retrieval.png)）：输入问题 → **2.7 s** 命中正确分块。
- 工作台提问同一问题（[截图](audit-30min-real-work-20261108/shots/06f-chat-kb-question.png)）：约 **5 分钟**（脚本计时 307.9 s）后答对「4,280,000 元 / 林可 / 2026-11-20 下午 3 点」，但正文说「根据提供的资料，未能找到完整的信息」，同时显示工具调用 `search_memory 无匹配记忆`。用户会怀疑这答案是编的。

**卡点**：知识库的「启用入口」不存在；状态自相矛盾；回答来源不透明；时延不可接受。
**改法**：系统设置加向量模型选择（自动探测 Ollama + 云端 Key）；清掉过期「不可用原因」；问答引用知识时分块标注「来自 本周会议纪要-蓝鲸77.txt」。

> 补注（A+ 已修复）：知识中心已改为三态文案 + 「重新检测」，系统设置新增 `settings-embedding` 只读卡；「到系统设置选择向量模型并填写密钥」与「系统设置加向量模型选择（含云端 Key）」均已取消，知识页按钮改为深链 `?p=settings&focus=embedding`。勿再据此复现。

### 步骤 7　设一个「1 分钟后提醒我喝水」
**点了什么**：工作台 → 直接对话 → 「1 分钟后提醒我喝水」→ 发送。
**等了多久**：**306.9 s（约 5 分钟）** 后失败。
**看到什么**（[截图](audit-30min-real-work-20261108/shots/07a-reminder-created.png)）：
- 聊天区只有两段裸 JSON：
  `{"status":"error","error":"请提供 text，例如「每天十点提醒我喝水」"}`
  `调用工具 manage_reminders` + 同样的 error。
- 没有助理回复、没有「已设置」确认、没有提醒列表/侧栏。
- `workspace\queue\reminders.db` 查询 `select count(*) from reminders` → **0**；等待到点也没有任何通知（[等待后截图](audit-30min-real-work-20261108/shots/07b-reminder-after-wait.png) 只是聊天文本）。

**卡点**：自然语言到工具入参完全没对上（工具要求 `text`，模型调用未提供）；失败后没有兜底，也不告诉用户「暂不支持提醒」。
**改法**：修 `manage_reminders` schema/解析与裸 JSON 兜底；加「提醒」入口 + 到点通知；不支持时 10 秒内明确拒绝，不要空转 5 分钟。

### 步骤 8　退出 / 开机自启 / 数据在哪 / 卸载删不删数据
**逐项答案**：
- **退出在哪**：控制台只有左下角「退出登录」（[系统设置](audit-30min-real-work-20261108/shots/05h-settings.png)），**没有「退出程序」**。0.1.0 桌面快捷方式指向 `D:\LiuHao\LiuHao.bat`，黑窗就是服务本体；关掉窗口 = 停止服务（`START-HERE.txt` 第 1 条也这么写）。**没有托盘图标**（源码 `src/desktop/tray.py` 存在，但该安装快捷方式没走 tray）。
- **开机自启怎么关**：安装任务页没有自启选项（[截图](audit-30min-real-work-20261108/shots/01-installer-tasks.png)）；`HKCU\...\Run` 无 LiuHao 项；启动文件夹也没有快捷方式。→ **实际没开机自启，也没有任何开关可关**。用户无法确认这一点。
- **数据存在哪**：控制台「系统状态」「备份与迁移」「工作区文件」都不显示路径（[系统状态](audit-30min-real-work-20261108/shots/08a-system-status.png)、[备份](audit-30min-real-work-20261108/shots/08c-backup.png)）；只能去安装目录看 `data-dir.txt` / `BEFORE-INSTALL.txt`。`BEFORE-INSTALL.txt` 第 5 条写「卸载默认保留数据，连续两次确认才删」，但旧 0.1.0 卸载器**未实跑**，不能打包票。
- **卸载会不会删数据**：0.1.1 源码 ISS 是 `uninsneveruninstall` + 双确认；0.1.0 只有文档可证。状态：🟡 未验证。

**改法**：系统设置加「关于 / 数据与退出」：版本、数据目录、端口、开机自启开关、退出程序；托盘菜单补自启。把「关黑窗=停服务」移到登录页显眼位置。

---

## 3. 「旧版 0.1.0 现状」vs「源码 0.1.1 已修、只是没进包」

> 用户告知旧装有 13 项落后于源码；本次不做全量 diff，只列**实证到的差异**。拿不准的单列「未验证」。

| 项目 | 旧版 `D:\LiuHao` 0.1.0 实测 | 源码 0.1.1 | 判定 |
|---|---|---|---|
| 版本 | `pyproject.toml` / `START-HERE.txt` 均 0.1.0 | 0.1.1 | 旧版确认 |
| GUI 首启 / DPI | `scripts/first_run_gui.py`、`src/desktop/dpi.py` **不存在** | 两者都存在 | 源码已修、未进旧包 |
| 管家「无产物仍成功」 | 实测：成功 5/5、100%、`artifacts=[]`、trace `unverified/0.0` | `src/ai/llm_support.py:400-430` 已加「无产出不得算完成、禁止 100%」的汇总约束 | 话术已修；**状态层是否同步未验证**，必须打包后复验 |
| 知识库向量模型 | 不自动探测本机 Ollama；只能靠环境变量/密钥 | `embedding.py` 已加本机 Ollama 自动探测 | 源码已修、未进旧包 |
| 知识库 UI 入口 | 「到系统设置选向量模型」，但设置无此控件 | 0.1.1 `Settings.tsx` 仍只有一句说明，无可选项 | **两版都有的产品缺口**，不是打包问题 |
| 提醒 | 无 UI；自然语言失败、0 条记录 | 无提醒 UI；工具仍在 | **两版都有的产品缺口** |
| 安装器数据目录 | 0.1.0 安装痕迹数据在 C（验收探针 `--data C`，非默认）；无「安装位置说明.txt」 | 0.1.1 默认 `D:\LiuHao` + 数据 `D:\LiuHaoData`，说明数据/卸载保留，回退时写实际位置 | 源码增强、旧包未体验证 |
| 首启崩溃 | 旧装 0.1.0 向导可跑（本机数据由探针建号） | `dist\walkthrough-20261108` 0.1.1 包首启曾崩 `ModuleNotFoundError: scripts.register_human_identity`；最新 `dist\payload` 已含该文件 | **中间构建的打包 bug**，最新包未复验，**不确定是否已修** |

---

## 4. 最该先改的三件事（按普通用户伤害排序）

### ① 管家「假成功」→ 必须让状态说实话（P0）
现在的判定链是「任务跑完 = success」，即使 `verification=unverified/0.0`、`artifacts=[]`、页面自己都写「没有可交付产物」，顶部仍是 **成功 / 100%**。用户会拿着一个空结果去汇报。
**最小改法**：
1. 目标终态由「有产物且通过验收」决定；无产物 → `partial / 待确认`，完成数不含它，进度不显示 100%。
2. 「生成文件」类任务必须落盘（工作区路径）并在产物区回传；没有路径就判失败。
3. 检索结果不能当会议纪要：检索类任务与办公产出任务分开标注，摘要禁止把 GitHub 仓库写成会议纪要。

### ② 提醒从自然语言到落库全断（P0）
用户说「1 分钟后提醒我喝水」，得到两段裸 JSON + 5 分钟空转 + 0 条记录 + 无通知。这比「不支持」更糟：用户以为设上了。
**最小改法**：
1. 修 `manage_reminders` 入参（text 必填）并在模型吐裸 JSON 时兜底解析；
2. 前端加「提醒」入口/侧栏 + 到点通知（托盘气泡或页面横幅）；
3. 解析失败时 10 秒内明确说「暂不支持提醒」，绝不静默。

### ③ 知识库引导去一个不存在的设置项（P0）
空状态让用户「到系统设置选择向量模型」，设置里却没有这个控件；旧版还不会自动探测本机 Ollama。普通用户 100% 卡死，「知识库」这个卖点等于不存在。
**最小改法**：
1. 系统设置加「向量模型」：自动列出本机 Ollama 已装模型 + 云端 OpenAI 兼容配置（Key）；
2. 启用后清掉残留的「不可用原因」，状态只显示一套真相；
3. 问答引用知识时标注来源文件名，避免「说没找到却又答对」。

> 补注（A+ 已修复）：本节「知识库 UI 入口 / 设置里没有控件」的两版缺口已收敛为只读 `settings-embedding` 卡 + `focus=embedding` 深链；不再新增「云端 Key」选择器。第 154、178、180 行的旧描述仅作当时现场记录，勿再据此复现。

### 并列 P0（建议同批修，不放进「三件」但必须做）
- **真实模型链路**：`/v1/chat/stream` 服务端 `RuntimeError: No response returned`，页面会永久「生成中」；必须加心跳/超时/重试/换模型，失败给可点按钮。本机 Ollama 单句 3–5 分钟、管家 7.5 分钟；云端 Key 时延未验证，但「卡死无兜底」是产品问题。
- **注册/恢复码教育**：注册页不提恢复码，忘记密码才要求；补一步「抄下恢复码」并解释人话。

---

## 5. 证据与截图索引

| 步骤 | 截图 |
|---|---|
| 1 安装 | [欢迎页](audit-30min-real-work-20261108/shots/01-installer-welcome.png) · [安装前 6 条](audit-30min-real-work-20261108/shots/01-installer-before-install.png) · [选择目录](audit-30min-real-work-20261108/shots/01-installer-select-dir.png) · [任务页](audit-30min-real-work-20261108/shots/01-installer-tasks.png) |
| 2 首开 | [命令行走查向导](audit-30min-real-work-20261108/shots/02-first-start-wizard.png) · [登录页](audit-30min-real-work-20261108/shots/03-login-page.png) · [总览](audit-30min-real-work-20261108/shots/03-overview.png) |
| 3 注册/恢复 | [注册空表单](audit-30min-real-work-20261108/shots/03-register-empty.png) · [短密码](audit-30min-real-work-20261108/shots/03-register-weak.png) · [建号失败](audit-30min-real-work-20261108/shots/03-register-failed.png) · [忘记密码](audit-30min-real-work-20261108/shots/03-forgot-password.png) · [恢复码页](audit-30min-real-work-20261108/shots/03-recovery-code.png) |
| 4 没 Key | [工作台](audit-30min-real-work-20261108/shots/04-workbench.png) · [演示回复](audit-30min-real-work-20261108/shots/04-chat-no-model.png) · [模型页](audit-30min-real-work-20261108/shots/05-model-page.png) · [测试连接](audit-30min-real-work-20261108/shots/05a-model-test.png) · [已添加](audit-30min-real-work-20261108/shots/05b-model-added.png) |
| 5 派活 | [员工页](audit-30min-real-work-20261108/shots/05c-employees-after.png) · [管家运行](audit-30min-real-work-20261108/shots/05e-butler-running.png) · [结果列表](audit-30min-real-work-20261108/shots/05f-butler-result.png) · [目标详情（无产物仍 100%）](audit-30min-real-work-20261108/shots/05g-butler-detail.png) · [工作区无文件](audit-30min-real-work-20261108/shots/08d-workspace-files.png) |
| 6 知识库 | [空状态](audit-30min-real-work-20261108/shots/06a-knowledge-empty.png) · [状态](audit-30min-real-work-20261108/shots/06b-knowledge-status.png) · [上传填写](audit-30min-real-work-20261108/shots/06c-knowledge-upload-filled.png) · [已入库](audit-30min-real-work-20261108/shots/06d-knowledge-imported.png) · [检索命中](audit-30min-real-work-20261108/shots/06e-knowledge-retrieval.png) · [问答](audit-30min-real-work-20261108/shots/06f-chat-kb-question.png) |
| 7 提醒 | [失败现场（裸 JSON）](audit-30min-real-work-20261108/shots/07a-reminder-created.png) · [等待后](audit-30min-real-work-20261108/shots/07b-reminder-after-wait.png) |
| 8 四问 | [系统状态](audit-30min-real-work-20261108/shots/08a-system-status.png) · [全部 Key](audit-30min-real-work-20261108/shots/08b-all-keys.png) · [备份与迁移](audit-30min-real-work-20261108/shots/08c-backup.png) · [系统设置](audit-30min-real-work-20261108/shots/05h-settings.png) |

**后台证据**（供开发复现，不属于交付物）：
- 目标记录：`.tmp\audit30\data\workspace\liuhao_goals.json` → `state=completed`、`artifacts=[]`、`evaluation.outcome=SUCCESS`、task trace `verification=unverified, score=0.0`。
- 提醒：`.tmp\audit30\data\workspace\queue\reminders.db` → `reminders` 表 0 行。
- 流式崩溃：`.tmp\audit30\data\logs\error.log` 2026-11-07 16:53:04 `path=/v1/chat/stream` → `RuntimeError: No response returned`。
- 旧装启动：`C:\Users\Administrator\AppData\Local\LiuHao\data\logs\launcher.log` 11:32:24 启动 → 网关就绪 5.97 s；`data-dir` 为探针 `--data C` 指定。

---

## 6. 走查约束与未验证项（请勿当成已完成）

- **未跑 setup.exe**：安装界面文案/回退行为、卸载删不删数据，均未实跑；只能引用 0.1.1 源码/ISS 与随附文档。
- **未用真实云端 Key**：步骤 5/6 用本机 `qwen2.5:3b` 代替；云端时延、失败提示、限流未验证。
- **未登录原安装账号**：原账号密码不可知；走查用独立数据目录 + 自建账号。
- **旧版 13 项落后**：只实证了上表几条，未做全量 diff；「源码已修」仅指文件存在/逻辑已加，**未打包复验**。
- 产品代码、`dist\`、安装目录均未改动；未提交 commit。
