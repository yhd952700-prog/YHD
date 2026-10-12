# 构建你的第一个智能体

本教程将手把手带你创建、配置并运行你的第一个 LiuHao AI OS 智能体。

---

## 前置条件

在开始之前，请确保你已经：

- 完成了 [安装指南](../knowledge_base/getting-started/installation.md)
- LiuHao AI OS 服务正在运行（`http://127.0.0.1:8080`）
- 已安装 Python SDK：`pip install liuhao`
- 有一个 API Key（如果启用了认证的话）

---

## 第一步：创建智能体

我们来创建一个"个人助理"智能体。

### 1.1 编写创建脚本

创建一个文件 `create_my_agent.py`：

```python
"""
创建你的第一个智能体
"""
from liuhao import LiuHaoClient

# 1. 初始化客户端
client = LiuHaoClient(
    base_url="http://127.0.0.1:8080",
    # api_key="your-api-key"  # 如果启用了认证
)

# 2. 定义智能体配置
agent_config = {
    "model": "qwen2.5:3b",
    "temperature": 0.7,
    "max_tokens": 2048,
    "system_prompt": """你是小明的私人助理，名叫"小灏"。

你的性格：
- 友善、耐心、乐于助人
- 回答简洁明了，不啰嗦
- 遇到不确定的事情会如实说明

你的能力：
- 回答问题
- 管理日程
- 记录备忘
- 简单的计算和分析

请始终用中文回复。""",
    "tools": [
        "calculator.add",
        "calculator.multiply",
        "datetime.now"
    ],
    "memory": {
        "enabled": True,
        "semantic_memory_enabled": True,
        "episodic_memory_enabled": True
    }
}

# 3. 创建智能体
agent = client.agents.create(
    name="小灏",
    role="general",
    description="小明的私人助理，友善、耐心、乐于助人",
    config=agent_config
)

print(f"✅ 智能体创建成功！")
print(f"   ID: {agent.id}")
print(f"   名称: {agent.name}")
print(f"   角色: {agent.role}")
print(f"   状态: {agent.status}")

# 保存智能体 ID 供后续使用
with open("agent_id.txt", "w") as f:
    f.write(agent.id)
```

### 1.2 运行脚本

```bash
python create_my_agent.py
```

你应该看到类似这样的输出：

```
✅ 智能体创建成功！
   ID: agent-abc123def456
   名称: 小灏
   角色: general
   状态: idle
```

### 1.3 验证

让我们确认智能体真的创建成功了：

```bash
curl http://127.0.0.1:8080/v1/agents/agent-abc123def456
```

或者在驾驶舱中查看：打开 `http://127.0.0.1:5173`，进入「智能体管理」。

---

## 第二步：和智能体对话

现在智能体已经创建好了，让我们和它说说话。

### 2.1 第一次对话

创建 `chat_with_agent.py`：

```python
"""
和智能体对话
"""
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

# 读取之前保存的智能体 ID
with open("agent_id.txt", "r") as f:
    agent_id = f.read().strip()

# 开始对话会话
session = client.agents.start_session(agent_id)

print("=== 与小灏对话 ===")
print("输入 'quit' 退出\n")

while True:
    user_input = input("你: ")
    
    if user_input.lower() in ("quit", "exit", "退出"):
        print("再见！")
        break
    
    if not user_input.strip():
        continue
    
    # 发送消息
    print("小灏: ", end="", flush=True)
    
    # 使用流式输出，效果更自然
    full_response = ""
    for chunk in session.stream(user_input):
        if chunk.type == "text":
            print(chunk.content, end="", flush=True)
            full_response += chunk.content
    
    print("\n")
```

### 2.2 运行对话

```bash
python chat_with_agent.py
```

试试问几个问题：

```
你: 你好，你是谁？
小灏: 你好！我是小灏，你的私人助理。有什么我可以帮你的吗？

你: 今天天气怎么样？
小灏: 抱歉，我目前没有获取实时天气的能力。
      如果你需要天气查询功能，可以为我添加天气插件。

你: 帮我算一下 1234 * 5678 等于多少
小灏: 让我算一下... 1234 × 5678 = 7,006,652
```

---

## 第三步：给智能体添加记忆

真正强大的智能体需要有记忆能力。让我们教智能体记住一些关于你的事情。

### 3.1 存储个人偏好

创建 `teach_agent.py`：

```python
"""
教智能体记住你的偏好
"""
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

with open("agent_id.txt", "r") as f:
    agent_id = f.read().strip()

# 存储一些个人偏好
memories = [
    {
        "content": "用户名叫小明，是一名软件工程师",
        "memory_type": "semantic",
        "category": "user_profile",
        "tags": ["用户", "姓名", "职业"],
        "importance": 0.9
    },
    {
        "content": "用户喜欢简洁直接的回答，不喜欢啰嗦",
        "memory_type": "semantic",
        "category": "user_preferences",
        "tags": ["用户", "偏好", "沟通风格"],
        "importance": 0.85
    },
    {
        "content": "用户对 Python 和人工智能感兴趣",
        "memory_type": "semantic",
        "category": "user_interests",
        "tags": ["用户", "兴趣", "Python", "AI"],
        "importance": 0.8
    },
    {
        "content": "用户通常在晚上 8 点到 10 点有空",
        "memory_type": "semantic",
        "category": "user_schedule",
        "tags": ["用户", "时间", "日程"],
        "importance": 0.7
    }
]

print("正在教智能体记住你的偏好...\n")

for mem in memories:
    result = client.memory.store(
        agent_id=agent_id,
        **mem
    )
    print(f"✅ 已记住: {mem['content'][:40]}...")

print(f"\n共存储了 {len(memories)} 条记忆")
print("\n现在试试问智能体一些关于你的问题吧！")
```

### 3.2 验证记忆

运行脚本：

```bash
python teach_agent.py
```

然后重新运行对话脚本，试试问：

```
你: 我叫什么名字？做什么工作？
小灏: 你叫小明，是一名软件工程师。

你: 我对什么感兴趣？
小灏: 你对 Python 和人工智能感兴趣。

你: 记住，我最喜欢的语言是 Python
小灏: 好的，我记住了，你最喜欢的编程语言是 Python。
```

---

## 第四步：理解各部分作用

让我们回顾一下，刚才创建的智能体由哪些部分组成：

### 4.1 身份（Identity）

- **名称**：小灏
- **角色**：general（通用型）
- **描述**：私人助理

### 4.2 配置（Config）

- **模型**：qwen2.5:3b — 智能体使用的 LLM
- **温度**：0.7 — 控制回复的随机性
- **系统提示词**：定义智能体的性格和能力边界

### 4.3 工具（Tools）

- `calculator.add` — 加法计算
- `calculator.multiply` — 乘法运算
- `datetime.now` — 获取当前时间

### 4.4 记忆（Memory）

- 工作记忆：当前对话的上下文
- 情景记忆：记录每次对话的内容
- 语义记忆：存储用户偏好等长期知识

---

## 第五步：自定义你的智能体

现在你已经掌握了基础，让我们来做一些自定义。

### 5.1 修改角色和性格

让我们把智能体改成一个"编程助手"：

```python
agent = client.agents.create(
    name="代码助手",
    role="creator",
    config={
        "model": "qwen2.5:7b",  # 用更大的模型写代码更好
        "temperature": 0.2,     # 低温度，输出更确定
        "system_prompt": """你是一名资深软件工程师，名叫"代码助手"。

你的专长：
- Python、JavaScript、Go 等多种编程语言
- 代码审查和最佳实践建议
- 调试和问题排查
- 算法和数据结构

你的风格：
- 代码质量优先
- 解释清晰，有注释
- 考虑边界情况和错误处理
- 给出完整可运行的代码

回答时先说明思路，再给代码。""",
        "tools": ["code_analyzer", "file_reader"]
    }
)
```

### 5.2 添加更多工具

你可以通过插件为智能体添加更多工具：

```bash
# 安装天气插件
liuhao plugin install liuhao-weather-plugin

# 安装网页搜索插件
liuhao plugin install liuhao-web-search-plugin
```

然后在智能体配置中启用：

```python
config = {
    "tools": [
        "weather.get_current",
        "weather.get_forecast",
        "web_search.search",
        "web_search.summarize"
    ]
}
```

### 5.3 调整记忆设置

```python
config = {
    "memory": {
        "enabled": True,
        "working_memory_size": 30,          # 更大的工作记忆
        "semantic_memory_enabled": True,
        "episodic_memory_enabled": True,
        "consolidation_threshold": 0.6,     # 更多内容被巩固
        "episodic_retention_days": 180      # 保留半年的对话历史
    }
}
```

---

## 第六步：最佳实践

### 6.1 写好系统提示词

好的系统提示词是智能体质量的关键：

1. **明确角色**：告诉智能体它是谁
2. **设定边界**：说明它能做什么、不能做什么
3. **定义风格**：描述回复的语气和格式
4. **给出示例**：如果有特定的输出格式，给出示例

### 6.2 合理使用工具

- 只启用真正需要的工具（最小权限原则）
- 工具太多会让智能体困惑，降低效率
- 危险操作（文件写入、命令执行）需要审批

### 6.3 记忆管理

- 重要信息明确标记高重要性
- 定期清理无用记忆
- 使用分类和标签组织记忆
- 敏感信息使用高作用域级别

---

## 常见问题

### Q: 智能体回复太慢怎么办？

A: 试试更小的模型（如 qwen2.5:1.5b），或者降低 max_tokens。

### Q: 智能体记不住东西怎么办？

A: 确保 memory.enabled = true，重要内容设置高 importance，也可以手动存储到语义记忆。

### Q: 智能体调用工具出错怎么办？

A: 检查工具是否正确配置，权限是否足够，参数是否正确。查看智能体日志获取详细错误信息。

---

## 下一步

恭喜你完成了第一个智能体的构建！接下来可以：

- [构建研究机器人](build_a_research_bot.md) - 构建一个专业的研究智能体
- [构建代码审查员](build_a_code_reviewer.md) - 让智能体帮你审查代码
- [构建多智能体系统](build_multi_agent_system.md) - 创建一个智能体团队
- [智能体用户指南](../knowledge_base/user-guide/agent_guide.md) - 更详细的智能体使用指南
