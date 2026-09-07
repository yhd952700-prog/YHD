# UIUX v3.0 (Draft) — LIUHAO X Phase 1 调研稿

> **⚠️ DEPRECATED** — 本草稿已被 `product/Designer-UIUX-v3.0.md` 正式版取代。请参阅正式版。
>
> **状态**: Phase 1 调研稿（D-P1-3=A 要求三文档调研 + 用户确认）
> **Owner**: Workstream C (Designer)
> **依据**: Phase 1 PRD + Architecture + capability-traceability-matrix

---

## §1 信息架构 (Information Architecture)

```
/                                → Dashboard (overview + KPIs)
/workflows                       → Workflows list + Editor
/workflows/[id]                  → Workflow detail / Run history
/plugins                         → Plugin marketplace
/plugins/[id]                    → Plugin detail / Sandbox config
/memory                          → Memory explorer (Mem0 view)
/memory/search                   → Search across all memory
/agents                          → Agent mesh (Phase 14)
/agents/[id]                     → Agent config + audit trail
/settings/roles                  → RBAC (KAREN)
/settings/policies               → ABAC policies
/settings/audit                  → Audit log (ENOCH)
/settings/vault                  → Vault Transit keys
/settings/observability          → ZOON metrics + traces
/admin/disaster                  → DR status (Phase 17)
/admin/scaling                   → HPA config (Phase 18)
```

---

## §2 关键页面线框 (Key Page Wireframes)

### 2.1 Dashboard（Must, P15）

```
┌─────────────────────────────────────────────────────────┐
│ LIUHAO X  v3.0                       [👤] [⚙] [🚪]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐     │
│  │ Active  │ │ Today's │ │ P95     │ │ Audit   │     │
│  │ Agents  │ │ Tasks   │ │ Latency │ │ Events  │     │
│  │   12    │ │  1,234  │ │  180ms  │ │ 8,901   │     │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘     │
│                                                         │
│  ┌──────────────────────────┐ ┌─────────────────────┐ │
│  │  Workflow Activity (24h) │ │  Memory Stats       │ │
│  │  [Time-series chart]     │ │  Items: 12,345      │ │
│  │                          │ │  Recall: 87%        │ │
│  └──────────────────────────┘ └─────────────────────┘ │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Recent Audit Events (latest 20)                │  │
│  │  [user] deployed workflow X                      │  │
│  │  [system] auto-scaled to 5 replicas             │  │
│  │  ...                                             │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Workflow Editor（Must, P16）

```
┌─────────────────────────────────────────────────────────┐
│ Workflow: "Q3 Report Analysis"         [▶ Run] [💾] [⏸] │
├──────────────────┬──────────────────────────────────────┤
│  Node Palette    │  Canvas (drag-drop)                  │
│                  │                                      │
│  📥 Trigger      │    ┌──────┐    ┌──────┐    ┌──────┐ │
│  🧠 LLM          │    │Start │───►│ LLM  │───►│ Tool │ │
│  💾 Memory       │    └──────┘    └──────┘    └──────┘ │
│  🔧 Tool         │                     │                │
│  👤 Human        │                     ▼                │
│  📤 Output       │               ┌──────────┐          │
│                  │               │   Wait   │          │
│                  │               └──────────┘          │
├──────────────────┴──────────────────────────────────────┤
│  Properties Panel (when node selected)                  │
│  Node: LLM                                              │
│  Model: gpt-4 | Temp: 0.3 | Scopes: [read, execute]     │
└─────────────────────────────────────────────────────────┘
```

### 2.3 Memory Explorer（Must, P16）

```
┌─────────────────────────────────────────────────────────┐
│ Memory Explorer                  [🔍 Search...]  [⏰]    │
├─────────────────────────────────────────────────────────┤
│  Layers: [L1 Working] [L2 Short] [L3 Episodic] ...     │
│  Filter: user=alice | tier=semantic | after=2026-09-01 │
├─────────────────────────────────────────────────────────┤
│  Result: 234 memories                                   │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐│
│  │ "Q3 finance report showed 23% revenue growth..."   ││
│  │ Layer: L4 | Created: 2026-09-04 | Used: 12x        ││
│  │ [Edit] [Delete] [Promote to L5]                    ││
│  └─────────────────────────────────────────────────────┘│
│  ... (more)                                             │
└─────────────────────────────────────────────────────────┘
```

### 2.4 Audit Log（Must, P16）

```
┌─────────────────────────────────────────────────────────┐
│ Audit Log                       [🔍 Filter]  [📥 Export]│
├─────────────────────────────────────────────────────────┤
│  Hash chain: ✅ Verified (last 8,901 events)           │
│                                                         │
│  Time      │ Actor  │ Action          │ Target  │ Hash  │
│  ──────────┼────────┼─────────────────┼─────────┼──────│
│  08:30:12  │ alice  │ deploy workflow │ wf-123  │ a3f.. │
│  08:25:01  │ bob    │ update RBAC     │ role-x  │ 7d2.. │
│  ...                                                  │
└─────────────────────────────────────────────────────────┘
```

### 2.5 Plugin Marketplace（Could, P20）

```
┌─────────────────────────────────────────────────────────┐
│ Plugin Marketplace                  [🔍] [+ New Plugin]  │
├─────────────────────────────────────────────────────────┤
│  Installed (3)        Available (47)        Updates (2) │
│                                                         │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐           │
│  │ plugin │ │ plugin │ │ plugin │ │ plugin │           │
│  │  A     │ │  B     │ │  C     │ │  D     │           │
│  │ 1.2.3  │ │ 0.9.1  │ │ 2.0.0  │ │ 1.5.7  │           │
│  │ [⚙][⏸]│ │ [⚙][⏸]│ │ [⚙][⏸]│ │ [⚙][⏸]│           │
│  └────────┘ └────────┘ └────────┘ └────────┘           │
└─────────────────────────────────────────────────────────┘
```

---

## §3 设计 Token (Design Tokens)

```yaml
# Color
color-bg-primary:    #0f172a  # 主背景
color-bg-secondary:  #1e293b  # 卡片背景
color-text-primary:  #f1f5f9  # 主文字
color-text-muted:    #94a3b8  # 弱化文字
color-accent:        #3b82f6  # 蓝（CTA）
color-success:       #22c55e  # 绿
color-warning:       #f59e0b  # 黄
color-error:         #ef4444  # 红
color-info:          #06b6d4  # 青

# Typography
font-sans: 'Inter', system-ui, sans-serif
font-mono: 'JetBrains Mono', monospace

text-xs:    12px / 16px
text-sm:    14px / 20px
text-base:  16px / 24px
text-lg:    18px / 28px
text-xl:    20px / 28px
text-2xl:   24px / 32px
text-3xl:   30px / 36px

# Spacing (4px 基准)
space-1:  4px
space-2:  8px
space-3:  12px
space-4:  16px
space-6:  24px
space-8:  32px
space-12: 48px

# Radius
radius-sm: 4px
radius-md: 8px
radius-lg: 12px
radius-xl: 16px

# Shadow
shadow-sm:  0 1px 2px 0 rgb(0 0 0 / 0.05)
shadow-md:  0 4px 6px -1px rgb(0 0 0 / 0.1)
shadow-lg:  0 10px 15px -3px rgb(0 0 0 / 0.1)
```

---

## §4 可访问性 (Accessibility)

| 维度 | 要求 |
|------|------|
| **WCAG 2.1 AA** | 全部页面 |
| **键盘导航** | 所有交互 |
| **屏幕阅读器** | ARIA 标签 |
| **颜色对比** | ≥ 4.5:1（正文） |
| **焦点可见** | 2px 蓝色 outline |
| **替代文本** | 所有图标/图片 |

---

## §5 响应式 (Responsive)

| 断点 | 宽度 | 适配 |
|------|------|------|
| **Mobile** | < 640px | 仅 Dashboard（v3.0） |
| **Tablet** | 640-1024px | Dashboard + Workflows |
| **Desktop** | ≥ 1024px | 全部 5 页面 |
| **Wide** | ≥ 1440px | 多列布局 |

---

## §6 国际化 (i18n)

| 语言 | v3.0 | v4.0 |
|------|------|------|
| 中文（简体） | ✅ 主 | ✅ |
| English | ✅ | ✅ |
| 中文（繁體） | ❌ | ✅ |
| 日本語 | ❌ | ✅ |

---

## §7 验收 (Definition of Done for UIUX)

- [ ] 5 Must 页面完成（Dashboard / Workflows / Memory / Audit / Plugins）
- [ ] 设计 token 全局统一
- [ ] WCAG 2.1 AA 验证
- [ ] 全部 Kernels 在 UI 中可视化
- [ ] 与 FastAPI 联调通过
- [ ] 用户（项目总监）签字

---

**本文档为 Phase 1 调研稿；用户确认后作为正式 v3.0 UIUX。**

