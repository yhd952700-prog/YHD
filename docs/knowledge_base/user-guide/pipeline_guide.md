# 流水线用户指南

流水线（Pipeline）是 LiuHao AI OS 中用于数据处理的核心组件，支持从数据源读取、转换、验证到输出的完整数据流处理。本指南介绍如何构建和使用流水线。

---

## 流水线概念

### 什么是流水线

流水线是一系列数据处理步骤的有序组合，数据从源头进入，经过一系列变换和验证，最终输出到目标位置。

### 流水线 vs 智能体

| 维度 | 流水线 | 智能体 |
|------|--------|--------|
| 处理对象 | 结构化数据流 | 开放式任务 |
| 执行方式 | 预定义步骤，顺序/并行 | 动态决策，自主推理 |
| 灵活性 | 配置驱动，中等灵活 | 高自主性，灵活调整 |
| 适用场景 | ETL、数据处理、批量转换 | 复杂任务、分析、创作 |
| 可预测性 | 高，结果可预期 | 低，依赖输入和推理 |

### 流水线核心组件

```
Source → Transform → Validate → Sink
  (源)    (转换)      (验证)     (输出槽)
```

| 组件 | 作用 | 示例 |
|------|------|------|
| Source | 数据源，从各种来源读取数据 | 文件、数据库、API、消息队列 |
| Transform | 转换器，处理和转换数据 | 过滤、映射、聚合、增强 |
| Validate | 验证器，检查数据质量 | 模式验证、范围检查、格式校验 |
| Sink | 输出槽，写入目标位置 | 文件、数据库、API、标准输出 |
| Aggregator | 聚合器，合并多路数据 | 并集、连接、合并 |

---

## 构建流水线

### 使用构建器（Builder）模式

```python
from liuhao.pipelines import PipelineBuilder

pipeline = (
    PipelineBuilder(name="数据处理流水线")
    .source("file", path="data/input.csv", format="csv")
    .transform("filter", condition="age > 18")
    .transform("map", mapping={"name": "full_name", "age": "user_age"})
    .validate("schema", schema={"full_name": "string", "user_age": "int"})
    .sink("file", path="data/output.json", format="json")
    .build()
)

# 运行流水线
result = pipeline.run()
print(f"处理完成: {result.processed} 条，失败: {result.failed} 条")
```

### 使用 YAML 配置

```yaml
# pipeline.yaml
name: 日志分析流水线
description: 分析服务器日志并生成报告

sources:
  - id: log_source
    type: file
    config:
      path: /var/log/server.log
      format: text
      pattern: '^(?P<time>\S+) (?P<level>\S+) (?P<message>.*)$'

transforms:
  - id: filter_errors
    type: filter
    config:
      condition: "level == 'ERROR'"
  
  - id: count_by_hour
    type: aggregate
    config:
      group_by: "time.hour"
      aggregations:
        - field: message
          function: count
          alias: error_count

validations:
  - id: check_count
    type: min_value
    config:
      field: error_count
      min: 0

sinks:
  - id: report_output
    type: file
    config:
      path: reports/error_report.json
      format: json
```

```python
# 从 YAML 加载流水线
from liuhao.pipelines import Pipeline

pipeline = Pipeline.from_yaml("pipeline.yaml")
result = pipeline.run()
```

### 使用 CLI

```bash
# 从配置文件运行流水线
liuhao pipeline run pipeline.yaml

# 查看运行状态
liuhao pipeline status <run_id>

# 列出流水线
liuhao pipeline list
```

---

## 数据源（Sources）

### 内置数据源

| 数据源 | 类型标识 | 说明 |
|--------|---------|------|
| 文件 | `file` | 读取本地文件（CSV/JSON/TXT/Markdown 等） |
| 数据库 | `database` | 从 SQL 数据库读取 |
| HTTP API | `http` | 从 REST API 获取数据 |
| 消息队列 | `mqtt` / `kafka` | 从消息队列消费数据 |
| 目录 | `directory` | 批量读取目录下的文件 |
| 内存 | `memory` | 从内存数据结构读取 |

### 文件源示例

```python
# CSV 文件
builder.source("file", path="data.csv", format="csv", delimiter=",")

# JSON Lines
builder.source("file", path="data.jsonl", format="jsonl")

# 正则解析文本
builder.source("file", path="log.txt", format="text", 
               pattern=r'^(\S+) (\S+) (.*)$',
               capture_groups=["time", "level", "message"])

# 通配符批量文件
builder.source("file", path="data/*.csv", format="csv")
```

### 数据库源示例

```python
builder.source("database",
    connection_string="postgresql://user:pass@localhost/db",
    query="SELECT * FROM users WHERE created_at > '2026-01-01'",
    batch_size=1000
)
```

### HTTP API 源示例

```python
builder.source("http",
    url="https://api.example.com/data",
    method="GET",
    headers={"Authorization": "Bearer xxx"},
    pagination={
        "type": "page",
        "param": "page",
        "max_pages": 10
    }
)
```

---

## 转换器（Transforms）

### 内置转换器

| 转换器 | 类型标识 | 说明 |
|--------|---------|------|
| 过滤 | `filter` | 按条件过滤数据 |
| 映射 | `map` | 字段映射和重命名 |
| 增强 | `enrich` | 添加新字段（AI 生成、API 调用等） |
| 聚合 | `aggregate` | 分组聚合计算 |
| 排序 | `sort` | 按字段排序 |
| 去重 | `deduplicate` | 去除重复数据 |
| 拆分 | `split` | 将一条数据拆分为多条 |
| 合并 | `join` | 合并两个数据源 |
| AI 转换 | `ai_transform` | 使用 LLM 进行智能转换 |

### 过滤

```python
# 简单条件
builder.transform("filter", condition="age > 18 and status == 'active'")

# 复杂表达式
builder.transform("filter", condition="len(tags) > 0 and score >= 0.8")
```

### 映射

```python
builder.transform("map", mapping={
    "old_name": "new_name",          # 重命名字段
    "first_name": "name.first",      # 嵌套字段展开
    "computed": "field1 + field2",   # 计算字段
    "constant": "固定值"             # 常量字段
})
```

### AI 增强

```python
builder.transform("ai_transform",
    prompt="为以下内容生成摘要，不超过100字：{{content}}",
    output_field="summary",
    model="qwen2.5:3b",
    batch_size=10
)
```

### 聚合

```python
builder.transform("aggregate",
    group_by=["category", "region"],
    aggregations=[
        {"field": "sales", "function": "sum", "alias": "total_sales"},
        {"field": "sales", "function": "avg", "alias": "avg_sales"},
        {"field": "id", "function": "count", "alias": "count"}
    ]
)
```

---

## 验证器（Validators）

### 内置验证器

| 验证器 | 类型标识 | 说明 |
|--------|---------|------|
| 模式验证 | `schema` | 验证字段类型和结构 |
| 范围验证 | `range` | 验证数值范围 |
| 格式验证 | `format` | 验证格式（邮箱、URL、日期等） |
| 非空验证 | `required` | 验证必填字段 |
| 自定义 | `custom` | 自定义验证逻辑 |
| AI 验证 | `ai_validate` | 使用 LLM 进行质量评估 |

### 模式验证

```python
builder.validate("schema", schema={
    "name": {"type": "string", "required": True},
    "age": {"type": "integer", "min": 0, "max": 150},
    "email": {"type": "string", "format": "email"},
    "tags": {"type": "list", "items": {"type": "string"}}
})
```

### 自定义验证

```python
def validate_data(item):
    """自定义验证函数"""
    if item["start_date"] > item["end_date"]:
        return False, "开始日期不能晚于结束日期"
    return True, None

builder.validate("custom", validator=validate_data)
```

### 验证失败处理

```python
builder.validate("schema", schema={...}, 
    on_error="skip",        # skip / abort / collect
    error_sink="file",      # 错误数据输出位置
    error_path="errors.jsonl"
)
```

---

## 输出槽（Sinks）

### 内置输出槽

| 输出槽 | 类型标识 | 说明 |
|--------|---------|------|
| 文件 | `file` | 写入本地文件 |
| 数据库 | `database` | 写入 SQL 数据库 |
| HTTP API | `http` | 发送到 REST API |
| 控制台 | `console` | 输出到标准输出 |
| 消息队列 | `kafka` / `mqtt` | 发送到消息队列 |

### 文件输出

```python
# JSON 格式
builder.sink("file", path="output.json", format="json", pretty=True)

# CSV 格式
builder.sink("file", path="output.csv", format="csv", delimiter=",")

# JSON Lines（适合大数据量）
builder.sink("file", path="output.jsonl", format="jsonl")
```

### 数据库输出

```python
builder.sink("database",
    connection_string="postgresql://user:pass@localhost/db",
    table="output_table",
    mode="append",  # append / overwrite / upsert
    batch_size=500
)
```

### 多输出槽

流水线可以有多个输出槽：

```python
builder = (
    PipelineBuilder()
    .source(...)
    .transform(...)
    .sink("file", path="results.json")        # 主输出
    .sink("database", table="results")        # 数据库输出
    .sink("console", format="summary")        # 控制台摘要
)
```

---

## 流水线模式

### 模式一：简单线性流水线

```
Source → Transform1 → Transform2 → Validate → Sink
```

适合简单的数据处理任务。

### 模式二：分支流水线

```
           ┌→ TransformA → SinkA
Source ────┤
           └→ TransformB → SinkB
```

```python
pipeline = (
    PipelineBuilder()
    .source("file", path="input.csv")
    .branch(
        condition="category == 'A'",
        then=PipelineBuilder()
            .transform("map", mapping={"value": "value * 1.1"})
            .sink("file", path="category_a.jsonl"),
        else_=PipelineBuilder()
            .transform("map", mapping={"value": "value * 0.9"})
            .sink("file", path="category_others.jsonl")
    )
    .build()
)
```

### 模式三：合并流水线

```
Source A ──┐
            ├→ Merge → Transform → Sink
Source B ──┘
```

```python
pipeline = (
    PipelineBuilder()
    .merge(
        sources=[
            {"type": "file", "path": "data1.csv", "format": "csv"},
            {"type": "file", "path": "data2.csv", "format": "csv"}
        ],
        strategy="union"  # union / join / concat
    )
    .transform("deduplicate", key="id")
    .sink("file", path="merged.jsonl")
    .build()
)
```

### 模式四：循环流水线

对每条数据执行多次处理（如迭代优化）。

```python
pipeline = (
    PipelineBuilder()
    .source("file", path="drafts.txt")
    .loop(
        body=PipelineBuilder()
            .transform("ai_transform", prompt="改进这段文字...",
                        output_field="improved_text"),
        max_iterations=3,
        stop_condition="quality_score >= 0.9"
    )
    .sink("file", path="final_drafts.jsonl")
    .build()
)
```

---

## 流水线最佳实践

### 1. 模块化设计

- 每个步骤职责单一
- 可复用的步骤封装为组件
- 复杂流水线拆分为子流水线

### 2. 错误处理

- 为验证失败设置合理的处理策略
- 关键数据使用 `collect` 模式保存错误记录
- 设置监控和告警

### 3. 性能优化

- 大数据量使用流式处理（不要一次性加载全部数据）
- 合理设置批处理大小
- 可并行的步骤尽量并行

### 4. 可观测性

- 记录处理计数和耗时
- 保存错误日志
- 设置关键指标监控

### 5. 安全考虑

- 数据源凭证使用环境变量或密钥管理
- 输出数据注意脱敏
- 遵循数据最小化原则

---

## 调试流水线

### 查看运行结果

```python
result = pipeline.run()

print(f"状态: {result.status}")
print(f"处理记录数: {result.processed}")
print(f"失败记录数: {result.failed}")
print(f"总耗时: {result.duration_seconds:.2f}s")
print(f"吞吐量: {result.throughput:.1f} 条/秒")

# 查看每个步骤的统计
for step in result.step_stats:
    print(f"  {step.name}: 输入 {step.input_count}, 输出 {step.output_count}, "
          f"耗时 {step.duration_ms}ms")
```

### 预览模式

```python
# 只处理前 N 条数据，快速验证流水线
result = pipeline.run(preview=True, preview_count=10)

# 查看每条数据的变化轨迹
for trace in result.data_traces:
    print(f"记录 {trace.id}:")
    for step in trace.steps:
        print(f"  [{step.name}] {step.action}")
```

### 常见问题排查

| 问题 | 可能原因 | 排查方法 |
|------|---------|---------|
| 输出为空 | 过滤条件太严格 | 逐步注释 filter 查看 |
| 速度太慢 | 数据量太大或 AI 调用多 | 增加批大小、减少 AI 调用 |
| 内存溢出 | 一次性加载太多数据 | 使用流式处理，减小批次 |
| 数据错误 | 转换逻辑有 bug | 使用预览模式逐步排查 |

---

## 下一步

- [构建数据流水线教程](../../tutorials/build_a_data_pipeline.md) - 完整实战教程
- [API 参考](../api-reference/tasks_api.md) - 流水线任务 API
- [智能体用户指南](agent_guide.md) - 智能体如何调用流水线
