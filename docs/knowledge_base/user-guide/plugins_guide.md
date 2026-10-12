# 插件用户指南

插件是扩展 LiuHao AI OS 功能的重要方式。本指南介绍如何安装、配置和使用插件，以及如何在智能体和流水线中利用插件能力。

---

## 插件基础

### 什么是插件

插件是一种可热加载的功能模块，可以扩展 LiuHao AI OS 的能力，包括：

- 新增工具（Tools）：为智能体提供新的操作能力
- 新增数据源（Sources）：为流水线提供新的数据接入方式
- 新增输出槽（Sinks）：为流水线提供新的数据输出方式
- 新增内核扩展（Kernel Extensions）：扩展系统内核能力
- 新增集成（Integrations）：与第三方服务对接

### 插件架构

```
┌─────────────────────────────────────────────┐
│              LiuHao AI OS                    │
├─────────────────────────────────────────────┤
│             Plugin Manager                   │
│  (插件管理器：注册、加载、生命周期管理)        │
├───────┬───────┬───────┬───────┬─────────────┤
│ 工具   │ 数据源 │ 输出槽 │ 集成  │ 内核扩展    │
│ 插件   │ 插件   │ 插件   │ 插件  │ 插件        │
└───────┴───────┴───────┴───────┴─────────────┘
```

### 插件类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `tool` | 智能体工具插件 | 天气查询、代码执行、邮件发送 |
| `source` | 数据源插件 | Slack 消息、Notion 页面、Google Drive |
| `sink` | 输出槽插件 | 飞书通知、Jira 工单、Webhook |
| `integration` | 集成插件 | GitHub、Jira、Salesforce 全功能集成 |
| `kernel_extension` | 内核扩展插件 | 自定义记忆后端、定制策略引擎 |
| `ui_extension` | UI 扩展插件 | 自定义驾驶舱页面、仪表盘组件 |

---

## 安装插件

### 方式一：通过插件市场安装

LiuHao AI OS 内置插件市场，可以浏览和安装官方及社区插件。

```bash
# 搜索插件
liuhao plugin search "slack"

# 安装插件
liuhao plugin install liuhao-slack-plugin

# 安装指定版本
liuhao plugin install liuhao-slack-plugin==1.2.0

# 从本地文件安装
liuhao plugin install ./my-plugin.zip
```

### 方式二：通过 Python pip 安装

```bash
pip install liuhao-plugin-slack
```

### 方式三：手动安装

将插件文件放入插件目录：

```bash
# 默认插件目录
# Linux/Mac: ~/.liuhao/plugins/
# Windows: %APPDATA%\liuhao\plugins\

# 或项目本地插件目录
./plugins/
```

### 查看已安装插件

```bash
# 列出已安装的插件
liuhao plugin list

# 查看插件详情
liuhao plugin info liuhao-slack-plugin
```

输出示例：

```
已安装插件 (5):
  - liuhao-slack-plugin v1.2.0 [integration] Slack 消息平台集成
  - liuhao-github-plugin v2.0.1 [integration] GitHub 代码托管集成
  - liuhao-weather-plugin v0.5.0 [tool] 天气查询工具
  - my-custom-plugin v0.1.0 [tool] 我的自定义插件
  - liuhao-notion-plugin v1.0.0 [source/sink] Notion 知识库集成
```

---

## 配置插件

### 插件配置方式

插件配置可以通过以下方式设置（优先级从高到低）：

1. 环境变量
2. 配置文件
3. 插件默认值

### 环境变量配置

```bash
# Slack 插件配置
LIUHAO_PLUGIN_SLACK_BOT_TOKEN=xoxb-your-token
LIUHAO_PLUGIN_SLACK_SIGNING_SECRET=your-secret

# GitHub 插件配置
LIUHAO_PLUGIN_GITHUB_TOKEN=ghp-your-token
LIUHAO_PLUGIN_GITHUB_ORG=your-org
```

### 配置文件配置

在 `config/plugins.json` 中配置：

```json
{
  "plugins": {
    "slack": {
      "enabled": true,
      "config": {
        "bot_token": "xoxb-your-token",
        "default_channel": "#general"
      }
    },
    "github": {
      "enabled": true,
      "config": {
        "token": "ghp-your-token",
        "default_org": "your-org"
      }
    },
    "weather": {
      "enabled": true,
      "config": {
        "api_key": "your-api-key",
        "default_city": "Beijing"
      }
    }
  }
}
```

### 启用/禁用插件

```bash
# 启用插件
liuhao plugin enable slack

# 禁用插件
liuhao plugin disable slack

# 卸载插件
liuhao plugin uninstall liuhao-slack-plugin
```

---

## 在智能体中使用插件

### 工具插件

工具插件为智能体提供新的可调用工具。

#### 启用工具

创建智能体时指定可用的工具：

```python
from liuhao import LiuHaoClient

client = LiuHaoClient()

agent = client.agents.create(
    name="运营助手",
    role="general",
    config={
        "tools": [
            "weather.get_current",      # 天气查询
            "slack.send_message",       # Slack 发消息
            "github.get_issues",        # GitHub 查 Issue
            "notion.search_pages"       # Notion 搜索
        ]
    }
)
```

#### 查看可用工具

```python
# 列出所有可用工具（来自所有已启用插件）
tools = client.tools.list()
for tool in tools:
    print(f"{tool.name}: {tool.description}")
    print(f"  来源: {tool.plugin_name} v{tool.plugin_version}")
    print(f"  参数: {tool.parameters}")
    print()
```

#### 工具调用示例

智能体会根据任务自主决定调用哪些工具：

```python
result = client.agents.execute(
    agent_id="agent-abc123",
    task="查一下北京今天的天气，然后把结果发到 Slack 的 #general 频道"
)
```

智能体可能的执行过程：
1. 调用 `weather.get_current` 获取北京天气
2. 调用 `slack.send_message` 发送到 Slack

### 集成插件

集成插件提供更深度的第三方服务对接，通常包含多个工具、数据源和输出槽。

```python
# 使用 GitHub 集成
agent = client.agents.create(
    name="开发助手",
    role="executor",
    config={
        "integrations": ["github"],
        "github": {
            "default_repo": "my-org/my-repo"
        }
    }
)

result = client.agents.execute(
    agent_id="agent-dev",
    task="帮我查看当前 Sprint 有哪些开放的 Issue，并按优先级排序"
)
```

---

## 在流水线中使用插件

### 数据源插件

插件可以提供新的数据源类型：

```python
from liuhao.pipelines import PipelineBuilder

# 使用 Slack 数据源（从 Slack 频道获取消息）
pipeline = (
    PipelineBuilder()
    .source("slack", 
            channel="#support",
            since="24h",
            plugin="liuhao-slack-plugin")
    .transform("filter", condition="reactions contains ':bug:'")
    .transform("ai_transform", 
               prompt="分析这个 bug 报告，提取问题描述和严重程度",
               output_fields=["bug_description", "severity"])
    .sink("jira", 
          project="SUPPORT",
          issue_type="Bug",
          plugin="liuhao-jira-plugin")
    .build()
)
```

### 输出槽插件

```python
pipeline = (
    PipelineBuilder()
    .source("database", query="SELECT * FROM weekly_metrics")
    .transform("aggregate", group_by=["department"],
               aggregations=[{"field": "revenue", "function": "sum"}])
    .sink("feishu",                    # 飞书机器人
          webhook_url="https://open.feishu.cn/...",
          template="weekly_report",
          plugin="liuhao-feishu-plugin")
    .build()
)
```

---

## 插件目录

### 官方插件

以下是 LiuHao AI OS 官方维护的插件：

| 插件名称 | 类型 | 说明 | 状态 |
|---------|------|------|------|
| `liuhao-slack-plugin` | integration | Slack 消息平台集成 | 稳定 |
| `liuhao-github-plugin` | integration | GitHub 代码托管集成 | 稳定 |
| `liuhao-gitlab-plugin` | integration | GitLab 代码托管集成 | 稳定 |
| `liuhao-jira-plugin` | integration | Jira 项目管理集成 | 稳定 |
| `liuhao-notion-plugin` | integration | Notion 知识库集成 | 稳定 |
| `liuhao-feishu-plugin` | integration | 飞书办公平台集成 | 稳定 |
| `liuhao-salesforce-plugin` | integration | Salesforce CRM 集成 | Beta |
| `liuhao-weather-plugin` | tool | 天气查询工具 | 稳定 |
| `liuhao-web-search-plugin` | tool | 网页搜索工具 | 稳定 |
| `liuhao-calculator-plugin` | tool | 科学计算工具 | 稳定 |

### 社区插件

社区插件由开发者社区贡献，可以在插件市场浏览。

> **注意**：社区插件的质量和安全性不保证，安装前请审查代码和权限。

---

## 插件开发入门

### 创建一个简单的工具插件

```python
# my_plugin/__init__.py
from liuhao.plugins import Plugin, tool

class MyPlugin(Plugin):
    name = "my-plugin"
    version = "0.1.0"
    description = "我的自定义插件"
    author = "Your Name"
    
    @tool(
        name="greeting.say_hello",
        description="向用户打招呼",
        parameters={
            "name": {"type": "string", "description": "用户名", "required": True}
        }
    )
    def say_hello(self, name: str) -> str:
        """打招呼函数"""
        return f"你好，{name}！欢迎使用 LiuHao AI OS！"
    
    @tool(
        name="calculator.add",
        description="两个数相加",
        parameters={
            "a": {"type": "number", "description": "第一个数", "required": True},
            "b": {"type": "number", "description": "第二个数", "required": True}
        }
    )
    def add(self, a: float, b: float) -> float:
        """加法运算"""
        return a + b
```

### 插件清单（manifest）

```yaml
# plugin.yaml
name: my-plugin
version: 0.1.0
description: 我的自定义插件
author: Your Name
type: tool

entry_point: my_plugin:MyPlugin

dependencies:
  - liuhao >= 3.0.0

permissions:
  - network:outbound     # 允许网络请求
  - filesystem:read      # 允许读取文件

config_schema:
  api_key:
    type: string
    description: API 密钥
    required: false
```

> 完整的插件开发指南请参考开发者文档。

---

## 插件故障排除

### 问题：插件加载失败

**症状**：启动时报告插件加载错误

**排查步骤**：

```bash
# 查看插件详细状态
liuhao plugin status <plugin-name>

# 查看加载日志
liuhao plugin logs <plugin-name>

# 检查依赖
pip check
```

**常见原因**：
- 缺少 Python 依赖
- 配置不正确
- 版本不兼容
- 插件文件损坏

### 问题：工具调用失败

**症状**：智能体调用工具时报错

**排查步骤**：

1. 确认插件已启用
2. 检查插件配置（API key、token 等）
3. 查看工具调用日志
4. 测试工具是否能单独运行

```bash
# 手动测试工具
liuhao tool call weather.get_current --param city=Beijing
```

### 问题：插件版本冲突

**症状**：启动时报告版本冲突

**解决方案**：

```bash
# 查看插件依赖
liuhao plugin deps

# 更新插件
liuhao plugin update <plugin-name>

# 回退版本
liuhao plugin install <plugin-name>==<old-version>
```

### 问题：插件安全警告

**症状**：插件请求超出预期的权限

**最佳实践**：
- 只从可信来源安装插件
- 审查插件的权限请求
- 生产环境使用签名验证的官方插件
- 定期更新插件以获取安全修复

---

## 插件安全最佳实践

1. **最小权限原则**：只授予插件必要的权限
2. **来源验证**：优先使用官方和可信社区插件
3. **定期更新**：保持插件版本最新，获取安全修复
4. **权限审计**：定期审查插件权限使用情况
5. **沙箱隔离**：敏感环境中使用沙箱运行插件
6. **配置加密**：插件的密钥、token 等敏感配置使用加密存储

---

## 下一步

- [SDK 指南](sdk_guide.md) - 使用 SDK 开发插件
- [智能体用户指南](agent_guide.md) - 智能体中使用插件工具
- [流水线用户指南](pipeline_guide.md) - 流水线中使用插件数据源
