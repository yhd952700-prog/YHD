# LiuHao AI OS - Ruby SDK

官方 LiuHao AI OS 平台 Ruby SDK。

## 特性

- Faraday HTTP 客户端
- Ruby 惯用法的 API (blocks, keyword arguments)
- 指数退避重试机制
- 完整的错误类层次结构
- 支持 Agents、Memory、Knowledge API
- Ruby 2.7+ 兼容

## 安装

在 Gemfile 中添加：

```ruby
gem 'liuhao-sdk'
```

然后执行：

```bash
bundle install
```

或者直接安装：

```bash
gem install liuhao-sdk
```

## 快速开始

```ruby
require 'liuhao/sdk'

# 创建客户端
client = LiuHao::SDK::Client.new(api_key: 'your-api-key')

# 列出所有 Agent
agents = client.agents.list
agents.each do |agent|
  puts "#{agent['name']} - #{agent['status']}"
end

# 创建 Agent
agent = client.agents.create(name: 'my-agent', model_name: 'gpt-4')
puts "Created agent: #{agent['id']}"

# 执行 Agent
result = client.agents.execute(agent['id'], 'Hello, how are you?')
puts result
```

## 配置选项

```ruby
client = LiuHao::SDK::Client.new(
  api_key: 'your-api-key',
  base_url: 'https://api.liuhao.ai/v1',
  timeout: 30,
  max_retries: 3,
  initial_retry_delay: 1.0,  # seconds
  backoff_multiplier: 2.0,
  user_agent: 'MyApp/1.0',
  logger: Logger.new(STDOUT)
)
```

或者使用模块级别的便捷方法：

```ruby
client = LiuHao::SDK.new('your-api-key', base_url: 'https://api.liuhao.ai/v1')
```

## Memory API

```ruby
# 存储记忆
memory = client.memory.store(
  content: 'User prefers dark mode',
  memory_type: 'preference',
  category: 'ui',
  importance: 0.8
)

# 使用 block 存储记忆
memory = client.memory.store_with do |attrs|
  attrs[:content] = 'User prefers dark mode'
  attrs[:memory_type] = 'preference'
  attrs[:category] = 'ui'
end

# 搜索记忆
results = client.memory.search('dark mode')
puts "Found #{results.length} memories"

# 列出记忆
all_memories = client.memory.list

# 列出分类
categories = client.memory.categories
```

## Knowledge API

```ruby
# 创建知识库
kb = client.knowledge.create_base(
  name: 'my-docs',
  description: 'My documentation knowledge base'
)

# 使用 block 创建知识库
kb = client.knowledge.create_base_with do |attrs|
  attrs[:name] = 'my-docs'
  attrs[:description] = 'My documentation knowledge base'
  attrs[:tags] = %w[docs api]
end

# 上传文档
client.knowledge.upload_document(
  kb['id'],
  title: 'README.md',
  content: '# My Project\n...'
)

# 搜索知识库
results = client.knowledge.search(kb['id'], 'how to install', top_k: 10)
results.each do |result|
  puts "Score: #{result['score']}, Content: #{result['content']}"
end
```

## 异常处理

SDK 提供完整的错误类层次结构：

- `LiuHaoError` - 基类错误
- `AuthenticationError` - 认证失败 (401)
- `NotFoundError` - 资源不存在 (404)
- `ValidationError` - 请求验证失败 (400)
- `RateLimitError` - 速率限制 (429)
- `APIError` - 服务端错误 (5xx)
- `TimeoutError` - 请求超时
- `ConfigurationError` - 配置错误
- `NetworkError` - 网络错误

```ruby
begin
  agent = client.agents.get('nonexistent-id')
rescue LiuHao::SDK::NotFoundError => e
  puts "Agent not found: #{e.message}"
rescue LiuHao::SDK::RateLimitError => e
  puts "Rate limited, retry after #{e.retry_after_seconds}s"
rescue LiuHao::SDK::LiuHaoError => e
  puts "API error: #{e.message}"
  puts "Status: #{e.status_code}"
  puts "Request ID: #{e.request_id}"
end
```

## 重试策略

SDK 默认对以下情况进行重试（最多 3 次）：

- 网络错误（超时、连接失败）
- 速率限制 (429)
- 服务端错误 (502, 503, 504)

重试采用指数退避策略，初始延迟 1 秒，每次重试延迟翻倍。

可以使用 `retryable?` 方法检查错误是否可重试：

```ruby
begin
  client.agents.list
rescue LiuHao::SDK::LiuHaoError => e
  if e.retryable?
    # 自定义重试逻辑
  end
end
```

## 许可证

详见项目根目录的 LICENSE 文件。
