# 快速开始指南

## 10分钟上手 LiuHao AI OS

### 前置条件

- Python 3.11+
- LiuHao AI OS 已安装
- D:\\LiuHao-AI-OS 目录权限

### 安装步骤

```bash
# 1. 克隆项目
git clone <repository-url>
cd LiuHao-AI-OS

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 验证安装
python -c "from src.audit import AuditEvent; print('Audit module OK')"
python -c "from src.observability import Span; print('Observability module OK')"
python -c "from src.performance import create_lru_cache; print('Performance module OK')"
python -c "from src.integrations import Base, init_models; print('Integration module OK')"
python -c "from src.plugins.marketplace import Plugin, get_marketplace_store; print('Plugin marketplace OK')"
```

### 基本使用

```python
from src.audit import auth_event, security_violation_event, AuditStore

# 创建审计事件
auth_ok = auth_event("authentication", "user1", True, source="auth_module")
auth_fail = auth_event("authentication", "user1", False, source="auth_module")
security = security_violation_event("authentication", "user1", "Failed login attempt")

# 存储事件
store = AuditStore()
eid1 = store.emit(auth_ok)
eid2 = store.emit(auth_fail)
eid3 = store.emit(security)

# 查看统计
stats = store.get_stats()
print(f"总事件数: {stats['total']}")
print(f"完整性验证: {store.verify_integrity()}")
```

### 第一个插件

```python
from src.plugins.marketplace import Plugin, PluginMetadata, PluginVersion, get_marketplace_store
from src.plugins.sandbox import SandboxExecutionContext, ResourceLimits

# 创建插件元数据
metadata = PluginMetadata(
    name="my-plugin",
    version="1.0.0",
    description="我的第一个插件",
    plugin_type="extension",
    author="LiuHao",
    tags=["demo", "example"]
)

# 创建版本信息
version = PluginVersion(
    version="1.0.0",
    release_notes="初始发布",
    changelog="无",
    upload_url="",
    status="approved"
)

# 创建插件实例
plugin = Plugin(
    plugin_id="my-plugin-001",
    name="my-plugin",
    description="我的第一个插件",
    metadata=metadata,
    current_version=version,
    status="published",
    is_active=True
)

# 注册插件
store = get_marketplace_store()
eid = store.register(plugin)
print(f"插件注册成功: {eid}")

# 创建沙箱执行上下文
context = SandboxExecutionContext(
    execution_id="exec-001",
    plugin_id="my-plugin-001",
    sandbox_id="sandbox-001",
    entry_point="main",
    arguments=["--arg1", "value1"],
    working_directory="/tmp",
    resource_limits=ResourceLimits(
        memory_limit=1024 * 1024,  # 1MB
        execution_time_limit=30,  # 30秒
        network_access=False,
        file_system_access=False
    ),
    timeout=60
)

# 存储上下文
eid2 = store.create_context(context)
print(f"沙箱上下文创建: {eid2}")
```

### 性能基准测试

```python
from src.performance import create_lru_cache, LRUCache

# 创建缓存
cache = create_lru_cache(max_size=100)

# 添加测试数据
for i in range(100):
    cache.put(f"key_{i}", f"value_{i}")

# 测试命中率
hits = 0
misses = 0
for i in range(100):
    if cache.get(f"key_{i}") is not None:
        hits += 1
    else:
        misses += 1

stats = cache.get_stats()
print(f"命中率: {stats.hit_rate:.2%}")
print(f"总请求: {stats.total_requests}")
print(f"缓存大小: {stats.current_size}")
```

### 外部系统集成

```python
from src.integrations import init_models, Base, create_storage_config, create_function_config, create_queue_config

# 创建存储配置
storage_cfg = create_storage_config(
    provider="s3",
    bucket="liuhao-bucket",
    region="ap-north-1",
    access_key="AKID...",
    secret_key="secret..."
)

# 创建函数配置
function_cfg = create_function_config(
    provider="aws",
    function_name="process-data",
    runtime="python3.11",
    handler="handler.main",
    timeout=60,
    memory=256,
    environment={"MODE": "production"}
)

# 创建队列配置
queue_cfg = create_queue_config(
    provider="sqs",
    queue-name="task-queue",
    region="ap-north-1",
    access_key="AKID...",
    secret_key="secret..."
)

print("配置创建成功")
```

### 项目结构

```
LiuHao-AI-OS/
├── src/
│   ├── audit/          # 安全审计
│   ├── observability/  # 观测性
│   ├── performance/    # 性能优化
│   ├── integrations/   # 外部系统集成
│   └── plugins/        # 插件生态
│       ├── marketplace/ # 插件商店
│       ├── sandbox/     # 沙箱运行时
│       └── dependencies/ # 依赖管理
└── docs/
    ├── tutorials/      # 教程
    ├── scenarios/       # 场景案例
    └── best-practices/ # 最佳实践
```

### 进阶技巧

1. **多级缓存**: 结合内存缓存和 Redis 缓存，提升访问速度
2. **插件热重载**: 使用沙箱隔离，支持热插拔而不重启系统
3. **审计链路**: 结合 Trace-ID，实现跨服务完整审计追踪
4. **性能监控**: 使用观测性模块监控关键指标和告警

### 常见问题

| 问题 | 解决方案 |
|------|----------|
| 审计事件未持久化 | 检查 `data/audit/events.json` 是否存在且可写 |
| 插件注册失败 | 检查插件元数据是否完整，版本是否冲突 |
| 沙箱超时 | 调整 `ResourceLimits.execution_time_limit` 或优化代码 |
| 缓存命中率低 | 调整 `max_size` 或�查 key 是否一致 |
| ORM 连接失败 | 检查数据库连接串和权限 |

### 升级指南

- **从 v0.x 升级**: 运行 `python scripts/migrate.py` 自动迁移数据库 schema
- **插件版本升级**: 在 marketplace 使用 `store.add_version(plugin_id, new_version)`
- **配置变更**: 修改 `config.yaml` 并重启服务

### 许可证

MIT License - 免费用于个人和商业用途