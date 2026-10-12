# 内核架构

LiuHao AI OS 的核心是 14 个内核（Kernel），它们构成了系统的基础能力层。本文详细介绍每个内核的职责、接口和相互关系。

---

## 内核分类

14 个内核分为三大类：

```
┌─────────────────────────────────────────────────────────┐
│                     LiuHao Kernel                       │
├──────────────────┬──────────────────┬───────────────────┤
│  智能内核        │  代理内核        │  主权内核         │
│  Intelligence    │  Agency          │  Sovereignty      │
│  (6 个)          │  (5 个)          │  (3 个)           │
│                  │                  │                   │
│  identity       │  resource        │  security        │
│  memory         │  event           │  audit           │
│  context        │  network         │  plugin          │
│  capability     │  trust           │                   │
│  policy         │  evaluation      │                   │
│  execution      │                  │                   │
└──────────────────┴──────────────────┴───────────────────┘
```

---

## 智能内核（Intelligence Kernels）

智能内核负责 AI 的核心认知能力：感知、记忆、理解、决策。

### 1. Identity Kernel（身份内核）

**职责**：管理智能体和用户的身份、权限和作用域。

**核心功能**：

- 身份注册和验证
- 角色和权限管理
- L0-L7 作用域级别过滤
- 身份链完整性验证
- 人类身份注册和管理

**关键接口**：

```python
class IdentityKernel:
    def register_identity(self, identity: Identity) -> Identity:
        """注册新身份"""
    
    def verify_identity(self, identity_id: str, credentials: dict) -> bool:
        """验证身份凭证"""
    
    def check_scope(self, identity_id: str, required_scope: ScopeLevel) -> bool:
        """检查身份是否满足指定作用域级别"""
    
    def get_roles(self, identity_id: str) -> List[Role]:
        """获取身份的角色列表"""
    
    def has_permission(self, identity_id: str, permission: str, 
                       resource: str) -> bool:
        """检查是否拥有某项权限"""
```

**作用域级别（L0-L7）**：

| 级别 | 名称 | 说明 |
|------|------|------|
| L0 | Public | 公开信息，任何人可访问 |
| L1 | Internal | 内部信息，系统内可访问 |
| L2 | Confidential | 机密信息，需授权 |
| L3 | Secret | 秘密信息，严格授权 |
| L4 | Top Secret | 绝密信息 |
| L5 | Restricted | 受限信息 |
| L6 | Highly Restricted | 高度受限 |
| L7 | Sovereign | 最高级别，仅人类主权者可访问 |

### 2. Memory Kernel（记忆内核）

**职责**：管理多层记忆存储，提供记忆的增删改查和检索能力。

**核心功能**：

- 工作记忆（短期）
- 情景记忆（中期）
- 语义记忆（长期）
- 元记忆（元层）
- 记忆分层和作用域过滤
- 向量相似度检索
- 记忆衰减和巩固

**关键接口**：

```python
class MemoryKernel:
    def store(self, key: str, value: Any, tier: MemoryTier,
              scope: ScopeLevel, tags: List[str] = None) -> MemoryEntry:
        """存储记忆"""
    
    def recall(self, key: str, tier: MemoryTier = None) -> Optional[MemoryEntry]:
        """按 key 检索记忆"""
    
    def search(self, query: str, memory_type: str = None,
               limit: int = 10, threshold: float = 0.7) -> List[MemoryEntry]:
        """语义搜索记忆"""
    
    def forget(self, key: str, tier: MemoryTier) -> bool:
        """删除记忆"""
    
    def consolidate(self, source_tier: MemoryTier, target_tier: MemoryTier,
                    threshold: float) -> int:
        """记忆巩固（从低层转移到高层）"""
```

**记忆层级**：

| 层级 | TTL | 存储后端 | 用途 |
|------|-----|---------|------|
| SHORT_TERM | 5 分钟 | 内存 | 工作记忆 |
| MID_TERM | 1 小时 | 内存 + 持久化 | 情景记忆 |
| LONG_TERM | 1 天 | 持久化 | 语义记忆 |
| PERSISTENT | 30 天 / 永久 | 持久化 | 身份、元记忆 |

### 3. Context Kernel（上下文内核）

**职责**：将多种类型的输入整合成模型可用的上下文。

**核心功能**：

- 12 类输入汇聚
- 上下文压缩和摘要
- 上下文窗口管理
- 优先级排序
- 上下文注入

**输入类型**：

1. 用户指令
2. 对话历史
3. 工作记忆
4. 相关语义记忆
5. 最近情景记忆
6. 工具调用结果
7. 环境状态
8. 系统提示词
9. 任务目标
10. 约束条件
11. 示例/模板
12. 外部知识（RAG）

### 4. Capability Kernel（能力内核）

**职责**：管理系统中所有可用的能力（工具、技能、插件）。

**核心功能**：

- 能力注册和发现
- 版本管理
- 依赖解析
- 作用域检查
- 能力追溯链
- 能力评分和推荐

**关键接口**：

```python
class CapabilityKernel:
    def register(self, capability: Capability) -> Capability:
        """注册新能力"""
    
    def list(self, category: str = None, scope: ScopeLevel = None) -> List[Capability]:
        """列出可用能力"""
    
    def get(self, capability_id: str) -> Optional[Capability]:
        """获取能力详情"""
    
    def check_scope(self, capability_id: str, 
                    identity_scope: ScopeLevel) -> bool:
        """检查身份是否有权使用该能力"""
    
    def resolve_dependencies(self, capability_id: str) -> List[Capability]:
        """解析能力依赖"""
```

### 5. Policy Kernel（策略内核）

**职责**：基于策略的访问控制和行为约束。

**核心功能**：

- ABAC（基于属性的访问控制）
- 策略评估和优先级
- 人类主权覆盖
- 策略冲突解决
- 策略审计

**策略类型**：

1. **访问控制策略**：谁能访问什么资源
2. **行为约束策略**：智能体可以做什么、不能做什么
3. **审批策略**：哪些操作需要人类审批
4. **配额策略**：资源使用限制
5. **安全策略**：安全相关的约束

### 6. Execution Kernel（执行内核）

**职责**：管理从目标到行动的完整执行链路。

**核心功能**：

- 目标→任务→计划→行动→验证 全链路
- 任务分解和调度
- 计划执行和监控
- 工具调用执行
- 结果验证和反馈
- 重规划触发

**执行流水线**：

```
Goal → Task Decomposition → Planning → Action Execution 
     → Result Verification → Feedback → Re-planning (if needed)
```

---

## 代理内核（Agency Kernels）

代理内核负责智能体的运行环境和交互能力。

### 7. Resource Kernel（资源内核）

**职责**：管理和控制系统资源的使用。

**核心功能**：

- 六种资源配额管理：
  1. CPU 使用率
  2. 内存使用量
  3. 存储空间
  4. Token 消耗量
  5. 执行时间
  6. 费用成本
- 配额分配和追踪
- 资源限流
- 用量统计和报表
- 资源调度和优先级

**关键接口**：

```python
class ResourceKernel:
    def allocate(self, identity_id: str, resource_type: str,
                 amount: float) -> AllocationResult:
        """分配资源"""
    
    def release(self, allocation_id: str) -> bool:
        """释放资源"""
    
    def check_quota(self, identity_id: str, resource_type: str,
                    requested: float) -> bool:
        """检查配额是否充足"""
    
    def get_usage(self, identity_id: str) -> ResourceUsage:
        """获取资源使用情况"""
```

### 8. Event Kernel（事件内核）

**职责**：统一事件总线，管理系统内的事件流。

**核心功能**：

- 事件发布和订阅
- Correlation ID 追踪
- 事件持久化和回放
- 死信队列和重试
- 事件模式匹配
- 事件驱动的工作流

**事件类别**：

- 智能体事件（创建、启动、停止、错误）
- 任务事件（创建、进度、完成、失败）
- 记忆事件（新增、更新、删除、检索）
- 安全事件（认证、授权、违规）
- 系统事件（启动、关闭、配置变更）
- 自定义事件（应用层定义）

### 9. Network Kernel（网络内核）

**职责**：管理与外部系统和其他智能体的网络通信。

**核心功能**：

- 多协议适配：
  - A2A（Agent-to-Agent）
  - MCP（Model Context Protocol）
  - gRPC
  - HTTP/HTTPS
  - WebSocket
- 智能体发现和路由
- 网络安全策略执行
- 连接管理和池化

### 10. Trust Kernel（信任内核）

**职责**：管理实体间的信任关系。

**核心功能**：

- 信任评分计算
- 信任传播（一个实体的信任传递给相关实体）
- 信任衰减（信任随时间降低）
- 信任撤销
- 信任路径查询
- 风险评估

**信任模型**：

```
信任评分 = f(历史行为, 结果质量, 可靠性, 时间衰减, 推荐)
```

### 11. Evaluation Kernel（评估内核）

**职责**：评估智能体执行结果的质量和效果。

**核心功能**：

- 结果质量评估
- 用户反馈收集和处理
- 自动重规划触发
- 任务升级机制
- 评估指标计算
- 基准对比

**评估维度**：

1. **准确性**：结果是否正确
2. **完整性**：是否完成了所有要求
3. **效率**：耗时和资源消耗
4. **安全性**：是否符合安全策略
5. **满意度**：用户主观评价

---

## 主权内核（Sovereignty Kernels）

主权内核保障系统的安全性、可审计性和人类主权。

### 12. Security Kernel（安全内核）

**职责**：系统安全的核心保障。

**核心功能**：

- RBAC + ABAC 权限控制
- Vault Transit 加密
- 身份认证和授权
- 数据加密（传输中 + 静态）
- 安全审计
- 威胁检测

详见 [安全架构](security.md)。

### 13. Audit Kernel（审计内核）

**职责**：不可篡改的审计记录。

**核心功能**：

- 审计链（hash-chain，防篡改）
- 审计事件记录
- 关联查询（按实体、时间、操作类型）
- 审计完整性验证
- 外部时间戳锚定
- 透明度锚点

**审计链结构**：

```
Block 1          Block 2          Block 3
┌──────────┐     ┌──────────┐     ┌──────────┐
│ prev_hash│◄────│ prev_hash│◄────│ prev_hash│
│ data     │     │ data     │     │ data     │
│ hash     │     │ hash     │     │ hash     │
└──────────┘     └──────────┘     └──────────┘
```

每个区块包含前一个区块的哈希，形成链式结构，篡改任何一个区块都会导致链断裂。

### 14. Plugin Kernel（插件内核）

**职责**：插件系统的管理和运行。

**核心功能**：

- 插件注册和发现
- 插件热加载
- 版本兼容性检查
- 插件生命周期管理
- 作用域激活（按身份/角色激活插件）
- 插件依赖管理
- 插件安全沙箱

**插件生命周期**：

```
安装 → 注册 → 初始化 → 启用 → 运行 → 禁用 → 卸载
```

---

## 内核交互

### 典型交互流：智能体执行任务

```
用户请求
    │
    ▼
┌─────────┐ 验证身份
│ Identity│───────────┐
└────┬────┘           │
     │                ▼
     │          ┌─────────┐ 检查权限
     │          │ Policy  │──────┐
     │          └────┬────┘      │
     │               │           ▼
     ▼               ▼     ┌─────────┐ 检查资源
┌─────────┐     ┌─────────┐│Resource │
│ 执行内核 │────▶│ 能力内核 │└────┬────┘
└────┬────┘     └────┬────┘     │
     │               │           │
     ▼               ▼           ▼
┌─────────┐     ┌─────────┐
│ 记忆内核 │     │ 上下文内核│
└────┬────┘     └────┬────┘
     │               │
     └───────┬───────┘
             ▼
      ┌────────────┐
      │ 模型网关    │
      └──────┬─────┘
             ▼
         LLM 推理
```

### 事件驱动的内核通信

内核之间通过事件总线（Event Kernel）进行松耦合通信：

```
Kernel A ──事件──→ Event Bus ──事件──→ Kernel B
                          └───────────→ Kernel C
                          └───────────→ Kernel D
```

优势：
- 松耦合：内核不直接依赖
- 可观测：所有事件可追踪
- 可扩展：新增内核不影响现有系统
- 异步：支持非阻塞操作

---

## 内核边界

每个内核有明确的职责边界，避免越权操作。

### 边界原则

1. **单一职责**：每个内核只负责一件事
2. **接口契约**：内核间通过明确的接口通信
3. **数据隔离**：每个内核管理自己的数据
4. **通过事件协作**：不直接操作其他内核的内部状态
5. **安全检查点**：跨内核操作必须经过安全检查

### 跨内核操作

当一个操作需要多个内核协作时：

1. 发起内核发布事件
2. 相关内核订阅并处理
3. 每个内核在自己的边界内完成工作
4. 结果通过事件返回
5. 安全内核在关键节点进行检查

---

## 下一步

- [架构总览](overview.md) - 高层架构概览
- [安全架构](security.md) - 安全内核和防护机制
- [内核规范](../../../docs/spec/KERNEL-CANON.md) - 内核权威规格文档
