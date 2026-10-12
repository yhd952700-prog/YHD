# 构建数据流水线

本教程将带你使用 LiuHao AI OS 的流水线系统构建一个完整的数据处理流水线。

---

## 目标

我们将构建一个数据处理流水线，实现：

1. 从多个数据源读取数据
2. 数据清洗和转换
3. AI 增强（内容生成、摘要、分类）
4. 数据验证和质量检查
5. 输出到多个目标

---

## 前置条件

- LiuHao AI OS 服务运行中
- Python SDK 已安装
- 一些示例数据（CSV/JSON 文件）

---

## 第一步：流水线基础

### 1.1 流水线核心概念

在开始之前，回顾一下流水线的核心组件：

- **Source（数据源）**：数据从哪里来
- **Transform（转换器）**：对数据做什么处理
- **Validate（验证器）**：检查数据质量
- **Sink（输出槽）**：结果输出到哪里

### 1.2 准备示例数据

创建一个示例 CSV 文件 `sales_data.csv`：

```csv
date,product,category,region,sales,quantity,profit
2026-01-15,产品A,电子产品,华东,15000,100,3000
2026-01-16,产品B,服装,华北,8500,250,1700
2026-01-17,产品C,食品,华南,12000,500,2400
2026-01-18,产品A,电子产品,华西,9800,65,1960
2026-01-19,产品B,服装,华东,11200,330,2240
2026-01-20,产品D,家居,华北,6500,80,1300
2026-01-21,产品C,食品,华东,15600,650,3120
2026-01-22,产品A,电子产品,华南,18900,126,3780
2026-01-23,产品E,图书,华西,4200,210,840
2026-01-24,产品B,服装,华南,9600,285,1920
```

---

## 第二步：构建简单流水线

### 2.1 第一个流水线：CSV 数据清洗

创建 `simple_pipeline.py`：

```python
"""
简单数据流水线：读取 CSV → 清洗 → 验证 → 输出 JSON
"""
from liuhao.pipelines import PipelineBuilder

# 构建流水线
pipeline = (
    PipelineBuilder(name="销售数据清洗")
    
    # 1. 数据源：读取 CSV 文件
    .source("file", 
            path="sales_data.csv", 
            format="csv",
            encoding="utf-8")
    
    # 2. 转换：字段映射和类型转换
    .transform("map", mapping={
        "date": "date",
        "product": "product_name",
        "category": "category",
        "region": "region",
        "sales": {"type": "float", "name": "sales_amount"},
        "quantity": {"type": "int", "name": "quantity_sold"},
        "profit": {"type": "float", "name": "profit_amount"}
    })
    
    # 3. 转换：计算利润率
    .transform("compute", {
        "profit_margin": "profit_amount / sales_amount"
    })
    
    # 4. 转换：添加处理时间戳
    .transform("add_field", {
        "processed_at": "{{now}}",
        "data_source": "sales_data.csv"
    })
    
    # 5. 验证：数据质量检查
    .validate("schema", schema={
        "date": {"type": "string", "required": True},
        "product_name": {"type": "string", "required": True},
        "sales_amount": {"type": "float", "min": 0, "required": True},
        "quantity_sold": {"type": "int", "min": 0, "required": True},
        "profit_margin": {"type": "float", "min": 0, "max": 1}
    }, on_error="skip")
    
    # 6. 验证：业务规则检查
    .validate("custom", 
              validator=lambda item: (
                  item["profit_amount"] <= item["sales_amount"],
                  "利润不能大于销售额"
              ),
              on_error="collect")
    
    # 7. 输出：JSON Lines 格式
    .sink("file", 
          path="sales_cleaned.jsonl", 
          format="jsonl")
    
    # 8. 输出：控制台摘要
    .sink("console", format="summary")
    
    .build()
)

# 运行流水线
print("🚀 启动流水线...")
result = pipeline.run()

# 查看结果
print(f"\n📊 流水线执行结果：")
print(f"   状态: {result.status}")
print(f"   处理记录: {result.processed}")
print(f"   成功: {result.succeeded}")
print(f"   失败: {result.failed}")
print(f"   总耗时: {result.duration_seconds:.2f}s")
print(f"   吞吐量: {result.throughput:.1f} 条/秒")

# 查看每步统计
print(f"\n📈 各步骤统计：")
for step in result.step_stats:
    print(f"   {step.name}: 输入 {step.input_count} → 输出 {step.output_count} "
          f"({step.duration_ms}ms)")
```

### 2.2 运行流水线

```bash
python simple_pipeline.py
```

预期输出：

```
🚀 启动流水线...

📊 流水线执行结果：
   状态: completed
   处理记录: 10
   成功: 10
   失败: 0
   总耗时: 0.12s
   吞吐量: 83.3 条/秒
```

---

## 第三步：AI 增强流水线

现在让我们加入 AI 能力，让流水线更智能。

### 3.1 AI 内容生成流水线

创建 `ai_enhanced_pipeline.py`：

```python
"""
AI 增强流水线：产品描述自动生成
"""
from liuhao.pipelines import PipelineBuilder

# 产品数据
products = [
    {"id": "P001", "name": "智能音箱 Pro", "category": "电子产品", 
     "features": "语音控制,蓝牙5.0,智能家居联动", "price": 299},
    {"id": "P002", "name": "轻薄笔记本", "category": "电子产品",
     "features": "14英寸,1.2kg,12小时续航", "price": 5999},
    {"id": "P003", "name": "运动手表", "category": "可穿戴",
     "features": "心率监测,GPS,50米防水", "price": 899},
]

pipeline = (
    PipelineBuilder(name="产品描述生成")
    
    # 数据源：内存中的数据
    .source("memory", data=products)
    
    # AI 转换：生成产品描述
    .transform("ai_transform",
        prompt="""为以下产品生成一段吸引人的营销描述，100字左右。
产品名称：{{name}}
产品类别：{{category}}
主要特点：{{features}}
价格：{{price}}元

要求：
- 突出产品亮点
- 语言生动有感染力
- 适合电商产品页使用
- 不要夸大其词""",
        output_field="description",
        model="qwen2.5:3b",
        batch_size=3
    )
    
    # AI 转换：生成产品卖点标签
    .transform("ai_transform",
        prompt="""为以下产品提取 3-5 个卖点标签，用逗号分隔。
产品名称：{{name}}
产品描述：{{description}}

只返回标签，用逗号分隔，不要其他内容。""",
        output_field="tags",
        model="qwen2.5:3b"
    )
    
    # 验证：AI 输出质量检查
    .validate("custom",
              validator=lambda item: (
                  len(item.get("description", "")) >= 50,
                  "描述太短"
              ),
              on_error="retry")
    
    # 输出：JSON
    .sink("file", path="products_with_desc.json", format="json", pretty=True)
    
    # 输出：控制台
    .sink("console", format="items")
    
    .build()
)

print("🤖 启动 AI 增强流水线...")
result = pipeline.run()

print(f"\n✅ 完成！生成了 {result.succeeded} 条产品描述")
print(f"⏱️  总耗时: {result.duration_seconds:.2f}s")
```

---

## 第四步：复杂流水线模式

### 4.1 分支流水线：按类别分别处理

```python
"""
分支流水线：按产品类别分别处理
"""
from liuhao.pipelines import PipelineBuilder

pipeline = (
    PipelineBuilder(name="分类处理流水线")
    
    .source("file", path="products.csv", format="csv")
    
    # 按类别分支处理
    .branch(
        condition="category == '电子产品'",
        then=PipelineBuilder()
            .transform("map", {"category_name": "电子产品"})
            .transform("add_field", {"tax_rate": 0.13})
            .sink("file", path="electronics.jsonl", format="jsonl"),
        else_branch=PipelineBuilder()
            .branch(
                condition="category == '服装'",
                then=PipelineBuilder()
                    .transform("add_field", {"tax_rate": 0.13})
                    .sink("file", path="clothing.jsonl", format="jsonl"),
                else_branch=PipelineBuilder()
                    .transform("add_field", {"tax_rate": 0.09})
                    .sink("file", path="others.jsonl", format="jsonl")
            )
    )
    
    .build()
)
```

### 4.2 聚合流水线：合并多路数据

```python
"""
聚合流水线：合并多份数据
"""
from liuhao.pipelines import PipelineBuilder

pipeline = (
    PipelineBuilder(name="数据合并流水线")
    
    # 合并多个数据源
    .merge(
        sources=[
            {"type": "file", "path": "sales_jan.csv", "format": "csv"},
            {"type": "file", "path": "sales_feb.csv", "format": "csv"},
            {"type": "file", "path": "sales_mar.csv", "format": "csv"},
        ],
        strategy="concat"  # concat / union / join
    )
    
    # 去重
    .transform("deduplicate", key=["date", "product", "region"])
    
    # 按月聚合
    .transform("aggregate",
        group_by=["month"],
        aggregations=[
            {"field": "sales", "function": "sum", "alias": "total_sales"},
            {"field": "profit", "function": "sum", "alias": "total_profit"},
            {"field": "quantity", "function": "sum", "alias": "total_quantity"},
            {"field": "id", "function": "count", "alias": "record_count"}
        ]
    )
    
    # 计算利润率
    .transform("compute", {"profit_margin": "total_profit / total_sales"})
    
    # 排序
    .transform("sort", by="total_sales", order="desc")
    
    .sink("file", path="monthly_summary.json", format="json", pretty=True)
    
    .build()
)
```

---

## 第五步：错误处理和监控

### 5.1 错误处理策略

```python
from liuhao.pipelines import PipelineBuilder

pipeline = (
    PipelineBuilder(name="错误处理示例")
    
    .source("file", path="input.csv", format="csv")
    
    # 验证失败时收集到错误文件
    .validate("schema", schema={...}, 
              on_error="collect",
              error_sink="file",
              error_path="errors.jsonl")
    
    # 转换失败时跳过
    .transform("map", mapping={...}, on_error="skip")
    
    .sink("file", path="output.jsonl", format="jsonl")
    
    # 全局错误处理
    .error_handling({
        "max_errors": 100,          # 最多允许的错误数
        "error_threshold": 0.1,     # 错误率超过 10% 则停止
        "on_max_errors": "stop",    # 达到上限时：stop / continue
        "dead_letter_queue": True   # 死信队列
    })
    
    .build()
)
```

### 5.2 流水线监控

```python
result = pipeline.run()

# 详细的执行统计
print(f"总记录数: {result.total}")
print(f"成功: {result.succeeded}")
print(f"失败: {result.failed}")
print(f"跳过: {result.skipped}")
print(f"成功率: {result.success_rate:.1%}")

# 各步骤详情
for step in result.step_stats:
    print(f"\n步骤: {step.name}")
    print(f"  输入: {step.input_count}")
    print(f"  输出: {step.output_count}")
    print(f"  错误: {step.error_count}")
    print(f"  耗时: {step.duration_ms}ms")
    print(f"  吞吐量: {step.throughput:.1f}/s")

# 错误详情
if result.errors:
    print("\n错误详情:")
    for error in result.errors[:5]:
        print(f"  [{error.step}] {error.message}")
        print(f"    数据: {str(error.record)[:100]}")
```

---

## 第六步：流水线最佳实践

### 6.1 性能优化

1. **批量处理**：AI 转换等操作使用批量模式
2. **流式处理**：大数据量使用流式处理，不要一次性加载
3. **并行执行**：独立步骤并行处理
4. **缓存中间结果**：复杂流水线缓存中间步骤

### 6.2 可观测性

1. **记录每步统计**：监控每个步骤的输入输出
2. **收集错误数据**：错误数据保存到死信队列，便于排查
3. **设置告警**：错误率过高时触发告警
4. **记录处理时间**：监控性能变化

### 6.3 数据质量

1. **尽早验证**：在流水线前端就验证数据质量
2. **多层验证**：模式验证 + 业务规则验证 + AI 质量评估
3. **数据血缘**：记录数据的来源和处理历史
4. **抽样检查**：定期人工抽样检查输出质量

### 6.4 安全考虑

1. **敏感数据脱敏**：处理前先脱敏
2. **访问控制**：数据源和输出的权限控制
3. **数据最小化**：只处理需要的数据
4. **审计日志**：记录所有数据操作

---

## 下一步

- [流水线用户指南](../knowledge_base/user-guide/pipeline_guide.md) - 更详细的流水线使用指南
- [构建多智能体系统](build_multi_agent_system.md) - 智能体与流水线结合
- [部署到生产环境](deploy_to_production.md) - 生产环境部署
