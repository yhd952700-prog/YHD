# 中文文案与术语一致性审计（只读快照 · 2026-11-08）

> 范围：`apps/console/console/src`（69 个非测试 ts/tsx）、`src/`（546 个 py）、`installer/`（含 `liuhao.iss`）、`scripts/`（138 个 bat/cmd/ps1/sh/py）、`packaging/`（34 个）、`docs/`（228 个 md/txt，排除 `archive/`、`evidence/`、`spec/incoming/`、SBOM 报告、REPO-LINE-CLOSURE）。
> 方法：脚本全量抽取 + 人工复核关键文件（控制台导航/管家/对话/登录/模型页、安装器欢迎页与 BEFORE-INSTALL、首启向导、启动器、CLI、知识库用户指南）。行号均对应当前工作区快照，仓库正有多条线并行改代码，本报告只记录、不修改。
> 品牌基准：`src/brand.json`（`LiuHao AI OS · 鎏灏智能中枢`）与 `docs/BRAND-CANON.md`。
> 关联：`docs/audit-docs-vs-ui-20261108.md` 已覆盖“文档描述与 UI 事实不符”，本报告只做语言与术语，不重复其功能类结论。

---

## 一、结论摘要

| 指标 | 结果 | 说明 |
|---|---|---|
| 错别字 | **3 处** | `c) 0)` 1 处；「秘钥」误写 2 行（共 3 个词） |
| 繁体/简体混用 | **0** | 严格繁体字扫描无命中（仅本报告引用字符清单的 1 行） |
| 全角/半角标点混用 | **1 处** | `ChatPanel.tsx` 用半角 `...`，其余 78 行用全角 `…` |
| 术语冲突 | **7 组** | 3 组需立即统一（员工对象、服务商、产品入口），4 组建议固化规则或先定义 |
| 经核对不算冲突 | **4 组** | 任务/目标/工单、管家/助理、模型/大模型/引擎、停用/禁用 |
| 未翻译英文/技术黑话 | **6 类 14 处** | 首屏 JARVIS 英文标语、`AI Provider`、`base_url`、`provider`、英文自我介绍等 |
| 语气不一致 | 「你」396 次 vs 「您」5 次（出现次数，录入本报告前统计） | 「您」全在 `ChatPanel`（4 次）+ 1 条 Slack 报错；另有「老板」称呼 |

### 最该先改的 5 处

1. **首屏人格与语气**：`ChatPanel.tsx:415-423,456,511` 仍是 `JARVIS` / `Your AI Business Partner` / 「老板」/「您」/ 半角 `...`，与全站「你」和「鎏灏」品牌冲突（P0，用户第一眼就看到）。
2. **安装器错字**：`installer/assets/BEFORE-INSTALL.txt:33` 的 `c) 0) 跳过`，多出的 `0)` 会让用户以为菜单编号坏了（P0，首次安装可见）。
3. **产品入口三种叫法**：`驾驶舱`（BEFORE-INSTALL:62,64；`start_liuhao.py:261,560,571,587,711,724,771,780`）/ `控制台`（`stage_payload.py:456`）/ `工作台`（UI 页面名），首次安装到启动全流程至少出现两种（P0）。
4. **「智能体」与「员工」**：UI 中「员工」158 次（导航为「我的员工」），docs 中「智能体」633 次（`agent_guide.md` 33 次、`build_your_first_agent.md` 39 次），用户照文档找不到界面入口（P1）。
5. **旧品牌仍在用户可见处**：`src/ai/chat.py:43,65` 打印 `鎏灏（LIUHAO X）`，`src/ai/planner/prompts.py:30` 用 `鎏灏（LIUHAO）AI 操作系统`，`docs/knowledge_base/{README.md:3,getting-started/installation.md:3,faq/faq.md:11,architecture/overview.md:3}` 仍以旧称作主名（P1）。`src/ai/liuhao.py:56` 当前人格已改为正确主名，但其 `:1` 文档串仍是旧称，且 `:58` 用了连字符 `LiuHao-AI-OS`。

---

## 二、A 组：错别字、标点、繁简

| # | 文件:行 | 原文 | 问题 | 建议改法 |
|---|---|---|---|---|
| A-1 | `installer/assets/BEFORE-INSTALL.txt:33` | `c) 0) 跳过 —— 没有 API Key 也可以先安装、先启动，模型之后再配。` | 菜单编号多出 `0)`，前一行是 `a)`、`b)`，此处明显误输入 | `c) 跳过 —— 没有 API Key 也可以先安装、先启动，模型之后再配。` |
| A-2 | `docs/knowledge_base/21-deployment/ci-cd-pipeline.md:403` | `秘钥在 Vault 轮换后，External Secrets Operator 更新 K8s Secret…` | 「秘钥」是「密钥」的常见误写；同句后半已正确写作「密钥管理」 | `密钥在 Vault 轮换后…` |
| A-3 | `docs/knowledge_base/21-deployment/helm-charts.md:358` | `**秘钥不入 Chart**：values 里永不放真实秘钥…` | 同一行两处「秘钥」 | `**密钥不入 Chart**：values 里永不放真实密钥…` |
| A-4 | `apps/console/console/src/components/ChatPanel.tsx:511` | `'请输入您的业务指令...'` | 半角三点省略号；全库其余 78 行使用全角 `…`；且用「您」 | `'请输入你的业务指令…'` |

补充：严格繁体字集合（員/務/體/設/啟/確/認/資/執/檢/錯/載/點/擊/檔/應/與/這/時/題/顯/選/擇/關/閉/開/網/連/線/統/軟/會/學/練/傳/輸/鑰/預/尋/儲/庫/訓/測）扫描除本报告该行字符清单外零命中；`docs/oss-radar` 等外部资料当前也未见繁体正文。文档散文中的半角 `, : ! ?` 未发现。

---

## 三、B 组：术语冲突

计数口径：UI=控制台非测试源码；docs=参与检查的 228 篇；code/scripts=src+scripts+installer+packaging。

### B-1 「员工 / 智能体」（P1，必须先统一）

| 叫法 | UI | docs | code/scripts | 主要位置 |
|---|---|---|---|---|
| 员工 / AI 员工 | **158** | 114 | **267** | `App.tsx:125`「我的员工」；`Butler.tsx:2-3`「员工执行」；`Directory.tsx`；`docs/account-hierarchy.md` |
| 智能体 | **2** | **633** | 5 | `Identity.tsx:113` `agent: '智能体'`；`Apps.tsx:47`；`docs/knowledge_base/user-guide/agent_guide.md:1,3,7`；`docs/tutorials/build_your_first_agent.md:1,3,18` |

**问题**：同一实体，界面上叫「员工」，用户指南/教程叫「智能体」；更糟的是 UI 内部也不一致——导航是「我的员工」，账号类型标签却是「智能体」。
**建议统一**：**对外一律「AI 员工」**（首次出现可写「AI 员工（智能体）」）；**架构/API/安全等技术文档保留「智能体（Agent）」**，并在 `docs/GLOSSARY.md` 增加「Agent = 智能体 = AI 员工（对外）」映射。这样改动集中在 2 处 UI 标签 + 用户指南/教程，不需要改导航。
**同组细节**：「AI 员工」带空格 47 处、「AI员工」不带空格 13 处 → 统一为 **`AI 员工`**。

### B-2 「服务商 / 提供商 / 提供方 / Provider」（P1）

| 叫法 | UI | docs | code/scripts | 主要位置 |
|---|---|---|---|---|
| 服务商 | **38** | 21 | 8 | `ModelManager.tsx:273,325,362,538,754,769` |
| 提供商 | 0 | **36** | 0 | `docs/knowledge_base/24-security/gdpr-compliance-guide.md`（8）、`23-performance/api-rate-optimization.md`（8）、`20-advanced-patterns/circuit-breaker-advanced.md`（4） |
| 提供方 | **4** | 2 | 10 | `ModelManager.tsx:104,106`「请到提供方控制台…」 |
| Provider | — | — | — | `stage_payload.py:433`「AI Provider」；`first_run_wizard.py:13`（内部文档串） |

**问题**：同一个文件 `ModelManager.tsx` 里，列名和表单叫「服务商」，报错文案却叫「提供方」；文档另用「提供商」。
**建议统一**：用户界面与用户文档统一为 **「服务商」**；技术文档可写「服务商（Provider）」。删除「提供商/提供方」作为同义叫法。

### B-3 「控制台 / 驾驶舱 / 工作台」（P0，首次安装链路）

| 叫法 | UI | docs | code/scripts | 主要位置（用户可见） |
|---|---|---|---|---|
| 控制台 | 少量注释 | 多 | 多 | `packaging/stage_payload.py:456`「浏览器会自动打开控制台」 |
| 驾驶舱 | 0 | 多（产品叙事） | 多 | `installer/assets/BEFORE-INSTALL.txt:62,64`；`scripts/start_liuhao.py:261,560,571,587,711,724,771,780`；`packaging/stage_payload.py:787,790`（构建日志） |
| 工作台 | **页面名** | 页面名 | — | `App.tsx:128`「工作台」 |

**问题**：安装器说「打开驾驶舱」，装完后的 START-HERE 说「打开控制台」，启动器终端也打印「驾驶舱」；而 UI 里「工作台」又是其中一个页面。三个词指两个层级，用户无法判断是不是同一个东西。
**建议统一**：**整个 Web 产品界面 = 「控制台」**；**「工作台」只保留为页面名**；**「驾驶舱」退出用户文案**（可留在品牌叙事/设计稿）。至少先把 BEFORE-INSTALL 与启动器输出改成「控制台」。

### B-4 「账号 / 账户 / 用户名」（P1，同一页面混用）

| 叫法 | UI | docs | code/scripts | 主要位置 |
|---|---|---|---|---|
| 账号 | **184** | **594** | **609** | `Login.tsx:236,391,398,642,729` |
| 账户 | 12 | 48 | 25 | `Login.tsx:280,334,375,905,1180`（「创建账户」按钮） |
| 用户名 | 6 | 17 | 20 | `Login.tsx:905`「输入用户名和密码即可创建账户」 |

**问题**：`Login.tsx` 同一个登录/初始化页里，「账号」「账户」「用户名」三种叫法交替出现（标签「账号」、按钮「创建账户」、说明「输入用户名」）。
**建议统一**：面向用户统一 **「账号」**；「账户」只在合规/安全文档指抽象账户主体时保留；「用户名」改为「账号名」或删除。

### B-5 「API Key / 密钥 / 秘钥」（P2，规则固化）

| 叫法 | UI | docs | code/scripts | 说明 |
|---|---|---|---|---|
| API Key | 7 | 68 | 126 | 字段名、申请入口、安装器 |
| 密钥 | 38 | 248 | 237 | 中文正文、加密说明 |
| 秘钥 | 0 | 3 | 0 | 错写，见 A-2/A-3 |

**建议规则**：**界面字段/按钮用「API Key」**；**中文说明正文用「密钥」**（首次出现可写「API Key（密钥）」）；**禁止「秘钥」**。当前两者并存但未造成理解障碍，按规则固化即可。

### B-6 「工作区 / 工作空间」（P2，建议先定义再改）

| 叫法 | UI | docs | code/scripts | 主要位置 |
|---|---|---|---|---|
| 工作区 | **21** | **94** | 94 | `App.tsx:132`「工作区文件」 |
| 工作空间 | 3 | 8 | 1 | `Identity.tsx:120` `workspace: '工作空间'`；`accountCaps.ts` |

**建议**：以「工作区」为 UI 主词；`Identity` 的 `workspace` 作用域标签改为「工作区」。若产品确实要区分“账号级空间”和“文件区”，请在 `GLOSSARY.md` 写明两个定义，不要继续混用。

### B-7 「其他 / 其它」（P2，写法统一）

其他：UI 25 / docs 62 / code 51；其它：UI 3 / docs 25 / code 49（全库合计 143 vs 81）。**建议统一「其他」**（现代汉语规范推荐，且 UI 已是多数）。

### B-8 经核对不算冲突（不要机械合并）

- **任务 / 目标 / 工单**：`目标`(Goal) 与 `任务`(Task) 是两层真实对象；`工单` 22 次主要出现在 Jira 集成（9 次）与「转人工/知识评估」等用例，是外部系统或业务单据名词，不应改成「任务」。
- **管家 / 助理**：`管家` 184 次是产品角色（其中「鎏灏管家」42 次）；`助理` 10 次多为「个人助理」人格模板/教程示例，不是同一实体的别名。
- **模型 / 大模型 / 引擎**：`大模型` 仅 6 次，属泛称；`引擎` 113 次主要是「执行引擎 / 工作流引擎 / 规划引擎」，与「模型」不是同义词。建议正文统一「模型」，「大模型」只用于行业泛称。
- **停用 / 禁用**：`停用` 指员工状态，`禁用` 指权限/写操作，属不同语义，保持现状。

---

## 四、C 组：未翻译英文与技术黑话（用户可见）

| # | 文件:行 | 原文 | 问题 | 建议改法 |
|---|---|---|---|---|
| C-1 | `apps/console/console/src/components/ChatPanel.tsx:415` | `JARVIS` | 首屏人格名不在品牌规范内；产品是「鎏灏管家」，用户会以为装错产品 | 改为「鎏灏管家」或「鎏灏」 |
| C-2 | `ChatPanel.tsx:416` | `Your AI Business Partner` | 整句英文标语未翻译 | `你的 AI 业务伙伴` |
| C-3 | `ChatPanel.tsx:422-423` | `老板，我已准备就绪！` / `今天我可以为您分析什么？` | 「老板」称呼 + 「您」+ 疑问式，与全站「你」的命令式语气冲突 | `你好，我已准备就绪。` / `今天想让我分析什么？` |
| C-4 | `ChatPanel.tsx:456` | `请下达业务指令，Jarvis 将为您实时分析。` | 英文人格名 + 「您」 | `请下达业务指令，鎏灏管家会实时分析。` |
| C-5 | `packaging/stage_payload.py:433` | `③ 选择 AI Provider（可跳过）：` | `AI Provider` 未翻译，和界面「服务商」不一致 | `③ 选择模型服务商（可跳过）：` |
| C-6 | `installer/assets/BEFORE-INSTALL.txt:32`；`packaging/stage_payload.py:436` | `填 base_url、模型名和你的 API Key` | 把配置键名 `base_url` 直接给普通用户 | `填接口地址（base_url）、模型名和 API Key` |
| C-7 | `scripts/first_run_wizard.py:331` | `API base_url（以 /v1 结尾，如 https://api.deepseek.com/v1）` | 命令行向导直接暴露 `base_url` | `接口地址 base_url（以 /v1 结尾…）`（首次给中文解释） |
| C-8 | `scripts/first_run_wizard.py:317-318` | `1) Ollama 本地…` / `2) OpenAI 兼容云（base_url + API Key）` | 选项说明中英混排，普通用户不懂 `base_url` | `2) OpenAI 兼容云（接口地址 + API Key）` |
| C-9 | `src/ai/chat.py:47` | `help="provider 类型（ollama / mock / openai / deepseek / moonshot ...）"` | CLI 帮助暴露 `provider` | `help="服务商类型（ollama / mock / openai / deepseek / moonshot …）"` |
| C-10 | `src/ai/chat.py:67` | `print(f"  provider={st['provider']}  model={st['model']}")` | 把英文键名原样打印给用户 | `服务商=…  模型=…` |
| C-11 | `src/ai/chat.py:104` | `print(f"provider 初始化失败（{args.provider}/{args.model}）：{exc}")` | `provider` 未翻译 | `服务商初始化失败（…）：…` |
| C-12 | `src/ai/tools/builtin.py:219` | `"liuhao": "LiuHao AI OS is an autonomous AI operating system with multi-agent capabilities."` | 工具自述是整句英文，会被模型/用户看到 | 改为中文自述，或中英双语 |
| C-13 | `apps/console/console/src/components/ModelManager.tsx:119` | `请检查服务商、模型名与端点设置后重试。` | 「端点」是 endpoint 直译，普通用户不易懂（同文件其他地方叫「地址」） | `请检查服务商、模型名与接口地址后重试。` |
| C-14 | `apps/console/console/src/lib/knowledge.ts:440` | `…请先确认知识库 / embedding provider 的状态。` | 报错文案把 `embedding provider` 原样给用户 | `…请先确认知识库 / 嵌入服务商（embedding provider）的状态。` |

说明：`API Key`、`Ollama`、`localhost`、`JSON` 等已在产品中长期作为专有名词使用，不算未翻译；但首次出现时应给一句中文解释。

---

## 五、D 组：品牌口径（以 `brand.json` + `BRAND-CANON.md` 为准）

### D-1 旧主名仍出现在用户可见处（P1）

| 文件:行 | 原文片段 | 建议 |
|---|---|---|
| `src/ai/chat.py:43` | `description="鎏灏（LIUHAO X）命令行对话"` | `description="鎏灏智能中枢（LiuHao AI OS）命令行对话"` |
| `src/ai/chat.py:65` | `print("  鎏灏（LIUHAO X）— AI 操作系统")` | `print("  鎏灏智能中枢（LiuHao AI OS）")` |
| `src/ai/liuhao.py:1` | `"""鎏灏（LIUHAO X）主控 — 可对话的 AI OS 入口。` | 文档串可保留历史说明，但需标注「旧称」；对外输出不要用 |
| `src/ai/planner/prompts.py:30` | `你是鎏灏（LIUHAO）AI 操作系统的智能规划引擎。` | `你是鎏灏智能中枢（LiuHao AI OS）的智能规划引擎。` |
| `docs/knowledge_base/README.md:3` | `欢迎来到 LiuHao AI OS（鎏灏 X）知识库。` | `欢迎来到 LiuHao AI OS · 鎏灏智能中枢知识库。` |
| `docs/knowledge_base/getting-started/installation.md:3` | `安装和配置 LiuHao AI OS（鎏灏 X）。` | 同上 |
| `docs/knowledge_base/faq/faq.md:11` | `LiuHao AI OS（鎏灏 X）是…` | 同上 |
| `docs/knowledge_base/architecture/overview.md:3` | `LiuHao AI OS（鎏灏 X）是…` | 同上 |

合规保留：`docs/product/PM-PRD-v3.0.md:12`、`docs/ARCHITECTURE.md:22`、`docs/spec/UNIFIED-BLUEPRINT.md:195` 写作「（旧称 LIUHAO X / 鎏灏 X）」，属于命名沿革说明，**不算违规**；`docs/audit-docs-vs-ui-20261108.md`、`BRAND-CANON.md`、`packaging/brand_guard.py` 中的命中是审计/门禁文本本身，也不算。

### D-2 产品描述变体

- `AI 操作系统`：`src/ai/chat.py:65`、`src/ai/planner/prompts.py:30` 各 1 次；`AI操作系统`：docs 1 次。
- 品牌中文定位应以 `鎏灏智能中枢` 为准；「AI 操作系统」作为口语解释可接受，但首屏/CLI 标题建议用主名。
- `LiuHao-AI-OS` 连字符写法 224 处，多为服务名/包名/路径（`liuhao-ai-os`），属技术标识，**不算品牌违规**；但 `src/ai/liuhao.py:58` 的人格提示词里把产品写成 `LiuHao-AI-OS`，应改为 `LiuHao AI OS`。同段 `:56-57` 还向 AI 暴露了「十源 DNA（ULTRON / VISION / ADA / EDITH / FRIDAY / JARVIS / JOCaSTA / KAREN / ENOCH / ZOON）」内部代号，其中 `JARVIS` 已外溢到首屏文案（见 C-1），建议这段只在内部人格说明中使用，不进入用户可见输出。

### D-3 裸「鎏灏」简称

「鎏灏」作为词根出现在 UI 51 行、src 91 行、docs 143 行，其中大量是合规的「鎏灏管家 / 鎏灏智能中枢 / 鎏灏 OS」。真正需要决策的是裸简称，集中在 `apps/console/console/src/lib/errorMessage.ts:24,28,38`（「鎏灏的本地服务没有运行」「请打开鎏灏」「重启软件」）这类短提示。`BRAND-CANON.md` 未把裸「鎏灏」列入允许短名。
**建议**：要么在 `BRAND-CANON.md` 明确「鎏灏 = 口语简称，仅限界面短提示」，要么统一为「鎏灏智能中枢」。这是口径决策，不建议本轮批量替换。

---

## 六、E 组：语气一致性

- **人称**：「你」396 次，「您」5 次。5 次中 4 次集中在 `ChatPanel.tsx:410,423,456,511`，1 次在 `docs/knowledge_base/22-integrations/slack-integration-guide.md:249`。全站实际基调是「你」，应统一为 **「你」**，删除「您」。
- **称呼**：`ChatPanel.tsx:422` 的「老板」与全站中性口吻不符，建议改为「你好」。
- **句式**：控制台主体是「命令式 + 行动提示」（如 `请先选择服务商`、`请填写…`）；`ChatPanel.tsx:423` 是「疑问式 + 感叹号」（`今天我可以为您分析什么？`），且带「！」。建议统一为「陈述/提示 + 可选问句」，避免感叹号堆叠。
- **错误提示**：主体统一用「请…」「无法…」「还没有…」；`ChatPanel` 的「Jarvis 将为您…」属于第三人称播报，与「请…」体系不一致。
- **建议声调规范**：第二人称「你」；不用「老板/您」；陈述句优先；错误提示给「现象 + 下一步动作」；中文正文用全角标点，省略号用 `…`。

---

## 七、F 组：并行开发与半成品观察

1. `ChatPanel.tsx` 的 `jarvis-zone` / `jarvis-*` 类名与 JARVIS 文案，明显是旧演示皮肤残留，与新品牌和当前导航（鎏灏管家/我的员工）不是一套。**记为历史半成品，不修**，但列为本报告 P0。
2. `docs/audit-docs-vs-ui-20261108.md` 是同日另一条线的“文档 vs UI 事实审计”，其中 P2-5 已提旧品牌；本报告与其结论一致，不重复其功能类问题（端口、版本号样例、Ollama 必需等）。
3. 仓库存在 `dist-mid-*` 构建产物目录，未纳入文案扫描；`packaging/stage_payload.py` 的构建日志（`:787,790`）使用「驾驶舱」，只影响构建者，不影响终端用户，但建议顺手统一。
4. 未发现“TODO/Lorem/Coming soon”类占位文案进入用户界面；`src/adapters/observability/*` 标注 `[LEGACY / NOT WIRED]` 属代码注释，不计入。

---

## 八、建议修复顺序（供后续执行线使用）

### P0（首次安装/首次打开即可见）
1. `installer/assets/BEFORE-INSTALL.txt:33`：删除 `0)`。
2. `ChatPanel.tsx:415-423,456,511`：JARVIS → 鎏灏管家；英文标语翻译；「老板/您」→「你」；`...` → `…`。
3. 安装链路统一「控制台」：`BEFORE-INSTALL.txt:62,64`、`scripts/start_liuhao.py:261,560,571,587,711,724,771,780`（及同文件其他用户可见「驾驶舱」），`packaging/stage_payload.py:456` 已正确，作为基准。

### P1（用户照文档操作会迷路 / 品牌违规）
4. 「智能体 → AI 员工」：`Identity.tsx:113`、`Apps.tsx:47`；`docs/knowledge_base/user-guide/agent_guide.md`、`docs/tutorials/build_your_first_agent.md` 等用户指南/教程；技术文档保留并加 GLOSSARY 映射。
5. 旧主名：`src/ai/chat.py:43,65`、`src/ai/planner/prompts.py:30`、`src/ai/liuhao.py:1`（文档串）、`docs/knowledge_base/README.md:3`、`getting-started/installation.md:3`、`faq/faq.md:11`、`architecture/overview.md:3`。
6. 服务商统一：`ModelManager.tsx:104,106`（提供方 → 服务商）；docs 中「提供商」按“服务商（Provider）”改写。

### P2（写法/规则固化）
7. `Login.tsx` 账号/账户/用户名统一为「账号」。
8. 「秘钥」→「密钥」（A-2/A-3）。
9. `其它` → `其他`；`AI员工` → `AI 员工`；`工作空间` → `工作区`（或先在 GLOSSARY 定义）。
10. `chat.py` 的 `provider` 帮助/状态输出中文化；`stage_payload.py:433` 的 `AI Provider` → 「模型服务商」；`base_url` 首次出现加中文解释。

### 验收方式（只读复核即可）
> 下面命令均需排除本审计报告自身（它引用了错字与旧名原文），否则命中会来自 `docs/audit-copywriting-20261108.md`。
```powershell
# 错字归零
Select-String -Path installer\assets\BEFORE-INSTALL.txt -Pattern 'c\) 0\)'
Get-ChildItem docs -Recurse -Include *.md | Where-Object { $_.Name -notlike 'audit-copywriting*' } | Select-String -Pattern '秘钥'
# 旧主名在用户可见文档归零（允许 BRAND-CANON/审计/门禁文本）
Get-ChildItem docs\knowledge_base,src\ai -Recurse -Include *.md,*.py | Where-Object { $_.Name -notlike 'audit-copywriting*' } | Select-String -Pattern 'LIUHAO X|鎏灏 X'
# 人格统一（ChatPanel 内应无 JARVIS/您/老板）
Select-String -Path apps\console\console\src\components\ChatPanel.tsx -Pattern 'JARVIS|Jarvis|老板|您'
# 入口统一（安装器与启动器不再出现驾驶舱）
Select-String -Path installer\assets\BEFORE-INSTALL.txt,scripts\start_liuhao.py -Pattern '驾驶舱'
```

---

## 九、方法与覆盖说明

- 全量抽取脚本临时放在 `%LOCALAPPDATA%\Temp\liuhao_audit_copy.py`、`liuhao_audit_ui.py`，未写入仓库。
- 覆盖：控制台 69 个非测试 ts/tsx、src 546 个 py、scripts 138 个批处理/脚本、docs 228 篇、installer 3 个文件、packaging 34 个文件。
- 排除：`node_modules`、`dist*`、`__pycache__`、`.venv`、测试快照、`docs/archive`、`docs/evidence`、`docs/spec/incoming`、SBOM/仓库收尾报告。
- 检索式（摘要）：繁体字集合、中文相邻半角标点、概念词两两 / 多组计数、`principal|scope|token|payload|endpoint|artifact|session|Provider|base_url|AI Provider`、`你|您`、`JARVIS|老板`、旧主名与错字变体。
- 局限：`aria-label`/动态拼接文案、运行时后端返回的异常文本可能未全部覆盖；建议修复后用上面的 PowerShell 验收命令再跑一遍。

---

## 十、执行记录（2026-11-08 收口）

> 本节记录实际改动。上文行号对应审计时快照；改动后行号已位移，以文件内容为准。

### 已改（本线完成）

| 组 | 文件 | 改动 |
|---|---|---|
| P0-1 | `apps/console/console/src/components/ChatPanel.tsx` | `JARVIS`→`鎏灏管家`；`Your AI Business Partner`→`你的 AI 业务伙伴`；`老板，我已准备就绪！`→`你好，我已准备就绪。`；`今天我可以为您分析什么？`→`今天想让我分析什么？`；`Jarvis 将为您实时分析`→`鎏灏管家会实时分析`；头像 `J`→`灏`；`请输入您的业务指令...`→`请输入你的业务指令…`；装饰字 `您`→`你` |
| P0-2 | `installer/assets/BEFORE-INSTALL.txt` | 删除多余的 `0)`；`base_url` 首次出现改为「接口地址（base_url）」 |
| P0-3 | `installer/assets/BEFORE-INSTALL.txt`、`scripts/start_liuhao.py` | 「驾驶舱」→「控制台」（安装器 2 处、启动器全部用户可见处）；「旧称驾驶舱」说明保留在 `docs/GLOSSARY.md` 的 L-CORE 条目 |
| P0-4 | `apps/console/console/src/pages/Identity.tsx`、`Apps.tsx` | `agent` 标签「智能体」→「员工」 |
| P0-4 | `docs/tutorials/*`（7 篇）、`docs/knowledge_base/user-guide/*`（5 篇）、`docs/knowledge_base/README.md`、`docs/knowledge_base/faq/faq.md`、`docs/knowledge_base/troubleshooting/common_issues.md`、`docs/README.md`、`docs/release-announcement.md` | 用户指南/教程中的「智能体」→「AI 员工」（含中英空格、`多智能体`→`多 AI 员工`归一）；技术文档（`api-reference`、`core-concepts`、`best-practices`、`architecture`、`GLOSSARY`）保留「智能体」 |
| P0-4 | `docs/GLOSSARY.md` | Agent 条目改为「智能体 / AI 员工」并写明对外口径 |
| P0-5 | `docs/knowledge_base/{README,getting-started/installation,faq/faq,architecture/overview}.md` | 复核：品牌线已改为「鎏灏智能中枢」，本线无需再改 |
| P1-A | `docs/knowledge_base/21-deployment/ci-cd-pipeline.md`、`helm-charts.md` | 「秘钥」→「密钥」 |
| P1-C | `packaging/stage_payload.py`、`scripts/first_run_wizard.py`、`src/ai/chat.py`、`src/ai/tools/builtin.py`、`apps/console/console/src/lib/knowledge.ts` | `AI Provider`→「模型服务商」；`base_url` 首现加中文解释；`provider` 帮助/状态/报错→「服务商」；英文工具自述改中文；`embedding provider`→「嵌入服务商（embedding provider）」 |
| P1-C | `apps/console/console/src/components/ModelManager.tsx` + `ModelManager.test.tsx` + `src/gateway/models.py` + `docs/END-USER-GUIDE.md` | 「端点」→「接口地址」（界面、测试期望、后端兜底文案、文档引用同步） |
| 顺带 | `apps/console/console/src/pages/Directory.tsx`、`Projects.tsx` | 用户可见「我AI员工」→「我的员工」；`创建 Goal`→`创建目标` |

### 未改（交给对应线 / 不在本次范围，已记录）

1. **代码侧旧主名**：`src/ai/chat.py:43,65`、`src/ai/planner/prompts.py:30`、`src/ai/liuhao.py:1` —— 按指示留给品牌线（`chat.py` 当前已在暂存区被品牌线改过，本线未动主名行）。
2. **`docs/spec`/`docs/architecture`/`docs/operations`/`docs/l10k` 等内部工程文档**仍大量使用 `LIUHAO X v3.0` 作为项目代号；这些属品牌线与 v3.0 冻结文档，本次未动用户可见 4 篇之外的内部文档。
3. **B-2「服务商 / 提供商 / 提供方」**、**B-4「账号 / 账户 / 用户名」**、**B-6「工作区 / 工作空间」**、**B-7「其他 / 其它」**、`Login.tsx` 三词混用 —— 属 P2/未列入本次 P0+P1，未改。
4. **`packaging/stage_payload.py` 构建日志**（`:787,790` 一带）仍有「驾驶舱」，仅构建者可见，未改。
5. **并发线新增文件 `apps/console/console/src/pages/About.tsx`**（未跟踪，14:48 创建）触发 `uiCopyGate`：`data\model_config.key` 机器字段名直接上屏。属他人半成品，按约定只记录不修；该文件收口后需重跑门禁。

### 验收结果（本次实跑）

```text
python scripts/check_docs_ui_terms.py   → [OK] 文档↔UI 检查通过（13 份用户文档，24 个界面名词）
python -m py_compile chat.py builtin.py first_run_wizard.py stage_payload.py models.py → exit 0
npx vitest run（全量 36 个测试文件）→ 361 passed（14:45）
npx vitest run src/lib/uiCopyGate.test.ts（复跑）→ 18/19，唯一失败为并发新增 About.tsx，见上
```

PowerShell 复核（排除本报告自身）：

```text
installer/ 与 scripts/start_liuhao.py 中「驾驶舱」→ 0
docs 中「秘钥」→ 0
BEFORE-INSTALL.txt 中 `c) 0)` → 0
ChatPanel.tsx 中 JARVIS / Jarvis / 老板 / 您 → 0（命中的 `...` 均为 JS 展开语法）
用户指南/教程/FAQ/排障中「智能体」→ 0
```
