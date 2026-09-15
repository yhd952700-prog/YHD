# 鎏灏全球开源生态吸收计划

> 建立日期：2026-09-13
> 所有者：Vigil（Principal Engineer）
> 状态：**第一阶段已完成并验收**

---

## 一、这份计划要做什么

让鎏灏持续从全球开源生态吸收**能力**，而不是复制代码。

一句话判据：**先看缺口，再找项目。** 反过来做就是无目的搬运。

所以整套体系的第一件事不是写爬虫，而是先把"鎏灏缺什么"写成可核对的账单
（`genebank/GENE-MAP.yaml`）。某个开源项目只有能对上账单里的一条具体缺口，
才算候选；对不上就是噪声，直接 reject。

### 明确不做的事

- **不批量 clone 代码**。吸收的是设计范式、算法、接口形态，然后在本项目里重写。
- **不引入新的并行权威**。工作流引擎、策略判定这类已有裁定主体的地方，外来项目只作对照物。
- **不为了覆盖数好看而扫描**。没有 API 的源就写 `null`，并注明它是被动源 —— 不猜 URL。

---

## 二、三层产物

| 层 | 位置 | 作用 | 状态 |
|---|---|---|---|
| 能力账单 | `genebank/GENE-MAP.yaml` | 12 类基因，每类写明"今天谁承担、缺什么" | ✅ 已建 |
| 覆盖蓝图 | `intents.yaml` | 用户列的 72 类 → 可执行查询 → 目标内核 | ✅ 已建 |
| 发现引擎 | `pipeline/oss_radar.py` | 真调 API，诚实报告失败 | ✅ 已建并跑通 |
| 能力数据库 | `capabilities/*.yaml` | 每个候选的 13 字段分析 | 🟡 **51 条 / 8 个文件**（20 深度分析 + 5 落地复记 + 12 tier1 分级 + 14 tier2 分级；扫描池去重 657；tier2 由 Oss-Thicken 于 2026-09-15 新增） |
| 字段契约 | `schema/capability-entry.schema.yaml` + `pipeline/validate_entries.py` | 让"必须分析"变成可校验的事 | ✅ 已建（含**可执行校验器**，2026-09-15 补齐） |
| 契约门禁 | `tests/test_oss_ecosystem_entries.py` | 契约在 CI 里**必跑**，并注入违规反证"真的会红" | ✅ 已接进 `pytest tests/`（CI 真门禁） |
| 扫描留痕 | `state/scan-*.json` | 每轮真实数据，可复盘 | ✅ 已生成 |

---

## 三、信号源真实状态（2026-09-13 实测，非文档抄写）

探测是逐条真发请求得到的，结果如下：

| 状态 | 源 | 说明 |
|---|---|---|
| ✅ 可用 | github, gitlab, npm, mcp_registry, spdx | 直接可跑 |
| ⚠️ 复验转为不可达 | linux_foundation | **2026-09-14 复验**：由 ✅ 变为 `RemoteProtocolError: Server disconnected without sending a response`。扫描时按失败如实记录（不假装扫过、不重试到"成功"），待上游/网络恢复后复验 |
| 🔒 需鉴权 | modelscope, kaggle | 未配凭据时**显式报 skipped**，不假装扫过 |
| ❌ 本机代理不可达 | huggingface, dockerhub | 走代理返回 502。这是**环境限制，不是源失效**，换网络环境需复验 |
| ○ 被动源 | bitbucket, sourceforge, apache, pypi, kaggle 等 | 官方无发现端点，仅在别的源提到时单点核查 |

> **2026-09-14 复验**（`--probe` 真发请求）：github / gitlab / npm / mcp_registry / spdx 仍 OK；
> **本轮唯一变化是 `linux_foundation` 由可用转为不可达**（上游断开，非本仓代码问题）；
> huggingface / dockerhub 仍被本机代理 502 挡住。没有任何一处被"修成成功"。

**本轮修正了两处错误记录**（前人手稿里有假数据）：

1. **Bitbucket** 全局仓库列表端点已官方废弃，返回 `410 Gone`（CHANGE-2770）。
   原记录写"实测 200"，已改为 `discover: null`，降级为被动源。
2. **MCP Registry** 的 `limit` 参数硬上限是 **100**，传 500 返回 422。
   拉全量必须走 `metadata.nextCursor` 分页，已在代码里实现。

> 这两条正好是本项目最忌讳的事：**谎报成功**。发现即修。

---

## 四、72 类覆盖统计

```
总 72 类    P0=14   P1=34   P2=24
性质        platform=14   topic=45   horizon=13
```

13 个 `horizon` 类（Papers with Code / Kaggle / Maven / Gradle / Java / Angular /
嵌入式 / IoT / 边缘 / 游戏引擎 / 3D / 图形算法 / WebGPU）明确标记**本轮不扫描**，
并在 `--plan` 输出里逐条列出理由。

**不扫描也必须出现在报告里** —— 静默遗漏等于骗人。

---

## 五、扫描结果

| 轮次 | 范围 | 候选 / 去重项目 | 耗时 |
|---|---|---|---|
| 第一轮 | P0 14 类 | 142 / 142 | 159 秒 |
| 全量轮 | P0+P1 42 类 | **705 / 657** | 603 秒 |
| P2 补扫 | P2 24 类（19 类为 horizon，标"本轮不扫描"） | 24 / 24 | 40 秒 |

**72 类已全部走过一遍**（扫过的 59 类 + 明确标注不扫的 13 类），没有静默遗漏。

失败源全部显式记录：Hugging Face 三次 502、Docker Hub 一次 502、
PyPI 十次无发现端点、CNCF 一次连接中断（已通过本地缓存修掉）。

stars / license / language 全部经 `api.github.com/repos/<repo>` **二次核实对齐**。

`capabilities/` 共 **51 条记录 / 8 个文件**（2026-09-15 重数）。四种体例**不可混算**：

| 文件 | 条数 | 体例 |
|---|---|---|
| `automation.yaml` / `safety.yaml` / `coding.yaml` / `agent.yaml` | 5 / 4 / 4 / 2 | **深度分析**（13 字段全填） |
| `tier2-depth.yaml` | 5 | **tier2 深度分析**（Oss-Thicken 新增，13 字段全填，各对上 GENE-MAP 一条具体缺口） |
| `s2-s5-landed.yaml` | 5 | **落地复记**（记录第 2–5 轮的实际吸收结果，非新候选） |
| `triage-tier1.yaml` | 12 | **tier1 分级**（轻量条目，按契约补齐必填字段） |
| `triage-tier2.yaml` | 14 | **tier2 分级**（Oss-Thicken 新增，轻量条目，id 0201–0214，避免与 tier2-depth 的 5 个 repo 重复登记） |

其中**深度分析 20 条**（原 15 + `tier2-depth.yaml` 新增 5）的结论分布：

| 结论 | 数量 | 代表 |
|---|---|---|
| **adopt** | 3 | Semgrep（把架构纪律变成 CI 会红的检查）、Bandit（工具已在用，缺的是必跑）、tesseract（OCR 引擎补多模态输入） |
| **evaluate** | 9 | Temporal、Conductor、Casbin、Monty、Semantic Kernel、vllm（本地模型 serving）、pgvector（库内向量检索）、authelia（自托管认证网关）、robotframework（真实世界动作面） |
| **watch** | 6 | Hatchet、OPA、Microsandbox、Dagster、deer-flow、MCP Server 生态 |
| **reject** | 2 | Airflow（重复自有能力）、Composio（凭证托管触碰主权红线） |

其余两类**不计入上面的结论分布**，因为它们都还没走完深度分析：

- `s2-s5-landed.yaml` 的 5 条是**已发生的事实**，不是新结论：Semgrep 已落地、
  Temporal 部分吸收（范式下沉，未引入集群）、`pydantic/monty` 本轮暂缓、
  既有代码 4 处"谎报成功"已修复、RestrictedPython 已落地。
- `triage-tier1.yaml` 的 12 条是**待复核清单**（adopt 6 / evaluate 6），
  条目已过白名单 + stars/license/last_push 二次核实，但**尚未做 13 字段深度分析**。
- `triage-tier2.yaml` 的 14 条是 Oss-Thicken 本轮新增的**待复核清单**（adopt 0 / evaluate 8 / watch 6 / reject 0），
  同样过白名单 + 真实 API 二次核实，尚未做 13 字段深度分析。

**关于 `NOASSERTION`（本轮最容易被误读的一项，单列说明）**：本轮共 5 条的 `license` 被
GitHub API 返回 `NOASSERTION` —— 本文件 4 条（rq、vercel/ai、Tencent/ncnn、NVIDIA/TensorRT-LLM）
+ `tier2-depth.yaml` 1 条（pgvector）。**已逐份实读 LICENSE 文件本体**，结论是
**「许可并非不明，而是 GitHub 检测器匹配不到」**：

| repo | 实读到的许可 | 检测器为何报 NOASSERTION | commercial_risk |
|---|---|---|---|
| `rq/rq` | **BSD-2-Clause**（两条条件，无第三条款） | 文末多一段作者观点免责声明 | low |
| `vercel/ai` | **Apache-2.0** | 只贴了 Apache **短式**声明（非全文） | low |
| `Tencent/ncnn` | **BSD-3-Clause** | 同一 LICENSE.txt 内**捆绑**第三方组件通知清单 | medium（见下） |
| `NVIDIA/TensorRT-LLM` | **Apache-2.0** | 同上，含源自其他项目的衍生代码 | medium（见下） |
| `pgvector/pgvector` | **PostgreSQL License** | 文件以 "Portions Copyright" 起头 | low |

其中 3 条按实读结论记 `commercial_risk=low`。ncnn 与 TensorRT-LLM 保留 `medium`，
**原因不是"许可未知"，而是捆绑/衍生代码另适用其他许可**（ncnn 的 LICENSE.txt 明列 zlib 等；
TensorRT-LLM 的 LICENSE 写明衍生部分 "may have different licenses"）——
正式采纳前需逐项确认这些组成部分非 copyleft。这是各自真实的残留风险。

⚠️ **订正**：worker 原先的记录里「vercel/ai 疑似 MIT」「TensorRT-LLM 疑似 BSD 系」经实读**两条均不成立**
（分别为 Apache-2.0 / Apache-2.0）。凡"疑似许可"一律不得作为结论，须实读文件。

> **已知局限（不掩盖）**：CNCF 全景源只按名称与描述做本地子串匹配，没有相关性排序，
> 因此候选池里存在噪声（例如搜 "apache arrow" 会命中描述中提到 Arrow 的 InfluxDB）。
> 这个源产出的是**待筛选池**而非结论；进入 `capabilities/` 之前一律人工/LLM 复核。

### 三条最值得注意的结论

**1. Temporal —— 要的是范式，不是集群。**
引入 Temporal 意味着要运维一整套集群加数据库，对鎏灏当前的**单端口自服务**部署形态过重。
真正该拿的是"确定性约束 + 事件历史作为唯一真源"这套设计，
重构成一层轻量 `ExecutionJournal`。验收标准很硬：**kill 掉进程再重启，任务从断点继续，而不是重头跑。**

**2. Semgrep —— LGPL-2.1 是可以接受的，前提是别 fork。**
以独立进程在 CI 调用，不进产品分发链路，因此 medium 商业风险可控。
收益极大：把"内核不得 import src.ai""新增 API 前缀必须进 RESERVED_PREFIXES"这类**写在文档里的纪律**，
变成**CI 里会红的规则**。这正是前面多轮修复（网络谎报投递、幻影依赖、CI 谎报成功）真正缺的那道防线。
⚠️ 红线：不得 fork 后内置进产品，否则触发 LGPL 开源义务。

**3. Composio —— MIT 许可没问题，但模式会被拒。**
它要求把第三方凭证托管到外部平台，违反 `MS:§143` 人类主权最高原则。
**这一条是整个体系存在的意义**：许可证过审 ≠ 可以吸收。

---

## 六、四条红线（License 只是其中一条）

任意一条命中即 reject：

| 红线 | 含义 |
|---|---|
| **R1 许可阻断** | AGPL / GPL-3.0 / SSPL / Elastic-2.0 / BUSL / 非 OSI，且需链接或同进程分发 |
| **R2 带 CVE 且无人维护** | 一年以上无提交 + 存在未修复的可远程利用 CVE |
| **R3 重复自有能力** | 鎏灏已有同等实现且无增量（例：工作流引擎已裁定自研 `goal_task_graph.py`） |
| **R4 主权冲突** | 要求把决策权、人类审批或凭证托管给外部服务 |

---

## 七、下一阶段路线图

| 阶段 | 目标 | 判据 | 状态 |
|---|---|---|---|
| S1 | 体系搭建 + 首批分析 | 扫描器可跑、失败诚实、数据二次核实 | ✅ 完成 |
| S2 | 全部 72 类走一遍 | 不扫的也要列出理由 | ✅ 完成（第 3 轮补完） |
| S3 | Semgrep 规则集 + 质量门禁 | 注入违规后 CI 会红 | ✅ 完成（实测退出码 2，3 条命中） |
| S4 | ExecutionJournal，崩溃续跑 | kill 后重启能从断点继续 | ✅ 完成（子进程 SIGTERM 后换进程续跑） |
| S5 | 第二执行面接入 | 危险调用被拒且报错可解释 | ✅ 完成 —— **但 Monty 判定为"现在接不上"**，见下 |

### S5 的诚实结论

Monty 本身没接进来，原因是**上游还不能用**，不是我们不想：

1. PyPI 上 `monty` 是**另一个项目**（`materialyzeai/monty`），真名是 `pydantic-monty`
2. `pydantic-monty` 0.0.23 实测 `Monty("1+2")` 直接抛 TypeError —— 绑定还没成型

但这一轮的**真正收获**是另一件事：`select_backend()` 旧逻辑在"显式指定的受限后端
不可用"时会**静默降级**到更弱的后端。这意味着要求沙箱隔离的 AI 生成代码，
会以宿主完整权限在 subprocess 里跑完并返回 `success=True`。
**这个洞比"没装 monty"严重得多**，已经修掉（`required=True` 时宁可抛错也不换后端）。

详见 `capabilities/s2-s5-landed.yaml`。

---

## 八、怎么用

```bash
python oss-ecosystem/pipeline/oss_radar.py --probe    # 源健康检查
python oss-ecosystem/pipeline/oss_radar.py --plan     # 72 类覆盖统计
python oss-ecosystem/pipeline/oss_radar.py --scan --priority P0 --per-intent 8
```

环境注意：本机出网依赖环境变量中的 `HTTPS_PROXY`（当前指向本机回环地址，端口每次会话会变，
用 `env | grep -i proxy` 查看）。GitHub 匿名限流 10 次/分钟，`--sleep` 默认 7 秒，
不要调小，否则会被限流产生 0 结果 —— 那不是源不可用，是自己触发了限流。
