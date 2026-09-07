# LIUHAO X v3.0 — Designer UIUX Document

> **Version**: v3.0
> **Date**: 2026-09-05
> **Status**: AUTO-GENERATED from Architecture v3.0 + Console Frontend Spec + Definition Lock §83
> **Source**: `apps/console/console/` (React 19 + Vite 8 + TS 6), Architecture v3.0, Gap Analysis

---

## 1. Design Vision

**LIUHAO X Console** = Control center for Human-Sovereign Agent Operating System

**Design Principles**:
- **Human Sovereignty First** — Every screen shows human control points
- **Observability by Default** — Real-time visibility into agent operations
- **Bounded Autonomy Visualization** — Clear boundaries of agent authority
- **Audit Transparency** — One-click access to decision trails

---

## 2. Design Token System

### 2.1 Color Palette

```css
:root {
  /* Primary — LIUHAO X Brand */
  --color-primary-50:  #f0f4f8;
  --color-primary-100: #d9e2ec;
  --color-primary-200: #bcccdc;
  --color-primary-300: #9fb3c8;
  --color-primary-400: #829ab1;
  --color-primary-500: #627d98;    /* Primary brand */
  --color-primary-600: #486581;
  --color-primary-700: #334e68;
  --color-primary-800: #243b53;
  --color-primary-900: #102a43;

  /* Semantic — Status */
  --color-success-500: #2e7d32;
  --color-warning-500: #ed6c02;
  --color-error-500: #c62828;
  --color-info-500: #1565c0;

  /* Semantic — Agent Autonomy Levels */
  --color-autonomy-L0: #102a43;   /* Human only */
  --color-autonomy-L1: #243b53;
  --color-autonomy-L2: #334e68;
  --color-autonomy-L3: #486581;   /* Balanced */
  --color-autonomy-L4: #627d98;
  --color-autonomy-L5: #829ab1;
  --color-autonomy-L6: #9fb3c8;
  --color-autonomy-L7: #bcccdc;   /* Full autonomy */

  /* Neutral */
  --color-bg-primary:   #ffffff;
  --color-bg-secondary: #f5f7fa;
  --color-bg-tertiary:  #eaeef3;
  --color-text-primary:   #1a1f2e;
  --color-text-secondary: #4a5568;
  --color-text-muted:     #718096;
  --color-border-light:   #e2e8f0;
  --color-border-medium:  #cbd5e0;

  /* Human Sovereignty Accent */
  --color-sovereignty: #c53030;  /* Red accent for human checkpoints */
}
```

### 2.2 Typography

```css
:root {
  --font-family-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-family-mono: 'JetBrains Mono', 'Fira Code', monospace;

  --font-size-xs:    0.75rem;   /* 12px */
  --font-size-sm:    0.875rem;  /* 14px */
  --font-size-base:  1rem;      /* 16px */
  --font-size-lg:    1.125rem;  /* 18px */
  --font-size-xl:    1.25rem;   /* 20px */
  --font-size-2xl:   1.5rem;    /* 24px */
  --font-size-3xl:   1.875rem;  /* 30px */
  --font-size-4xl:   2.25rem;   /* 36px */

  --font-weight-normal: 400;
  --font-weight-medium: 500;
  --font-weight-semibold: 600;
  --font-weight-bold: 700;

  --line-height-tight: 1.25;
  --line-height-normal: 1.5;
  --line-height-relaxed: 1.75;
}
```

### 2.3 Spacing System

```css
:root {
  --space-1: 0.25rem;  /* 4px */
  --space-2: 0.5rem;   /* 8px */
  --space-3: 0.75rem;  /* 12px */
  --space-4: 1rem;     /* 16px */
  --space-5: 1.25rem;  /* 20px */
  --space-6: 1.5rem;   /* 24px */
  --space-8: 2rem;     /* 32px */
  --space-10: 2.5rem;  /* 40px */
  --space-12: 3rem;    /* 48px */
  --space-16: 4rem;    /* 64px */
}
```

### 2.4 Border Radius & Shadows

```css
:root {
  --radius-sm: 0.25rem;   /* 4px */
  --radius-md: 0.375rem;  /* 6px */
  --radius-lg: 0.5rem;    /* 8px */
  --radius-xl: 0.75rem;   /* 12px */
  --radius-full: 9999px;

  --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px rgba(0, 0, 0, 0.07);
  --shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.1);
  --shadow-xl: 0 20px 25px rgba(0, 0, 0, 0.15);
}
```

### 2.5 Breakpoints

```css
:root {
  --bp-sm:  640px;
  --bp-md:  768px;
  --bp-lg:  1024px;
  --bp-xl:  1280px;
  --bp-2xl: 1536px;
}
```

---

## 3. Component Library (Core Components)

### 3.1 Layout Components

| Component | Purpose | Variants |
|-----------|---------|----------|
| `AppShell` | Main layout with sidebar + header | Fixed / Collapsed sidebar |
| `PageContainer` | Consistent page padding + max-width | Standard / Full-width |
| `Section` | Semantic content grouping | Card / Divider / Inset |
| `Grid` | Responsive grid system | 12-col, auto-fit |

### 3.2 Navigation Components

| Component | Purpose | Variants |
|-----------|---------|----------|
| `Sidebar` | Primary navigation (5 core pages) | Expanded / Collapsed / Mobile drawer |
| `Breadcrumbs` | Hierarchical location | Standard / With actions |
| `Tabs` | Sub-page navigation | Line / Enclosed / Soft |

### 3.3 Data Display Components

| Component | Purpose | Variants |
|-----------|---------|----------|
| `MetricCard` | KPI display with trend | Single / Comparison / Sparkline |
| `DataTable` | Sortable, filterable, paginated | Dense / Comfortable / Row actions |
| `StatusBadge` | Agent/task/workflow status | Dot / Pill / Outline / Autonomy-level colored |
| `Timeline` | Event/decision/audit trail | Vertical / Horizontal / Compact |
| `TraceView` | Distributed trace waterfall | Full / Collapsed / Error-highlight |
| `LogViewer` | Structured log display | Streaming / Filtered / Searchable |

### 3.4 Form & Input Components

| Component | Purpose | Variants |
|-----------|---------|----------|
| `Button` | Primary actions | Primary / Secondary / Ghost / Danger / Sovereignty (red) |
| `Input` | Text/numeric input | Standard / With prefix/suffix / Validation states |
| `Select` | Single/multi select | Searchable / Grouped / Async options |
| `Switch` | Boolean toggle | Standard / With label / Sizes |
| `Slider` | Range input | Single / Range / With marks |
| `TextArea` | Multi-line input | Standard / Code / Markdown |
| `FileUpload` | Drag-drop file upload | Single / Multiple / Progress |

### 3.5 Feedback Components

| Component | Purpose | Variants |
|-----------|---------|----------|
| `Alert` | Inline notifications | Info / Success / Warning / Error / Sovereignty |
| `Toast` | Transient notifications | Top-right / Center / Bottom |
| `Modal` | Focused dialogs | Confirm / Form / Detail / Full-screen |
| `Drawer` | Side panels | Right / Left / Bottom |
| `Progress` | Operation progress | Linear / Circular / Indeterminate |
| `Skeleton` | Loading placeholders | Card / Table / Text / Chart |

### 3.6 Agent-Specific Components

| Component | Purpose | Key Features |
|-----------|---------|--------------|
| `AgentCard` | Agent summary + status | Autonomy level badge, health, last action |
| `AgentDetail` | Full agent view | Capabilities, permissions, memory, audit trail |
| `WorkflowCanvas` | Visual workflow editor | LangGraph node/edge, zoom, minimap |
| `CapabilityMatrix` | Capability registry view | Search, filter by scope/L-level, traceability |
| `PermissionMatrix` | RBAC/ABAC visualization | Role×Permission grid, inheritance |
| `AuditTrail` | Immutable decision log | Filter, search, hash-chain verification |
| `SovereigntyCheckpoint` | Human approval UI | Prominent red-accented, requires explicit confirm |

---

## 4. Page Specifications (5 Core Pages per §83)

### 4.1 Dashboard (MUST — MoSCoW)

**Purpose**: System health + agent fleet overview + sovereignty status

**Layout**:
```
┌─────────────────────────────────────────────────────────────┐
│ Header: LIUHAO X | User | Sovereignty Status | Notifications │
├─────────────┬───────────────────────────────────────────────┤
│ Sidebar     │ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────┐ │
│  Navigation │ │ Agents  │ │ Workflows│ │ Memory  │ │ Sec  │ │
│  (5 pages)  │ │ Active  │ │ Running  │ │ Usage   │ │Audit │ │
│             │ └─────────┘ └─────────┘ └─────────┘ └──────┘ │
│             │ ┌─────────────────────────────────────────┐   │
│             │ │ Agent Fleet Overview (DataTable)        │   │
│             │ │ [Name] [Type] [Autonomy L0-L7] [Status] │   │
│             │ │ [Health] [Last Action] [Sovereignty ✓]  │   │
│             │ └─────────────────────────────────────────┘   │
│             │ ┌──────────────┐ ┌────────────────────────┐   │
│             │ │ System Metrics│ │ Recent Sovereignty     │   │
│             │ │ (MetricCards) │ │ Checkpoints (Timeline) │   │
│             │ └──────────────┘ └────────────────────────┘   │
└─────────────┴───────────────────────────────────────────────┘
```

**Key Metrics**:
- Active agents count (by autonomy level L0-L7)
- Workflows running / queued / completed (24h)
- Memory usage (working/long-term/vector)
- Security audit: pending checkpoints, violations
- System health: API P95, DB connections, error rate

**Human Sovereignty Elements**:
- Prominent "Sovereignty Status" indicator in header
- Red-accented sovereignty checkpoints in recent activity
- One-click "Pause All Agents" emergency control

---

### 4.2 Workflows (MUST — MoSCoW)

**Purpose**: Create, monitor, debug agent workflows

**Layout**:
```
┌─────────────────────────────────────────────────────────────┐
│ Header + Sidebar                                             │
├─────────────────────────────────────────────────────────────┤
│ Toolbar: [New Workflow] [Import] [Filter] [Search]          │
├─────────────────────────────────────────────────────────────┤
│ ┌──────────────────────┐ ┌────────────────────────────────┐ │
│ │ Workflow List        │ │ Workflow Canvas (when selected)│ │
│ │ (DataTable)          │ │                                │ │
│ │ [Name] [Type]        │ │  [Node: Goal] → [Task] →       │ │
│ │ [Status] [Agent]     │ │  [Plan] → [Action] → [Verify] │ │
│ │ [Autonomy] [Duration]│ │                                │ │
│ │ [Sovereignty ✓]      │ │  [Zoom] [Minimap] [Debug]      │ │
│ └──────────────────────┘ └────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Workflow Canvas Features**:
- Visual LangGraph representation (nodes = steps, edges = transitions)
- Click node → side drawer with: inputs, outputs, LLM calls, traces, audit
- Real-time execution highlighting (running/completed/failed)
- Time-travel debugging (step back/forward through checkpoints)
- Human sovereignty checkpoints rendered as red-diamond nodes requiring explicit approval

**Interactions**:
- Drag-drop to reorder (where permitted by policy)
- Right-click node → "View Trace" / "View Audit" / "Replay from Here"
- Keyboard shortcuts for canvas navigation

---

### 4.3 Plugins (SHOULD)

**Purpose**: Browse, install, configure, monitor plugins

**Layout**:
```
┌─────────────────────────────────────────────────────────────┐
│ Header + Sidebar                                             │
├─────────────────────────────────────────────────────────────┤
│ Tabs: [Marketplace] [Installed] [Sandbox] [Dependencies]    │
├─────────────────────────────────────────────────────────────┤
│ Marketplace:                                                │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐             │
│ │ Plugin  │ │ Plugin  │ │ Plugin  │ │ Plugin  │  ...        │
│ │ Card    │ │ Card    │ │ Card    │ │ Card    │             │
│ │ [Icon]  │ │ [Icon]  │ │ [Icon]  │ │ [Icon]  │             │
│ │ Name    │ │ Name    │ │ Name    │ │ Name    │             │
│ │ Version │ │ Version │ │ Version │ │ Version │             │
│ │ [Install]             │ [Install]            │             │
│ └─────────┘ └─────────┘ └─────────┘ └─────────┘             │
│                                                              │
│ Installed: DataTable with [Enable/Disable] [Configure] [Logs]│
│ Sandbox: Runtime status per plugin (isolated/healthy/error)  │
│ Dependencies: Graph view of plugin dependency tree           │
└─────────────────────────────────────────────────────────────┘
```

---

### 4.4 Memory (SHOULD)

**Purpose**: Inspect and manage agent memory (10-layer architecture)

**Layout**:
```
┌─────────────────────────────────────────────────────────────┐
│ Header + Sidebar                                             │
├─────────────────────────────────────────────────────────────┤
│ Tabs: [Working] [Episodic] [Semantic] [Procedural] [Vector] │
│       [Short-term] [Long-term] [Collective] [Archival] [Meta]│
├─────────────────────────────────────────────────────────────┤
│ Memory Explorer (Tree + Search):                             │
│ ┌─────────────────────────┐ ┌─────────────────────────────┐ │
│ │ Memory Hierarchy        │ │ Memory Detail               │ │
│ │ ▸ Working (2.3 MB)      │ │ Key: conv_abc123            │ │
│ │   ▸ session_001         │ │ Tier: Working               │ │
│ │   ▸ session_002         │ │ TTL: 3600s                  │ │
│ │ ▸ Episodic (45 MB)      │ │ Scope: L3                   │ │
│ │   ▸ 2026-09-01          │ │ Size: 12 KB                 │ │
│ │   ▸ 2026-09-02          │ │ Content: [JSON viewer]      │ │
│ │ ▸ Vector Store (1.2 GB) │ │ Tags: [user, preference]    │ │
│ │   ▸ embeddings_v1       │ │ Audit: [View trail]         │ │
│ └─────────────────────────┘ └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Features**:
- Tier-by-tier memory browser with size/TTL/scope
- Search across all tiers (full-text + vector similarity)
- Memory inspection with JSON viewer + audit trail link
- Manual memory operations: promote/demote/expire/delete (with sovereignty checkpoint for destructive ops)
- Mem0 integration status indicator

---

### 4.5 Settings (COULD)

**Purpose**: System configuration, user preferences, security policies

**Layout**:
```
┌─────────────────────────────────────────────────────────────┐
│ Header + Sidebar                                             │
├─────────────────────────────────────────────────────────────┤
│ Tabs: [General] [Security] [Agents] [Providers] [Integrations]│
├─────────────────────────────────────────────────────────────┤
│ General:    System name, timezone, language, theme          │
│ Security:   RBAC roles, ABAC policies, Vault config,        │
│             Audit retention, Sovereignty checkpoint rules   │
│ Agents:     Default autonomy level, resource quotas,        │
│             capability defaults, trust thresholds           │
│ Providers:  LLM provider config, API keys (Vault),          │
│             cost limits, fallback order                     │
│ Integrations: PostgreSQL, Redis, Mem0, Vector store,        │
│               External APIs, Webhooks                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Human Sovereignty UI Patterns

### 5.1 Sovereignty Checkpoint Component

```tsx
// Mandatory for all high-risk operations per Definition Lock
<SovereigntyCheckpoint
  operation="deploy_agent_fleet"
  riskLevel="HIGH"
  requiredApprovers={['human_operator']}
  auditTrailLink="/audit/trace/abc123"
  onConfirm={handleDeploy}
  onCancel={handleCancel}
/>
```

**Visual**: Red border, red accent icon, explicit "I authorize this action" checkbox, shows audit trail preview, requires typed confirmation for CRITICAL operations.

### 5.2 Autonomy Level Indicator

```tsx
<AutonomyBadge level={3} showTooltip={true} />
```

**Visual**: Colored pill (L0-L7 gradient from §2.1), tooltip explains authority boundaries, clickable → opens permission matrix.

### 5.3 Audit Trail Link

Every agent action, decision, and state change shows:
```
[View Audit Trail] → opens AuditTrail component filtered to that correlation_id
```

### 5.4 Emergency Controls

Always visible in header (desktop) or floating action button (mobile):
- **Pause All Agents** — immediate stop, requires sovereignty checkpoint
- **Revoke All Permissions** — nuclear option, double confirmation
- **Export Audit Package** — one-click compliance export

---

## 6. Accessibility (WCAG 2.1 AA)

- **Color contrast**: All text ≥ 4.5:1, UI elements ≥ 3:1
- **Keyboard navigation**: Full app operable via keyboard, focus indicators visible
- **Screen reader**: Semantic HTML, ARIA labels, live regions for toasts/alerts
- **Motion**: Respect `prefers-reduced-motion`, disable non-essential animations
- **Language**: `lang="zh-CN"` primary, English technical terms preserved

---

## 7. Responsive Behavior

| Breakpoint | Sidebar | Layout Adjustments |
|------------|---------|-------------------|
| ≥ 1280px (xl) | Fixed 280px | Full grid, 4-col metric cards |
| 1024-1279px (lg) | Fixed 280px | 3-col metric cards |
| 768-1023px (md) | Collapsible drawer | 2-col metric cards, stacked canvas |
| < 768px (sm) | Mobile drawer | Single column, bottom nav bar |

---

## 8. Technical Implementation

### 8.1 Stack
- **Framework**: React 19 + TypeScript 6
- **Build**: Vite 8
- **Styling**: CSS Modules + CSS Variables (design tokens) — no CSS-in-JS runtime
- **State**: Zustand (global) + React Query (server state)
- **Charts**: Recharts (metrics) + React Flow (workflow canvas)
- **Icons**: Lucide React
- **Testing**: Vitest + React Testing Library + Playwright (E2E)

### 8.2 Project Structure

```
apps/console/console/
├── src/
│   ├── components/
│   │   ├── layout/          # AppShell, PageContainer, Section, Grid
│   │   ├── navigation/      # Sidebar, Breadcrumbs, Tabs
│   │   ├── data-display/    # MetricCard, DataTable, StatusBadge, Timeline, TraceView
│   │   ├── forms/           # Button, Input, Select, Switch, Slider, TextArea
│   │   ├── feedback/        # Alert, Toast, Modal, Drawer, Progress, Skeleton
│   │   ├── agent/           # AgentCard, AgentDetail, WorkflowCanvas, CapabilityMatrix, PermissionMatrix, AuditTrail, SovereigntyCheckpoint
│   │   └── index.ts         # Barrel exports
│   ├── pages/
│   │   ├── Dashboard/
│   │   ├── Workflows/
│   │   ├── Plugins/
│   │   ├── Memory/
│   │   └── Settings/
│   ├── hooks/               # useAgents, useWorkflows, useMemory, useSovereignty
│   ├── services/            # api.ts (generated from OpenAPI), websocket.ts
│   ├── store/               # Zustand stores
│   ├── styles/
│   │   ├── tokens.css       # Design tokens (CSS variables)
│   │   ├── global.css       # Reset + base styles
│   │   └── components/      # Component-specific CSS modules
│   ├── types/               # TypeScript interfaces from OpenAPI
│   ├── utils/               # formatters, validators, constants
│   ├── App.tsx
│   ├── main.tsx
│   └── vite-env.d.ts
├── index.html               # MISSING — MUST CREATE
├── package.json
├── tsconfig.json
├── vite.config.ts
└── .eslintrc.cjs
```

### 8.3 API Integration

- **OpenAPI Spec**: Generated from FastAPI backend (`src/gateway/main.py`)
- **Client Generation**: `openapi-typescript-codegen` → `src/services/api.ts`
- **WebSocket**: Real-time updates for agent status, workflow execution, sovereignty checkpoints
- **Auth**: JWT in Authorization header, refresh via secure cookie

---

## 9. Gaps & Implementation Priority (from Gap Analysis)

| Gap | Current | Required | Priority | Effort |
|-----|---------|----------|----------|--------|
| G5 | `index.html` missing | Create entry point + `App.tsx` skeleton | MUST | 1 day |
| — | 5 core pages | Dashboard, Workflows, Plugins, Memory, Settings | MUST | 10 days |
| — | Design token system | Colors, typography, spacing, shadows | MUST | 2 days |
| — | Component library | 25+ components per §3 | MUST | 15 days |
| — | Sovereignty UI patterns | Checkpoint, AutonomyBadge, EmergencyControls | MUST | 5 days |
| — | API integration | OpenAPI client + WebSocket | MUST | 3 days |
| — | E2E tests | Playwright for 5 pages + sovereignty flows | SHOULD | 5 days |
| — | Accessibility audit | WCAG 2.1 AA verification | SHOULD | 3 days |

---

## 10. Acceptance Criteria (Designer DoD)

- [ ] `index.html` + `App.tsx` created and serving
- [ ] Design tokens implemented as CSS variables
- [ ] 25+ core components built and documented (Storybook)
- [ ] 5 core pages functional with backend API integration
- [ ] Sovereignty checkpoint component working end-to-end
- [ ] Autonomy level visualization (L0-L7) on all agent views
- [ ] Audit trail accessible from every actionable item
- [ ] Emergency controls always accessible
- [ ] WCAG 2.1 AA compliance verified
- [ ] Responsive breakpoints working
- [ ] TypeScript strict mode, zero `any`
- [ ] Unit tests > 80% for components, E2E for critical flows

---

## 11. Open Decisions

| ID | Decision | Recommendation |
|----|----------|----------------|
| OD-U1 | Chart library | Recharts (lightweight, React-native) |
| OD-U2 | Workflow canvas | React Flow (proven, good TypeScript) |
| OD-U3 | State management | Zustand + React Query (simple, performant) |
| OD-U4 | Internationalization | i18next (later phase, Chinese primary now) |

---

*Auto-generated from Architecture v3.0 + Definition Lock §83 + Console frontend spec. Ready for user review and approval.*