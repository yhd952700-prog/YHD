# LiuHao AI OS - 前端组件库

基于 **React 18 + TypeScript + Vite + Tailwind CSS** 的鎏灏 AI OS 前端组件库，覆盖 Agent 管理、任务看板、对话、记忆、知识库、仪表盘、工作流、插件、设置、通知与整体布局等全部核心界面。

## 目录结构

```
frontend/
├── package.json              # 依赖与脚本
├── tsconfig.json             # TypeScript 配置
├── vite.config.ts            # Vite 配置（含路径别名 @/）
├── tailwind.config.js        # Tailwind 配置（brand/status 色板、组件类）
├── postcss.config.js
├── README.md
└── src/
    ├── index.ts              # 主导出（组件 / hooks / types / utils）
    ├── index.css             # Tailwind 指令与 lh-* 组件类
    ├── types/
    │   └── index.ts          # 全部领域模型类型（Agent / Task / Chat / Memory / Knowledge / Workflow / Plugin / Notification / Metrics / Settings / Search / Navigation）
    ├── utils/
    │   └── index.ts          # cn、格式化（字节/数字/货币/百分比/时长/时间）、genId、avatarColor、initials
    ├── hooks/
    │   ├── useAgent.ts       # Agent CRUD、筛选、指标刷新
    │   ├── useMemory.ts      # 记忆检索、写入、删除、统计
    │   ├── useKnowledge.ts   # 知识库集合/文档管理、上传进度、语义搜索
    │   ├── useTask.ts        # 任务看板状态、移动、进度
    │   ├── useWebSocket.ts   # WebSocket 连接、重连、订阅
    │   └── index.ts
    └── components/           # 20 个组件，每个独立目录（.tsx + index.ts）
        ├── AgentCard/        # Agent 卡片（状态、指标、能力标签）
        ├── AgentList/        # Agent 列表（筛选、网格布局）
        ├── TaskCard/         # 任务卡片（优先级、进度、步骤）
        ├── TaskBoard/        # 看板（多列、WIP 限制、拖拽回调）
        ├── ChatWindow/       # 对话窗口（消息流、输入、流式状态）
        ├── ChatMessage/      # 单条消息（Markdown、工具调用、附件）
        ├── MemoryPanel/      # 记忆面板（树形结构、类型筛选、重要性）
        ├── KnowledgeBase/    # 知识库（集合、文档上传、处理状态）
        ├── Dashboard/        # 仪表盘（Widget 组合布局）
        ├── MetricCard/       # 指标卡（趋势 Sparkline、环比）
        ├── CostTracker/      # 成本追踪（按模型/提供商/Agent 拆分）
        ├── HealthStatus/     # 服务健康状态
        ├── WorkflowBuilder/  # 工作流构建器（节点、连线、画布）
        ├── PluginManager/    # 插件管理（启用开关、权限、状态筛选）
        ├── SettingsPanel/    # 设置面板（分组锚点导航、五种字段类型）
        ├── NotificationCenter/ # 通知中心（未读数、下拉面板）
        ├── SearchBar/        # 全局搜索（键盘导航、分组结果）
        ├── Sidebar/          # 侧边导航（树形、折叠、徽标）
        ├── Header/           # 顶栏（Logo、中间插槽、用户菜单）
        └── CodeEditor/       # 代码编辑器（内置语法高亮、行号、只读模式）
```

## 安装与启动

```bash
cd frontend
npm install        # 或 pnpm install
npm run dev        # 启动开发服务器
npm run build      # 类型检查 + 生产构建
npm run typecheck  # 仅 TypeScript 检查
npm run lint       # ESLint
```

## 使用示例

```tsx
import {
  Header,
  Sidebar,
  AgentList,
  NotificationCenter,
  SearchBar,
  useAgent,
  type NavItem,
} from './src';

const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', label: '仪表盘', icon: '📊', path: '/' },
  { id: 'agents', label: 'Agent', icon: '🤖', path: '/agents', badge: 3 },
  {
    id: 'memory',
    label: '记忆',
    icon: '🧠',
    children: [
      { id: 'memory-episodic', label: '情景记忆', path: '/memory/episodic' },
      { id: 'memory-semantic', label: '语义记忆', path: '/memory/semantic' },
    ],
  },
];

function App() {
  const { agents, loading } = useAgent();

  return (
    <div className="flex h-screen">
      <Sidebar items={NAV_ITEMS} activeId="agents" />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header
          center={<SearchBar onSearch={console.log} />}
          actions={<NotificationCenter notifications={[]} />}
          user={{ name: '刘灏', role: '管理员' }}
        />
        <main className="flex-1 overflow-y-auto p-6">
          <AgentList agents={agents} loading={loading} />
        </main>
      </div>
    </div>
  );
}
```

## 设计约定

- **组件规范**：`memo` 包裹的函数组件，导出完整 `Props` interface；每个组件目录下 `index.ts` 统一 re-export。
- **样式**：Tailwind 原子类 + `index.css` 中的 `lh-card` / `lh-btn` / `lh-input` / `lh-badge` / `lh-chip` / `lh-skeleton` 组件类；全部组件支持 `dark:` 暗色模式。
- **可访问性**：交互元素均带 ARIA 属性（`role`、`aria-label`、`aria-selected` 等），支持键盘操作。
- **文案**：界面文案统一使用中文。
- **路径别名**：`@/` 指向 `src/`（见 `vite.config.ts` 与 `tsconfig.json`）。
