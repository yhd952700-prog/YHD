# PM PRD v3.0 (Draft) — LIUHAO X Phase 1 调研稿

> **⚠️ DEPRECATED** — 本草稿已被 `product/PM-PRD-v3.0.md` 正式版取代。请参阅正式版。
>
> **状态**: Phase 1 调研稿（D-P1-3=A 要求三文档调研 + 用户确认）
> **Owner**: Workstream C (PM)
> **依据**: Definition Lock §1-§10 + Capability Traceability Matrix

---

## §1 问题陈述 (Problem Statement)

### 1.1 现状痛点

| 痛点 | 现有方案 | 用户体验 |
|------|----------|----------|
| 智能体行为不可审计 | LLM 黑盒输出 | 金融/医疗/法务行业无法接受 |
| 多模型切换成本高 | 每家公司自己 wrapper | 重复造轮子 |
| 长期记忆断裂 | 每次会话从零开始 | 用户重复告知上下文 |
| 工作流无状态恢复 | 出错就重头 | 大工作流成本高 |
| 权限粒度不够 | 角色二选一 | 复杂企业场景不够 |

### 1.2 LIUHAO X 解决方案

**Human-Sovereign Agent Operating System** — 一个以人类主权为最高原则的智能体操作系统：

- **10 DNA 智能**: ULTRON/VISION/ADA/EDITH/FRIDAY/JARVIS/JOCaSTA/KAREN/ENOCH/ZOON 协同
- **Bounded Autonomous Authority**: 能力 ≠ 权限；所有权限可追溯
- **Long-horizon Memory**: 10 层内存架构 + Mem0 长期记忆
- **Stateful Workflow**: LangGraph + checkpoint 状态恢复
- **Defense in Depth**: RBAC + ABAC + Vault + 不可篡改审计

---

## §2 用户旅程 (User Journeys)

### 2.1 主要用户角色

| 角色 | 占比 | 核心诉求 | 典型场景 |
|------|------|----------|----------|
| **AI Engineer** | 40% | 快速构建/部署 agent | 编写 workflow、调模型、接插件 |
| **Enterprise Admin** | 25% | 权限/审计/合规 | RBAC 配置、审计追溯、合规报告 |
| **Business User** | 20% | 自然语言交互 | 通过 Console 下达业务指令 |
| **Operator/SRE** | 10% | 监控/灾备 | 全链路追踪、扩缩容、灾备演练 |
| **Auditor** | 5% | 只读核查 | 审计 trail、不可篡改验证 |

### 2.2 关键用户旅程

#### Journey A: AI Engineer 部署 Workflow

```
1. 登录 Console
2. 进入 Workflows 页面
3. 拖拽节点（LLM/Memory/Tool/Human）
4. 配置权限（KAREN）
5. 设置 SLA / 限流（ZOON）
6. 测试运行（带 audit trail）
7. 灰度发布
8. 监控 + 自动扩缩容
```

#### Journey B: Business User 通过 AI 完成任务

```
1. 自然语言请求："帮我分析 Q3 财报"
2. AIEmployee 接收 → 拆解 Goal/Task
3. 长期记忆加载（Mem0）
4. 调用合适 Provider（ADA）
5. 执行 + 状态保存（FRIDAY）
6. 人类检查点（如有）（JOCaSTA 审批）
7. 返回结果 + 审计 ID（ENOCH）
```

---

## §3 优先级 MoSCoW

### Must Have (M1-M3)

| 功能 | 阶段 | Kernels |
|------|------|---------|
| Console 入口 + Dashboard | M3 (P15) | K01 |
| FastAPI 主链 | M1 (P3) | K01 |
| LLM Provider（4 个） | M1 (P3) | K03 |
| Plugin Sandbox（4 后端） | M2 (P7) | K07 |
| RBAC | M2 (P8) | K08 |
| Audit | M2 (P10) | K09 |
| Mem0 集成 | M2 (P4) | K04 |
| LangGraph 工作流 | M2 (P5) | K05 |
| Goal/Task Graph | M2 (P6) | K06 |

### Should Have (M3)

| 功能 | 阶段 | Kernels |
|------|------|---------|
| ABAC 策略 | M2 (P9) | K08 |
| Provider Mesh | M2 (P11) | K02 |
| RAG Pipeline | M2 (P12) | K04 |
| Long-horizon Planning | M2 (P13) | K06 |
| 5 Console 页面 | M3 (P16) | K01 |
| Observability Mesh | M3 (P19) | K10 |

### Could Have (M4+)

| 功能 | 阶段 |
|------|------|
| Disaster Recovery 异地演练 | M3 (P17) |
| Auto-Scaling | M3 (P18) |
| Plugin Marketplace | M3 (P20) |
| Distribution 灰度 | M3 (P21) |

### Won't Have (v3.0 内)

- 多语言 Console UI（v4.0 考虑）
- 移动 App（v4.0 考虑）
- 联邦学习（v4.0+ 长期）
- 跨域联邦部署（v4.0+）

---

## §4 非目标 (Non-Goals)

| 不做 | 原因 |
|------|------|
| 替换 OpenAI/Anthropic | LIUHAO 是 OS 不是 LLM |
| 自研 GPU 调度 | 已有 K8s + Ray 生态 |
| 全自动无人值守 | 违反 Human Sovereignty |
| 黑盒模型可解释性 | 超出 v3.0 范围 |
| 移动端原生应用 | v4.0 路线 |
| 区块链存证 | 不可篡改审计已满足 |

---

## §5 度量 (Metrics / KPIs)

| 类别 | KPI | 目标 |
|------|-----|------|
| **可用性** | 系统 uptime | ≥ 99.95% |
| **延迟** | P95 响应 | < 2s（不含 LLM 推理） |
| **吞吐** | 并发 workflow | ≥ 100 |
| **记忆** | 召回率 (RAG) | ≥ 85% |
| **审计** | 完整 trail | 100% 操作可追溯 |
| **权限** | 误授权率 | < 0.01% |
| **灾备** | RPO/RTO | < 5min / < 30min |
| **人类主权** | 强制检查点 | 100% 高风险操作 |
| **L10K** | 1 万用户场景 | 通过 |

---

## §6 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| Mem0 实际启用遇阻 | 中 | 保留 mock 路径；Phase 4 内 1 周内验证 |
| Console 工作量大 | 高 | 5 页面 MoSCoW；Must 优先 |
| L10K 性能不达标 | 中 | M3 末预演；M4 调优 |
| 跨 Workstream 冲突 | 中 | R2 文件锁 + CI |
| 决策悬挂 | 低 | OD Register 强制 |

---

## §7 验收 (Definition of Done for v3.0)

- [ ] 22 Phase 全部完成（commit 链路完整）
- [ ] 12 Kernels × 10 DNA 覆盖 ≥ 80%
- [ ] 31 张表 + PG 主从端到端通过
- [ ] Console 5 页面 + 设计 token
- [ ] L10K 基准通过
- [ ] 全部 OD 关闭
- [ ] 三文档定稿 + 项目总监签字

---

**本文档为 Phase 1 调研稿；用户确认后作为正式 v3.0 PRD。**

