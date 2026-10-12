# 侧边栏导航结构

本文档定义了 LiuHao AI OS 文档站点的侧边栏导航结构。

---

## 导航结构

```
LiuHao AI OS 文档
│
├── 🏠 首页
│   └── （链接到 README）
│
├── 🚀 快速开始
│   ├── 安装指南
│   │   └── knowledge_base/getting-started/installation.md
│   └── 快速上手指南
│       └── knowledge_base/getting-started/quickstart.md
│
├── 💡 核心概念
│   ├── 智能体
│   │   └── knowledge_base/core-concepts/agents.md
│   ├── 记忆系统
│   │   └── knowledge_base/core-concepts/memory.md
│   ├── 规划引擎
│   │   └── knowledge_base/core-concepts/planning.md
│   └── 多智能体协作
│       └── knowledge_base/core-concepts/multi_agent.md
│
├── 📖 用户指南
│   ├── 智能体指南
│   │   └── knowledge_base/user-guide/agent_guide.md
│   ├── 记忆指南
│   │   └── knowledge_base/user-guide/memory_guide.md
│   ├── 流水线指南
│   │   └── knowledge_base/user-guide/pipeline_guide.md
│   ├── 插件指南
│   │   └── knowledge_base/user-guide/plugins_guide.md
│   └── SDK 指南
│       └── knowledge_base/user-guide/sdk_guide.md
│
├── 🏗️ 架构设计
│   ├── 架构总览
│   │   └── knowledge_base/architecture/overview.md
│   ├── 内核架构
│   │   └── knowledge_base/architecture/kernels.md
│   └── 安全架构
│       └── knowledge_base/architecture/security.md
│
├── 🔌 API 参考
│   ├── 智能体 API
│   │   └── knowledge_base/api-reference/agents_api.md
│   ├── 记忆 API
│   │   └── knowledge_base/api-reference/memory_api.md
│   ├── 知识 API
│   │   └── knowledge_base/api-reference/knowledge_api.md
│   ├── 任务 API
│   │   └── knowledge_base/api-reference/tasks_api.md
│   └── 健康 API
│       └── knowledge_base/api-reference/health_api.md
│
├── 🎓 教程
│   ├── 构建你的第一个智能体
│   │   └── tutorials/build_your_first_agent.md
│   ├── 构建研究机器人
│   │   └── tutorials/build_a_research_bot.md
│   ├── 构建代码审查员
│   │   └── tutorials/build_a_code_reviewer.md
│   ├── 构建数据流水线
│   │   └── tutorials/build_a_data_pipeline.md
│   ├── 构建多智能体系统
│   │   └── tutorials/build_multi_agent_system.md
│   └── 部署到生产环境
│       └── tutorials/deploy_to_production.md
│
├── ❓ 帮助
│   ├── 常见问题
│   │   └── knowledge_base/faq/faq.md
│   ├── 故障排除
│   │   └── knowledge_base/troubleshooting/common_issues.md
│   ├── 术语表
│   │   └── GLOSSARY.md
│   └── 更新日志
│       └── knowledge_base/CHANGELOG.md
│
└── 📚 更多资源
    ├── 项目 README
    │   └── README.md
    ├── 架构文档（权威）
    │   └── docs/ARCHITECTURE.md
    └── GitHub 仓库
        └── https://github.com/LiuHao-AI-OS/LiuHao-AI-OS
```

---

## YAML 格式（用于静态站点生成器）

```yaml
# sidebars.yaml
docs:
  - title: 快速开始
    items:
      - id: installation
        label: 安装指南
        path: knowledge_base/getting-started/installation.md
      - id: quickstart
        label: 快速上手指南
        path: knowledge_base/getting-started/quickstart.md

  - title: 核心概念
    items:
      - id: agents
        label: 智能体
        path: knowledge_base/core-concepts/agents.md
      - id: memory
        label: 记忆系统
        path: knowledge_base/core-concepts/memory.md
      - id: planning
        label: 规划引擎
        path: knowledge_base/core-concepts/planning.md
      - id: multi-agent
        label: 多智能体协作
        path: knowledge_base/core-concepts/multi_agent.md

  - title: 用户指南
    items:
      - id: agent-guide
        label: 智能体指南
        path: knowledge_base/user-guide/agent_guide.md
      - id: memory-guide
        label: 记忆指南
        path: knowledge_base/user-guide/memory_guide.md
      - id: pipeline-guide
        label: 流水线指南
        path: knowledge_base/user-guide/pipeline_guide.md
      - id: plugins-guide
        label: 插件指南
        path: knowledge_base/user-guide/plugins_guide.md
      - id: sdk-guide
        label: SDK 指南
        path: knowledge_base/user-guide/sdk_guide.md

  - title: 架构设计
    items:
      - id: architecture-overview
        label: 架构总览
        path: knowledge_base/architecture/overview.md
      - id: kernel-architecture
        label: 内核架构
        path: knowledge_base/architecture/kernels.md
      - id: security-architecture
        label: 安全架构
        path: knowledge_base/architecture/security.md

  - title: API 参考
    items:
      - id: agents-api
        label: 智能体 API
        path: knowledge_base/api-reference/agents_api.md
      - id: memory-api
        label: 记忆 API
        path: knowledge_base/api-reference/memory_api.md
      - id: knowledge-api
        label: 知识 API
        path: knowledge_base/api-reference/knowledge_api.md
      - id: tasks-api
        label: 任务 API
        path: knowledge_base/api-reference/tasks_api.md
      - id: health-api
        label: 健康 API
        path: knowledge_base/api-reference/health_api.md

  - title: 教程
    items:
      - id: tutorial-first-agent
        label: 构建你的第一个智能体
        path: tutorials/build_your_first_agent.md
      - id: tutorial-research-bot
        label: 构建研究机器人
        path: tutorials/build_a_research_bot.md
      - id: tutorial-code-reviewer
        label: 构建代码审查员
        path: tutorials/build_a_code_reviewer.md
      - id: tutorial-data-pipeline
        label: 构建数据流水线
        path: tutorials/build_a_data_pipeline.md
      - id: tutorial-multi-agent
        label: 构建多智能体系统
        path: tutorials/build_multi_agent_system.md
      - id: tutorial-deploy-production
        label: 部署到生产环境
        path: tutorials/deploy_to_production.md

  - title: 帮助
    items:
      - id: faq
        label: 常见问题
        path: knowledge_base/faq/faq.md
      - id: troubleshooting
        label: 故障排除
        path: knowledge_base/troubleshooting/common_issues.md
      - id: glossary
        label: 术语表
        path: GLOSSARY.md
      - id: changelog
        label: 更新日志
        path: knowledge_base/CHANGELOG.md
```

---

## 底部导航（Footer）

```yaml
footer:
  - title: 文档
    items:
      - label: 知识库
        path: knowledge_base/README.md
      - label: 教程
        path: tutorials/README.md
      - label: API 参考
        path: knowledge_base/api-reference/agents_api.md
      - label: 更新日志
        path: knowledge_base/CHANGELOG.md

  - title: 资源
    items:
      - label: 术语表
        path: GLOSSARY.md
      - label: 常见问题
        path: knowledge_base/faq/faq.md
      - label: 故障排除
        path: knowledge_base/troubleshooting/common_issues.md

  - title: 社区
    items:
      - label: GitHub
        href: https://github.com/LiuHao-AI-OS/LiuHao-AI-OS
      - label: 讨论区
        href: https://github.com/LiuHao-AI-OS/LiuHao-AI-OS/discussions
      - label: 提交 Issue
        href: https://github.com/LiuHao-AI-OS/LiuHao-AI-OS/issues
```

---

## 面包屑导航

面包屑路径示例：

```
首页 / 核心概念 / 智能体
首页 / 用户指南 / 智能体指南
首页 / 教程 / 构建你的第一个智能体
首页 / 帮助 / 常见问题
```
