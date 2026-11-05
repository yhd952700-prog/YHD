# 鎏灏 LIUHAO AI OS — 30 个方向 × 五大中心的战略架构研究

> 证据口径：本报告只依据本仓库当前真实代码路径与静态检索结果。**未运行服务、未跑测试、未启动 Docker、未做真实 E2E**（遵守本次边界）。因此“已接线”指存在非测试代码的调用路径，不等于运行时已通过验收。
>
> 产品形态前提：`start-liuhao.bat` 双击启动、单进程单端口、网关直接服务 `apps/console/console/dist`、本地 SQLite 持久化、可接本地 Ollama。默认不要求 Docker、外部数据库、常驻云服务、用户运维。凡与此冲突的方向，矩阵中明确标注。

---

## 0. 判定摘要

### 0.1 30 方向完成度分布（按当前代码）

| 完成度 | 数量 | 方向编号 |
|---|---|---|
| 真有（存在真实生产调用链） | 8 | 1、8、16、22、23、25、29、30 |
| 半成品（有真实模块，但缺 UI / 缺调用方 / 只覆盖局部） | 11 | 2、3、7、13、17、20、21、24、26、27、28 |
| 假实现（看起来存在，实际是桩、内存态或不可达模板） | 4 | 5、6、18、19 |
| 无任何实现 | 7 | 4、9、10、11、12、14、15 |

### 0.2 现阶段做了能立刻提升可用性的（4 项）

1. **知识闭环（方向 3）**：后端 `src/knowledge/documents.py` + `vector_store.py` + `embedding.py` 已是真实链路，但控制台“知识中心”只是 OpenAPI 端点列表（`apps/console/console/src/pages/Directory.tsx:489`），没有入库/检索界面；`/v1/chat` 与 AgentRuntime 也没有把知识检索结果注入上下文。补一块 UI + 一条检索注入，收益最大。依赖：可用了的 embedding provider（当前靠 `LIUHAO_EMBEDDING_*` 环境变量，模型管理页不配置 embedding，见 `src/knowledge/embedding.py:345`）。
2. **本地排程器（方向 8/20）**：目标执行链是真的，但除审计链 checkpoint 外没有通用任务调度（`src/reliability/checkpointer.py` 只调度审计检查点，不调度用户任务；全仓无 `taskiq/apscheduler/cron` 生产调用）。没有“每天/每周自动执行”，自动化工作流就只是手动触发。补一个进程内、SQLite 持久化的最小排程器即可复用现有 Goal 链。
3. **备份/恢复/导出（方向 28/23）**：`scripts/ops/backup.py` 有真实加密归档能力，但文件头明确写 “a CLI but no production caller wired”；控制台无入口。对单机个人数据产品，这是信任底座。
4. **产物索引与执行回溯（方向 26/7）**：`file_write` 真实写工作区，`src/gateway/files.py` 只读浏览已存在，但缺“按目标/按员工聚合产物”的索引；用户看得到文件，却难以从目标反查产物。

### 0.3 做了也没人用的（个人单机场景）

多智能体委员会/黑板式组织仿真（方向 2 的组织层）、数字孪生与仿真（10）、物联网（11）、机器人与具身（12）、空间智能（14）、工业自动化（15）、全球信息情报“平台”（9）、金融行情与交易（17 的交易层）、独立教育产品（18）、创意内容套件（19）、重型数字资产管理（26）、开源情报控制台（25 的产品化）、HA 集群（28 的集群层）、云边平台（13）。共同原因：要么需要用户没有的硬件/数据/外部服务，要么对“一个个人用户 + 一台电脑”不产生日常价值。

### 0.4 技术上做不到、或做了有法律/安全风险的

- **电脑自主操作（6）**：仓库有两层：`src/ai/host_command/` 的授权管线与 `src/ai/world_interface_shell_gate.py`；以及 `src/automation/` 的三重闸门、确认票据（SQLite）、急停、守卫、审计。**接线点是真的**——`src/ai/tools/kernel_tools.py:369` 的 `build_automation_tools` 被 `src/ai/liuhao.py:202` 的 `make_tools` 调用；但 `src/automation/tools.py:59-65` 在三闸未全开时返回 `[]`（`gate.py:15-20` 的 `LIUHAO_AUTOMATION_ENABLED` / `LIUHAO_AUTOMATION_BACKEND`），且 `src/automation/backends/` 只有 `fake.py`（文件头写明“不接触真实屏幕/键鼠”）。所以准确说法是：**接了线，但默认可用工具数为 0，执行后端是假的**。真正开放 OS 级控制需要用户授权、持续权限与不可运维的故障面，安全上不可接受。
- **AI 软件工厂（7）的“任意仓库自动改代码/跑测试/提 PR”**：当前只有受限 Python 计算与工作区写文件；放开等于供应链与任意代码执行风险。
- **金融建议/交易（17）**：需要实时行情、券商接口与合规定位，产品形态与法律责任都不成立。
- **医疗/法律建议（18/行业模板）**：`solutions/industry/{healthcare,legal}` 只有测试可导入的模板，不能作为产品能力露出。
- **数字身份/KYC（22 的外部身份层）**：本地 JWT/RBAC 是真的，但身份证件核验、外部 IdP 信任链不在单机产品范围内。
- **全网情报采集（9）**：常驻爬取涉及 ToS、反爬、隐私与运维，和“双击即用、无需运维”冲突。
- **机器人/IoT/工业执行（11/12/15）**：安全关键作动需要认证、硬件与现场运维。
- **“AI OS 取代 Windows 内核/驱动层”**：本项目的 OS 是应用层操作系统，不是内核；技术上不可行，也不应作为路线。

### 0.5 五大中心：应做信息架构，不应拆成五套系统

**结论：只做 IA（信息架构）+ 命名 + 权限边界，不新建五套后端。** 理由：当前是一个进程、一个主库 `liuhao_ai_os.db` 加若干模块库、一套 JWT/审计、一套 SQLite；拆成五套意味着五套部署、五个库、五份迁移、五套备份、五个鉴权面，直接违反“不要求 Docker/外部数据库/运维”。五个中心之间的边界也不干净：知识会被执行链消费，安全要拦执行，进化要评估执行。

---

## 1. 30 方向现状矩阵

> 完成度定义：**真有** = 非测试生产路径可追到真实实现；**半成品** = 有真实模块但缺 UI/调用方/只覆盖局部；**假实现** = 桩、内存态、不可达模板或仅枚举；**无** = 无任何实现。

| # | 方向 | 现有实现与真实路径 | 完成度 | 与单机产品形态冲突 | 建议 |
|---|---|---|---|---|---|
| 1 | 通用智能与认知架构 | `src/ai/liuhao.py`（人格助手/工具/画像注入）、`src/ai/providers.py`（真实 Provider）、`src/ai/model_router.py`（任务路由，接 `src/model_gateway`）、`src/ai/planner/`、`src/ai/reasoning/`、`src/ai/agent_runtime.py`、`src/kernels/context/` | 真有（工程能力）；“统一认知架构”理论层不存在 | 否 | 做（继续加固；不新造认知理论） |
| 2 | 多智能体协作 | `src/ai/collaboration.py`、`committee.py`、`blackboard.py`、`organization.py`、`employee.py`、`agent_factory.py`；生产侧 `src/gateway/ai_management.py` 只用 `EmployeeStore` 与 AgentRuntime 的单员工 resolver。`committee`/`blackboard` 无任何非测试引用 | 半成品（单智能体真，多智能体编排未接线） | 否 | 缓（先做 2–3 角色审查链，不做组织仿真） |
| 3 | 长期记忆与知识图谱 | `src/kernels/memory/`（SQLite、L1/L2、WAL）、`src/knowledge/documents.py`（真实 chunk→embedding→vector）、`src/knowledge/vector_store.py`、`src/ai/conversation_store.py`、`src/ai/personal_context.py`；KG 侧 `src/ai/memory/semantic_network.py` 仅测试引用 | 半成品（记忆/RAG 后端真，KG 未接线；无 UI；未注入对话） | 否 | 做（仅 RAG/记忆闭环；KG 只做设计） |
| 4 | 自主研究与科学发现 | 无任何实现；全仓无 `arxiv`/论文/假设/实验闭环，只有 `scripts/verify_*` 验证脚本与 `oss-ecosystem/state/*.json` 扫描快照 | 无 | 是（需常驻检索与外部论文源） | 只做设计 |
| 5 | 多模态智能 | `src/ai/perception.py` 只有文本感知；图像/音频/视频是显式“not implemented”占位（同文件注释 `modality understanding not implemented`）。`src/ai/providers.py` 有 `supports_vision` 标志，但 chat/agent 不接收图像。`plugins/{image,audio,video}` 只做本地文件处理且未加载 | 假实现 | 条件冲突（本地需 GPU；云端需外部 API） | 缓（先做截图→vision provider 的单向输入） |
| 6 | 电脑自主操作 | `src/automation/`（gate/guard/confirm/estop/audit/tools + `backends/fake.py`；文件头明确“本轮只有 fake；真实后端在下一轮接入”，默认三重关闭）、`src/ai/host_command/{broker,policy,approval,harness}.py`、`src/ai/world_interface.py`、`world_interface_shell_gate.py`。接线点真实（`src/ai/tools/kernel_tools.py:369` ← `src/ai/liuhao.py:202` 的 `make_tools`），但 `src/automation/tools.py:59-65` 在三闸未全开时返回空列表，唯一后端是 `fake.py`；无 gateway 路由、无真实屏幕/浏览器控制 | 假实现（安全地基真，执行后端 fake） | 是（OS 级权限/不可运维/安全） | 不做（保留地基，不接真实后端） |
| 7 | AI 软件工厂 | `src/ai/tools_local.py`（受限 Python + 工作区写文件）、`src/plugins/sandbox/backends/restricted_python_backend.py`、`src/ai/workspace.py`、`src/gateway/files.py`；无仓库编辑/测试运行/git/PR；`src/integrations/github_adapter.py` 无实例化方 | 半成品（仅最小产物生成） | 否（限沙箱内可做；放开即风险） | 缓（只做“生成+校验”单文件工作流） |
| 8 | 自动化工作流 | 真实引擎在 `src/ai/goal_task_graph.py` + `src/ai/agent_runtime.py`；`src/gateway/ai_management.py` 提供 goals 创建/停止/重规划与只读 workflows；控制台有 `CreateGoalForm`/`GoalActionButtons`（`OperatorControls.tsx`）。`src/workflow/workflow.py` 是显式占位；`langgraph_workflow.py` 未接生产；无通用任务调度器（仅 `src/reliability/checkpointer.py` 调度审计检查点） | 真有（手动执行链）；缺排程与工作流模板 | 否 | 做（补进程内排程 + 模板） |
| 9 | 全球信息情报 | 顶层 `plugins/rss`、`plugins/web_scraper` 等 61 个插件目录**未被运行时加载**（`src/plugins/manager.py` 默认扫描 `src/plugins`）；无调度器、无持续采集；`oss-ecosystem` 是离线扫描，不是情报流 | 无（产品层） | 是（需常驻采集/云服务） | 缓（只做本地订阅摘要，不做情报平台） |
| 10 | 数字孪生与仿真 | 无任何实现；全仓关键词仅命中 `docs/` 与 `oss-ecosystem/intents.yaml`、`state/*.json` 的扫描清单，`src/` 无仿真引擎 | 无 | 是（需物理模型/传感器/仿真环境） | 不做 |
| 11 | 物联网 | 无任何实现；`src/` 无 MQTT/Modbus/OPC-UA/Zigbee 代码 | 无 | 是（需设备与现场运维） | 不做 |
| 12 | 机器人与具身智能 | 无任何实现；无 ROS/`rclpy`/Gazebo/运动控制代码 | 无 | 是（需硬件、安全认证） | 不做 |
| 13 | 云端与边缘计算 | `deploy/cloud/`（源码副本）、`docker-compose.yml`、`Dockerfile*`、部署脚本；`start-liuhao.bat` 默认完全不依赖 Docker | 半成品（部署物料） | 条件冲突（作为默认形态冲突；作为可选出口不冲突） | 只做设计 |
| 14 | 空间智能 | 无任何实现；无 GIS/地图/点云/3D 场景代码 | 无 | 是（需空间数据/GPU/硬件） | 不做 |
| 15 | 工业自动化 | 无任何实现；无 PLC/SCADA/OPC-UA/产线协议代码 | 无 | 是（需工业设备与安全认证） | 不做 |
| 16 | 企业管理智能体 | 内部：`src/gateway/ai_management.py`（员工/目标/暂停/恢复）、`src/ai/employee_store.py`、`src/gateway/projects.py`、审批/审计。外部：`src/integrations/*_adapter.py` 无注册表消费方；控制台 `BusinessCenter` 明确写“没有接入真实账本数据源”（`Directory.tsx:477`） | 真有（内部运营）/半成品（外部系统） | 否（内部）；外部需 SaaS 账号 | 做（内部）；外部只做设计 |
| 17 | 金融经济分析 | `src/ai/economy.py`（Budget/Billing/Economy，真实算术）接入 `src/ai/agent_factory.py` 做员工 token 成本；无行情/持仓/账本；`solutions/industry/financial/*` 仅测试；`src/integrations/stripe_adapter.py` 无消费方；控制台无财务页 | 半成品（仅 token 成本） | 条件冲突（行情/合规需外部服务） | 缓（只做成本账本） |
| 18 | 个性化教育 | `solutions/industry/education/tutor.py` 等 38 个行业模板仅被 `tests/test_all_industry_solutions.py` 导入；`src/ai/domain_templates.py` 是权限域模板，不是教学模板；无学生模型/课程/进度 | 假实现 | 否 | 不做 |
| 19 | 创意生产 | 顶层 `plugins/image`（缩放/裁剪/EXIF 等文件处理，非生成）、`plugins/audio`、`plugins/video`，均未加载；无 TTS/STT/图像生成执行路径（`src/model_gateway/model_registry_extended.py` 仅有 `IMAGE_GENERATION` 枚举） | 假实现 | 条件冲突（生成需 GPU/云 API） | 不做 |
| 20 | 个人数字生活管理 | `src/ai/personal_context.py`（KAREN SQLite 画像，真实）、`src/ai/conversation_store.py`、`src/ai/liuhao.py:523` 注入画像摘要、`src/gateway/files.py`；无日历/待办/提醒运行时；`plugins/calendar` 等未加载；无排程 | 半成品 | 否 | 做（KAREN 已有，补提醒/日程） |
| 21 | 分布式系统 | `src/distribution/coordination.py`、`lock.py`、`election.py`、`msg_bus.py`、`bus_backends.py`；`src/kernels/execution/fence.py` 使用 file backend 做执行围栏。无多节点部署、无集群调度 API | 半成品（局部真，集群层无） | 是（多节点/外部协调服务） | 只做设计 |
| 22 | 网络安全与数字身份 | `src/security/`（20 个模块：JWT/RBAC/加密/审计）、`src/kernels/{identity,trust,policy,security,audit}`、`src/gateway/{identity,trust,audit,policy}.py`、审批中心 `ApprovalCenter`；无外部 IdP/KYC | 真有（本地身份/权限/信任）；外部身份无 | 否 | 做 |
| 23 | 数据基础设施 | `alembic/versions` 6 个迁移 + `liuhao_ai_os.db`；另有 `audit_store.db`、`conversation_store.db`、`memory_store.db`、`personal_context.db`、`data/model_registry.sqlite3`、`data/human_identities.sqlite3`、`.liuhao_jwt_revocation.db`，以及 `src/intel` 独立的 `data/intel/osi_intel.db`；`src/storage/`、`src/pipelines/` 为库；目标历史另落 JSON（`.liuhao_goals.json`），不在 SQLite | 真有（单机 SQLite）；无仓库/ETL/流式 | 条件冲突（数据仓库需外部 DB/运维） | 做（收口路径与备份，不做仓库） |
| 24 | 自适应与持续优化 | `src/kernels/evaluation/` 被 AgentRuntime 调用（真实评估）；`src/feedback/feedback_service.py` 用内存 `dict` 且 API 未挂载；`src/ai/evolution.py` 仅测试引用；`src/mlops/` 无生产调用方 | 半成品（评估真，反馈/进化假） | 否 | 缓（先持久化反馈与评估集） |
| 25 | 开源技术情报 | `src/intel/`（scanner/github_client/licenses/proposals/report/store，SQLite `data/intel/osi_intel.db`，CLI `python -m src.intel`，只给建议不自动动手）、`oss-ecosystem/pipeline/oss_radar.py`、`oss-ecosystem/state/scan-*.json`（快照时间 2026-09）、`oss-registry.yaml`、`docs/oss-radar/*`；无控制台/无调度 | 真有（CLI + SQLite） | 否（按需联网） | 缓（保留 CLI，不产品化） |
| 26 | 数字资产管理 | `src/gateway/files.py` 只读浏览、`src/ai/workspace.py` 写文件、`apps/console/src/pages/Files.tsx`；无资产目录/版本/权限/检索 | 半成品（只读文件面） | 否 | 缓（只做产物索引，不做 DAM） |
| 27 | 隐私计算与可信 AI | `src/knowledge/pii.py`、`src/knowledge/security.py`、`src/compliance/`（库）、`src/kernels/retention/`（无生产调用方）、审计证据链 `audit_store.db`；无 MPC/TEE/差分隐私；PII 仅被未接线的 `rag_pipeline.py` 使用 | 半成品（策略库真，未接写入路径） | 否（本地）；MPC 需多方 | 只做设计 |
| 28 | 高可用与灾难恢复 | `scripts/ops/backup.py`（加密归档，文件头声明“a CLI but no production caller wired”）、`src/sre/disaster/backup.py`（记录级完整性）、`src/backup/`、`src/resilience/`（重试/熔断）、`src/reliability/checkpointer.py`（审计链检查点，已挂 `app.state`）；无复制/故障切换/用户数据定时备份 | 半成品（工具真，未接线） | HA 是；本地 DR 否 | 缓（DR 先做，HA 不做） |
| 29 | 跨平台生态 | 单端口 HTTP/OpenAPI `src/gateway/`；`sdk/` 10 种语言（csharp/go/java/javascript/kotlin/php/python/ruby/rust/swift）；`src/desktop/`（pywin32 托盘、HKCU 自启、诊断包、防火墙只读探测，CLI `python -m src.desktop`）；`src/adapters/mcp/mcp_adapter.py`（B1，默认关闭、未接对话主链）；`apps/console/console/public/sw.js`（PWA）；无独立原生 UI App | 真有（Web/SDK/PWA/桌面集成 CLI） | 否 | 做（维护 SDK/PWA/桌面集成，不做原生 App） |
| 30 | 人机协同决策 | `src/gateway/policy.py`（审批签发/列表/撤销 + enforcement）、审批中心 UI `apps/console/src/App.tsx:535`、`src/kernels/trust`、审计链、`require_human_principal` 闸门；`src/ai/committee.py` 未接线 | 真有 | 否 | 做 |

---

## 2. 五大中心归位方案

### 2.1 逐中心回答

#### Intelligence Center（智能中枢）

- **天然属于它的模块**：`src/ai/liuhao.py`、`src/ai/providers.py`、`src/ai/model_router.py`、`src/ai/planner/`、`src/ai/reasoning/`、`src/ai/agent_factory.py`、`src/ai/conversation_store.py`、`src/ai/personal_context.py`；网关 `src/gateway/chat.py`、`src/gateway/models.py`；控制台 `Workbench`、`Overview`、`Directory`、`ModelManager`。
- **只做归位与命名能否成立**：**能**。它是一个真实的“对话 + 规划 + Provider + 人格”能力集合，改名不会掏空。
- **真独立建设缺什么/成本/值不值**：缺统一多模态、模型生命周期管理、评测与路由策略；成本大，且会与现有 Provider 管理重复。**不值得独立成服务**。

#### Knowledge Universe（知识宇宙）

- **天然属于它的模块**：`src/knowledge/{documents,embedding,chunker,vector_store,pii,security}.py`、`src/kernels/memory/`、`src/ai/conversation_store.py`、`src/ai/personal_context.py`、`src/gateway/files.py`、`src/api/routes/knowledge.py`。
- **只做归位与命名能否成立**：**不能**。当前控制台“知识中心”只是 OpenAPI 端点清单（`Directory.tsx:489`），不是入库/检索；`/v1/chat` 不检索知识；KG 只有测试类。叫“宇宙”会是空壳。
- **真独立建设缺什么/成本/值不值**：缺入库 UI、文件夹/文档解析器、embedding 配置入口、检索注入对话、记忆浏览器、KG；成本中等。**作为产品能力值得补，作为独立服务不值得**。

#### Execution OS（执行系统）

- **天然属于它的模块**：`src/ai/goal_task_graph.py`、`src/ai/agent_runtime.py`、`src/ai/tools_local.py`、`src/ai/lcore.py`、`src/ai/workspace.py`、`src/plugins/sandbox/`、`src/automation/`（默认三重关闭的自动化地基）、`src/kernels/{execution,resource}`、`src/gateway/{ai_management,projects,files,plugins}.py`、`src/distribution`（经 `kernels/execution/fence.py` 的执行围栏）。
- **只做归位与命名能否成立**：**基本能**。目标创建/执行/停止/重规划、员工雇佣/暂停、产物写入都已真实；但缺排程、用户自定义工作流、产物索引。
- **真独立建设缺什么/成本/值不值**：缺调度器、工作流模板/画布、重试与检查点可视化、资源配额 UI；成本中等。**作为 IA 值得，作为独立服务不值得**。

#### Security & Governance（安全与治理）

- **天然属于它的模块**：`src/security/`、`src/kernels/{identity,trust,policy,security,audit,retention}`、`src/gateway/{identity,trust,audit,policy,events,observability}.py`、`ApprovalCenter`、`src/ai/host_command/` 的授权与审批管线、`src/automation/` 的三重闸门与急停。
- **只做归位与命名能否成立**：**能**。这是当前最完整、最“真”的中心；但 `retention`/`compliance` 未接生产、外部 IdP 无。
- **真独立建设缺什么/成本/值不值**：缺保留策略执行、密钥轮换、外部 IdP、DLP、合规导出；成本小到中。**值得作为 IA 强化，不值得拆服务**。

#### Evolution Lab（进化实验室）

- **天然属于它的模块**：`src/kernels/evaluation/`（真，被 AgentRuntime 调用）、`src/evaluation/`、`src/feedback/`（内存态、API 未挂载）、`src/mlops/`（无调用方）、`src/ai/evolution.py`（测试引用）、`src/datasets/`。
- **只做归位与命名能否成立**：**不能**。除评估内核外，反馈不落盘、进化无生产调用，命名后就是假中心。
- **真独立建设缺什么/成本/值不值**：缺反馈持久化、评估数据集、实验注册、A/B、晋升门禁；成本大。**现阶段只做设计，不建中心**。

### 2.2 导航与命名建议（不改后端）

保持现有页面，只加“中心”分组与二级折叠，避免五套系统：

| 中心 | 归入现有页面 | 说明 |
|---|---|---|
| 智能中枢 Intelligence Center | 总览、工作台、我AI员工、业务中心（能力图谱） | “业务中心”当前是能力注册表，不是财务/业务账本，放这里最诚实 |
| 知识宇宙 Knowledge Universe | 知识中心、工作区文件 | 先把它做成“可入库/可检索”，再谈宇宙；记忆浏览待建 |
| 执行系统 Execution OS | 目标与任务、项目、应用、数据中心 | 数据中心是真实执行/资源指标，归执行侧 |
| 安全与治理 Security & Governance | 审批中心、审计链、信任、身份与权限、事件流、系统状态、生产预检 | 现有 GOVERNANCE_NAV 已是事实上的治理中心 |
| 进化实验室 Evolution Lab | 暂不设一级入口 | 只在目标详情展示评估结果；等反馈落盘后再升级 |

命名建议：中文主名 + 英文副名（如“智能中枢 · Intelligence Center”），不要只留英文；导航层级不超过两级。

### 2.3 是否真的拆成五套系统

**不应该。** 具体代价：

- 五套部署 = 五份启动脚本/端口/健康检查/日志，违背“双击即用、无需运维”；
- 五套库 = 五份迁移与备份，当前已经是至少 9 个 SQLite 文件（主库、审计、会话、记忆、画像、模型注册、人类身份、JWT 吊销、开源情报），再拆只会加剧数据割裂；
- 五套鉴权 = 五套 JWT/审批/审计，安全面反而扩大；
- 边界不成立：知识被对话/执行消费，安全拦执行，进化评估执行。

正确做法：**一个运行时 + 五个信息架构分区 + 明确的模块契约**。只有在未来转为“多用户服务器产品”时，才考虑把 Execution/Knowledge 拆成独立服务。

---

## 3. 路线图（三个时间层，每层 ≤ 5 项）

> 时间层不是日历工期，而是“先做 / 再做 / 只做设计”的优先级。每项都给出为什么是它、依赖什么。

### 第一层：先做（4 项）

1. **知识闭环：入库 UI + 检索 + 对话注入（方向 3）**
   - 为什么：后端 `documents.py`→chunk→embedding→SQLite 向量库已是真实链路，只缺产品面与消费端；这是“知识中心”从空壳变可用的唯一捷径。
   - 依赖：embedding provider 配置（当前仅 env：`LIUHAO_EMBEDDING_PROVIDER/MODEL`）；`/v1/knowledge/documents`、`/v1/knowledge/search`；`src/ai/liuhao.py` 的 system prompt 注入点。

2. **进程内排程器：定时/循环 Goal（方向 8/20）**
   - 为什么：Goal 执行链真实，但除审计链 checkpoint 外没有通用任务调度（全仓无 taskiq/apscheduler 生产调用）；没有它，“自动化工作流/个人助理”都只能手动点。
   - 依赖：`POST /v1/goals`、Goal 状态持久化（当前历史落 `.liuhao_goals.json`）、单进程生命周期；注意不要引入外部队列/Redis。

3. **备份/恢复/导出一键化（方向 28/23）**
   - 为什么：`scripts/ops/backup.py` 能力真实但无生产调用；单机个人数据产品必须有“可带走、可恢复”。
   - 依赖：各 SQLite 路径统一登记、启动时完整性检查、控制台设置页入口。

4. **产物索引 + 目标→文件回溯（方向 26/7）**
   - 为什么：`file_write` 与只读 `Files` 都真实，但用户无法从目标反查产物；这是执行可信度的最后一公里。
   - 依赖：`src/ai/workspace.py` 写入约定、`/v1/files`、Goal 执行记录。

### 第二层：再做（5 项）

1. **多模态输入（截图/图片 → vision Provider）（方向 5）**：为什么现在不做？因为 chat/agent 全链路是文本，新增需要 provider 视觉能力与存储；先做单向上传即可。
2. **受限多智能体（planner→executor→critic 三角色）（方向 2）**：不做委员会/黑板；只把 `employee.py`/`committee.py` 中可复用的评审逻辑收敛成 2–3 角色流水线。
3. **反馈持久化 + 评估闭环（方向 24）**：`feedback_service.py` 现在是内存 dict；先把反馈落 SQLite 并挂到目标详情，再谈进化。
4. **本地信息摘要（RSS/本地文档 → 定时摘要）（方向 9/25）**：不 Crawler、不做情报平台；复用排程器 + 知识入库。
5. **个人日程/提醒（方向 20）**：KAREN 画像已真实注入对话；补日程/提醒后才能算“个人数字生活”。

### 第三层：只做设计（5 项）

1. **云边/分布式契约（方向 13/21）**：设计“可选远端执行/同步”的接口与冲突边界，不实现常驻云。
2. **隐私与可信策略（方向 27）**：设计 PII/保留/脱敏在知识写入与审计中的强制执行点；不引入 MPC/TEE。
3. **企业外部连接器契约（方向 16）**：现有 `src/integrations/*_adapter.py` 是死代码；只设计连接器注册、凭据与权限模型，不逐个接 SaaS。
4. **自主研究循环（方向 4）**：设计“问题→检索→假设→实验→报告”的状态机与安全边界，不实现执行器。
5. **开源情报产品化（方向 25）**：保留现有 `src/intel` CLI（`python -m src.intel`）与 `oss_radar.py`；只设计“何时值得进控制台”的触发条件。

---

## 4. 明确的“不做清单”及理由

### 4.1 物理世界与硬件依赖（不做）

- **数字孪生与仿真（10）**：需要真实设备模型/传感器数据，本产品无采集面。
- **物联网（11）**：需要 MQTT/Modbus/OPC-UA 与现场设备，且要 7×24 运维。
- **机器人与具身智能（12）**：安全关键作动，需硬件、实时性与认证。
- **空间智能（14）**：需要 GIS/点云/3D 数据与 GPU，个人单机无输入源。
- **工业自动化（15）**：需要 PLC/SCADA 与产线，法律与安全责任不可承担。

### 4.2 高风险/强监管（不做）

- **电脑自主操作（6）**：开放 OS 级控制会绕过执行围栏与人类主权闸门；当前只有授权管线与 fake 后端，不应产品化。
- **金融交易/投资建议（17 的交易层）**：需行情、券商接口、合规资质；只保留 token 成本账本。
- **医疗/法律建议（18 的行业模板）**：`solutions/industry/{healthcare,legal}` 仅测试模板，不能露出为产品能力。
- **外部数字身份/KYC（22 的外部层）**：本地 JWT/RBAC 足够；证件核验与外部信任链不做。
- **全网情报采集（9 的平台层）**：ToS、反爬、隐私与常驻运维冲突。

### 4.3 对个人用户无日常价值（不做）

- **多智能体委员会/黑板组织仿真（2 的组织层）**：单人使用没有“组织政治”场景；只保留 2–3 角色审查。
- **全球信息情报门户（9）**：个人用户需要的是“今天值得看的 5 条”，不是情报终端。
- **独立教育产品（18）**：无学生数据、无课程体系；做出来是模板 Demo。
- **创意内容套件（19）**：本地只有文件处理插件，生成需 GPU/云；与成熟工具竞争无胜算。
- **重型数字资产管理（26）**：只做产物索引，不做版本库/版权/素材市场。
- **开源情报控制台（25）**：开发者低频工具，保留离线脚本即可。
- **原生桌面/移动 App（29 的原生层）**：PWA + SDK 已覆盖；原生是长期维护负担。

### 4.4 与产品形态直接冲突（不做）

- **HA 集群/多节点复制（28 的集群层）**：需要多机、外部协调与运维；单机只做备份/恢复。
- **数据仓库/流式数据平台（23 的平台层）**：需要外部 DB/集群；单机 SQLite 足够。
- **默认 Docker/云依赖（13 的默认形态）**：`start-liuhao.bat` 的单进程单端口是产品承诺；云部署只能作为可选出口。

---

## 5. 未验证项

1. **未运行任何服务/测试/Docker**：本报告所有“已接线”判断来自静态代码阅读与引用检索，不是运行时 E2E 结论。
2. **Provider / Embedding 实际可用性未验证**：`src/ai/providers.py`、`src/knowledge/embedding.py` 需要真实密钥或本地 Ollama；未确认当前环境是否配置。知识端点可能如实返回 503“未启用”。
3. **目标执行链未实跑**：`AgentRuntime.run_goal` → planner → tools → memory 的真实结果未在本轮验证。
4. **审批中心 UI 未实点**：`ApprovalCenter` 源码存在（`App.tsx:535`），但未在浏览器验证签发/撤销。
5. **顶层 61 个插件是否被任何外部入口动态加载未穷尽验证**：已确认 `src/plugins/manager.py` 默认扫描 `src/plugins`，且 `main.py` 只注册 builtin；不排除仓库外脚本加载 `plugins/`。
6. **38 个 `solutions/industry` 模板是否被外部入口使用未穷尽验证**：静态检索仅见 `tests/test_all_industry_solutions.py`。
7. **`apps/console/console/dist` 是否与源码同步未验证**：启动器直接服务 dist；若未重新 build，界面可能落后于源码。
8. **`src/distribution` 的 file backend 在多进程/多机下的行为未验证**：只确认 `kernels/execution/fence.py` 引用。
9. **审计证据链的外部锚定/防篡改强度未验证**：`audit_store.db` 真实存在且为 32MB，但 HC-01 外部锚定未在本轮核验。
10. **`oss-ecosystem/state` 快照时间为 2026-09-13，相对当前 2026-11-06 已过期**；其结论不能当作当前开源生态事实。
11. **SDK 10 种语言的构建/发布状态未验证**：只确认源码与测试存在，未执行各语言构建。
12. **`src/automation` / `src/intel` / `src/desktop` 的运行时状态未验证**：`src/automation` 有真实接线点（`src/ai/tools/kernel_tools.py:369` ← `src/ai/liuhao.py:202`），但三闸默认关闭、只有 fake 后端，因此默认可用工具数为 0（未实跑确认）；`src/intel` 与 `src/desktop` 是 CLI（`python -m src.intel` / `python -m src.desktop`），未确认是否被 `start-liuhao.bat` 或托盘入口实际调用。静态检索未发现非测试导入。
13. **“无任何实现”的判断基于关键词与目录检索**：不排除以其他命名隐藏的实现；已尽量覆盖 `src/` 与仓库根，但未逐一人工通读 433 个 `src/*.py`。

---

## 6. 判定标准：怎么区分“真接线”和“只是文件存在”

> 这一节比结论更有用。以后新增任何能力、复核任何方向，先过这四关，再写进矩阵。

### 6.1 四关（按顺序，任何一关不过都不能记成“真有”）

| 关 | 问题 | 一条命令 | 判据 |
|---|---|---|---|
| 1 存在 | 文件/目录在吗 | `Test-Path <路径>` / `Get-ChildItem <目录> -Recurse -Name` | 在 ≠ 可用 |
| 2 可达 | 谁在生产代码里调用它 | `git grep -n "<顶层符号>" -- src ":!<模块自身目录>"` | 0 命中 ⇒ 未接线；只剩 CLI/测试 ⇒ 不算生产 |
| 3 门槛 | 调用点会不会返回空/None | 读调用点 + 闸门默认值 | 默认返回空 ⇒ 用户根本看不到这项能力 |
| 4 后端 | 最后一步是真执行还是 fake | `Get-ChildItem <模块>/backends`；`git grep -n "fake\|mock\|not implemented" -- <模块>` | 只有 fake/桩 ⇒ 假实现 |

**第 2 关的关键陷阱（本次修正就是因为踩了它）**：本仓库大量使用**相对导入**
（`from ...automation.tools import build_automation_tools`），grep 目录字符串
`src/automation` 会**漏掉**这类调用。必须 grep **顶层符号**（类名/函数名），
不要 grep 目录路径。

### 6.2 四个具体案例（命令与输出都复核过）

**① 61 个 plugins 目录**

```powershell
(Get-ChildItem plugins -Directory).Count          # 61
(Get-ChildItem src/plugins -Directory).Count      # 5
git grep -n "get_plugin_manager\|PluginManager(" -- src ":!src/plugins"
```

结果只剩 `src/cli/__init__.py:221`（插件管理 CLI）与 `src/plugins/manager.py`
自身的单例；`src/plugins/manager.py:48` 的默认目录是 `src/plugins`（5 个），
不是顶层 `plugins`（61 个）。**判定：文件名存在，运行时网关不加载。**

**② 38 个 `solutions/industry` 模板**

```powershell
git grep -n "solutions.industry" -- src        # 0 命中
git grep -n "solutions" -- tests               # 命中 test_all_industry_solutions.py
```

**判定：只被测试导入，生产代码零引用。**

**③ `src/automation` 的 fake 后端（这条比“没接线”更细）**

```powershell
git grep -n "build_automation_tools" -- src ":!src/automation"
# src/ai/tools/kernel_tools.py:369 / 371
Get-ChildItem src/automation/backends -Name      # 只有 fake.py
```

再读 `src/automation/gate.py:15-20` 与 `tools.py:59-65`：三闸
（`LIUHAO_AUTOMATION_ENABLED` / `LIUHAO_AUTOMATION_BACKEND` / 主体类型）默认全关，
`build_automation_tools` 直接返回 `[]`。
**判定：接线点真实（`src/ai/liuhao.py:202` 的 `make_tools` → `kernel_tools.py:369`），
但默认可用工具数为 0，执行后端是假的。** 这属于“接了线但不可用”，
不能写成“没有调用方”——两种说法的修复方向完全不同。

**④ 多模态 / 创意生产**

```powershell
git grep -n "not implemented|占位" -- src/ai/perception.py
git grep -n "IMAGE_GENERATION" -- src
```

图像/音频/视频是显式占位，生成能力只有枚举、没有执行路径。
**判定：能力声明存在，执行路径不存在。**

### 6.3 一条可复用的“能力体检”命令

```powershell
# 用法：$m = 模块目录；$sym = 该模块对外暴露的顶层符号
$m = "src/automation"; $sym = "build_automation_tools"
git grep -n $sym -- src ":!$m" ;                              # 关 2：调用方
Get-ChildItem "$m/backends" -Name -ErrorAction SilentlyContinue ;  # 关 4：后端
git grep -n "fake|mock|not implemented|占位" -- $m            # 关 4：桩标记
git grep -n "return \[\]|return None" -- $m                   # 关 3：默认空返回
```

判读：关 2 为空 ⇒ 未接线；关 3/4 命中 ⇒ 假实现或默认不可用；
四项都有真实输出才可能是“真有”。

### 6.4 写进矩阵时可操作的完成度定义

- **真有**：四关全过，且有非测试调用方与真实后端（不是 fake、不是桩、不是枚举）。
- **半成品**：有真实实现，但缺调用方，或缺产品面（UI/API/排程）。
- **假实现**：调用点返回空、只有 fake 后端、只有占位/枚举，或只被测试导入。
- **无**：第 1 关就不过。

> 最容易误判的是**第 3 关**。“接线了但默认返回空”要记成假实现/半成品，
> 不能记成真有；`automation` 就是这一类。另外，**测试通过不等于接线**——
> 仓库里有相当多“测试全绿但生产零调用”的模块（`committee`、`blackboard`、
> 顶层 `plugins`、`solutions/industry`）。判接线只能看 `src` 的调用方，
> 不能看 `tests` 的通过率。
