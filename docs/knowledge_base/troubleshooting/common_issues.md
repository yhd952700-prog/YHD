# 常见问题与解决方案

本文档汇总了 LiuHao AI OS 使用过程中常见的问题及其解决方案。

---

## 安装问题

### 问题：pip install 失败，提示依赖冲突

**症状**：安装依赖时报错 `ERROR: ResolutionImpossible` 或类似的依赖冲突错误。

**解决方案**：

```bash
# 1. 升级 pip
pip install --upgrade pip

# 2. 尝试使用 --no-deps 安装，再手动安装依赖
pip install liuhao-ai-os --no-deps
pip install -r requirements.txt

# 3. 使用国内镜像源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4. Windows 上如遇编译错误，安装 pre-built wheel
# 访问 https://www.lfd.uci.edu/~gohlke/pythonlibs/ 下载对应 whl 文件
pip install package_name.whl
```

### 问题：Python 版本不兼容

**症状**：报错 `Python version 3.10 is not supported`。

**解决方案**：

LiuHao AI OS 需要 Python 3.11 或更高版本。

```bash
# 检查当前 Python 版本
python --version

# Windows: 下载安装 Python 3.11+
# https://www.python.org/downloads/

# 使用 py 启动器指定版本
py -3.11 -m venv .venv
```

### 问题：Node.js 依赖安装失败

**症状**：`npm install` 报错或速度很慢。

**解决方案**：

```bash
# 使用国内镜像
npm config set registry https://registry.npmmirror.com

# 或使用 pnpm
npm install -g pnpm
pnpm install
```

---

## 连接问题

### 问题：无法连接到 Ollama

**症状**：启动时报错 `Connection refused` 或 `Failed to connect to Ollama`。

**排查步骤**：

1. **确认 Ollama 正在运行**

```bash
# Windows
ollama serve

# 或检查服务状态
curl http://localhost:11434/api/tags
```

2. **确认模型已下载**

```bash
ollama list

# 如缺少模型，下载默认模型
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

3. **检查配置**

确认 `OLLAMA_BASE_URL` 环境变量设置正确：

```bash
# Windows
set OLLAMA_BASE_URL=http://localhost:11434

# PowerShell
$env:OLLAMA_BASE_URL = "http://localhost:11434"
```

### 问题：数据库连接失败

**症状**：报错 `Database connection failed` 或 `OperationalError`。

**解决方案**：

**SQLite 模式**：

```bash
# 确认数据库文件路径可写
# 默认: ./liuhao_ai_os.db

# 检查目录权限
ls -la liuhao_ai_os.db
```

**PostgreSQL 模式**：

```bash
# 1. 确认 PostgreSQL 服务正在运行
# Windows: 服务管理器中查看
# Linux: systemctl status postgresql

# 2. 测试连接
psql -h localhost -U username -d database_name

# 3. 检查 DATABASE_URL 格式
# postgresql://user:password@host:port/dbname
```

### 问题：API 返回 401 Unauthorized

**症状**：调用 API 时返回 401 错误。

**解决方案**：

1. 确认 API Key 是否正确
2. 检查请求头是否包含 `Authorization: Bearer <api_key>`
3. 确认 API Key 没有过期或被撤销
4. 如果是本地开发且未启用认证，确认 `AUTH_ENABLED=false`

### 问题：CORS 错误

**症状**：前端调用 API 时报 CORS 错误。

**解决方案**：

配置允许的跨域源：

```bash
# 环境变量
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

---

## 性能问题

### 问题：智能体回复很慢

**症状**：发送消息后等待很久才有回复。

**可能原因和解决方案**：

| 原因 | 解决方案 |
|------|---------|
| 模型太大 | 换用更小的模型（qwen2.5:1.5b / qwen2.5:3b） |
| CPU 推理慢 | 使用 GPU 加速推理 |
| 上下文太长 | 减少上下文窗口大小 |
| 工具调用太多 | 优化提示词，减少不必要的工具调用 |
| 网络延迟 | 使用本地模型或更近的 API 端点 |
| 并发请求太多 | 增加资源，或减少并发 |

### 问题：内存占用过高

**症状**：系统内存占用持续增长，甚至 OOM（Out of Memory）。

**解决方案**：

1. **限制工作记忆大小**：减少工作记忆容量
2. **定期清理记忆**：运行记忆清理任务
3. **降低模型上下文长度**：减少 max_tokens
4. **使用更小的模型**：小模型占用更少内存
5. **检查内存泄漏**：确认是否有未释放的资源

```bash
# 手动触发记忆清理
liuhao memory cleanup --older-than 30d --min-importance 0.3
```

### 问题：搜索速度慢

**症状**：记忆搜索或知识库搜索响应很慢。

**解决方案**：

1. **优化向量索引**：重建向量索引
2. **减少搜索范围**：指定类型和分类
3. **调整搜索精度**：降低 `search_effort` 参数
4. **使用更好的硬件**：GPU 加速向量计算

---

## 记忆问题

### 问题：智能体记不住事情

**症状**：之前告诉智能体的信息，下次对话就忘了。

**可能原因**：

1. **记忆没有持久化**：信息只存在工作记忆中，会话结束就丢失
2. **重要性太低**：记忆重要性评分低，很快被衰减
3. **搜索没搜到**：语义相似度不够，没有检索到
4. **作用域不匹配**：记忆的作用域级别高于当前请求

**解决方案**：

```python
# 1. 明确存储重要信息，并设置高重要性
client.memory.store(
    content="重要信息内容",
    memory_type="semantic",
    importance=0.9,  # 设置高重要性
    tags=["重要", "用户偏好"]
)

# 2. 降低搜索阈值，找到更多结果
results = client.memory.search(
    query="你的查询",
    threshold=0.5  # 降低阈值
)
```

### 问题：记忆内容有错误

**症状**：智能体记住了错误的信息。

**解决方案**：

```python
# 1. 更新错误的记忆
client.memory.update(
    memory_id="mem-abc123",
    content="正确的内容",
    metadata={"confidence": 0.95}
)

# 2. 删除错误记忆
client.memory.delete(memory_id="mem-abc123")

# 3. 添加纠正信息（置信度更高）
client.memory.store(
    content="纠正后的正确信息",
    memory_type="semantic",
    importance=0.9,
    metadata={"confidence": 0.99, "corrects": "mem-abc123"}
)
```

### 问题：记忆占用空间太大

**症状**：记忆数据库文件持续增大。

**解决方案**：

1. **运行清理**：删除过期和低价值记忆
2. **压缩情景记忆**：将旧的情景记忆提取要点转为语义记忆
3. **调整保留策略**：缩短记忆保留时间
4. **归档历史数据**：将不常用的记忆归档

```bash
# 清理 90 天前的低重要性记忆
liuhao memory cleanup --older-than 90d --min-importance 0.3

# 压缩 30 天前的情景记忆
liuhao memory compress --type episodic --older-than 30d
```

---

## API 错误

### 问题：429 Too Many Requests

**症状**：API 返回 429 状态码，提示触发速率限制。

**解决方案**：

1. **降低请求频率**：在客户端实现请求间隔
2. **实现重试**：收到 429 后，按 `Retry-After` 头等待后重试
3. **批量操作**：使用批量接口减少请求次数
4. **升级配额**：如果需要更高的速率限制，联系管理员

```python
# Python 示例：指数退避重试
import time
import random

def request_with_retry(func, max_retries=3):
    for i in range(max_retries):
        try:
            return func()
        except RateLimitError as e:
            if i == max_retries - 1:
                raise
            wait_time = (2 ** i) + random.uniform(0, 1)
            time.sleep(wait_time)
```

### 问题：500 Internal Server Error

**症状**：API 返回 500 错误。

**排查步骤**：

1. **查看服务日志**：检查服务端的错误日志和堆栈信息
2. **检查系统资源**：确认 CPU、内存、磁盘是否充足
3. **检查数据库**：确认数据库连接正常
4. **简化请求**：逐步简化请求参数，定位触发错误的字段
5. **提交 Issue**：如果是系统 bug，提交 Issue 并附上请求详情和错误日志

### 问题：请求超时

**症状**：请求长时间无响应，最终超时。

**解决方案**：

1. **增加超时时间**：客户端设置更长的超时
2. **使用异步模式**：启动任务后轮询状态，而不是同步等待
3. **使用流式输出**：对于长生成任务，使用流式接口
4. **优化任务**：将大任务拆分为小任务

```python
# 使用异步任务而不是同步等待
task = client.agents.execute_async(agent_id, task)
# 然后轮询任务状态
```

---

## 智能体错误

### 问题：智能体无法调用工具

**症状**：智能体应该调用工具但没有调用，或工具调用失败。

**可能原因**：

1. 工具没有正确配置
2. 智能体没有权限使用该工具
3. 工具参数不正确
4. 工具执行出错

**排查步骤**：

```python
# 1. 检查智能体可用工具
tools = client.agents.get_tools(agent_id)
print([t.name for t in tools])

# 2. 手动测试工具
result = client.tools.call("tool_name", {"param": "value"})
print(result)

# 3. 查看智能体执行日志
logs = client.agents.get_logs(agent_id, level="DEBUG")
```

### 问题：智能体陷入死循环

**症状**：智能体反复调用同一个工具，无法推进任务。

**解决方案**：

1. **设置最大步数**：限制智能体的最大执行步数
2. **优化系统提示词**：明确任务目标和约束
3. **增加反思步骤**：让智能体定期反思是否在正确推进
4. **人工干预**：暂停任务，人工介入调整

```python
# 设置最大步数
result = client.agents.execute(
    agent_id,
    task=task_description,
    options={"max_steps": 20}
)
```

### 问题：智能体输出质量差

**症状**：智能体的回复质量不高，或不符合预期。

**优化建议**：

1. **优化系统提示词**：更清晰地定义角色和输出格式
2. **调整温度参数**：
   - 低温度（0.1-0.3）：更确定、更聚焦
   - 高温度（0.7-1.0）：更有创意、更多样
3. **提供示例**：在提示词中给出输出示例
4. **使用更强的模型**：复杂任务使用更大的模型
5. **增加上下文**：提供更多相关信息和背景

---

## 下一步

- [FAQ](faq.md) - 常见问题解答
- [安装指南](../getting-started/installation.md#常见问题排查) - 安装相关问题
- [联系支持](#) - 获取更多帮助
