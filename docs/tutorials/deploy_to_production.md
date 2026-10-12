# 部署到生产环境

本教程将指导你将 LiuHao AI OS 部署到生产环境，包括配置、监控、备份和扩展等关键环节。

---

## 目标

完成本教程后，你将拥有：

- 一个生产级别的 LiuHao AI OS 部署
- 完整的监控和告警体系
- 数据备份和恢复机制
- 可扩展的架构设计
- 安全加固的运行环境

---

## 生产环境清单

在开始部署之前，请确认以下清单：

### 基础设施

- [ ] 服务器（推荐 4 核 16GB 内存起步）
- [ ] 域名和 SSL 证书
- [ ] PostgreSQL 15+ 数据库（独立服务器更佳）
- [ ] Redis 缓存服务
- [ ] 对象存储（可选，用于文件存储）
- [ ] 负载均衡器（高可用部署）

### 软件

- [ ] Python 3.11+
- [ ] Node.js 18+（驾驶舱前端）
- [ ] Nginx 或其他反向代理
- [ ] 系统服务管理器（systemd）
- [ ] Docker（容器化部署）

### 安全

- [ ] 防火墙配置
- [ ] SSL/TLS 证书
- [ ] 身份认证方案
- [ ] 密钥管理方案
- [ ] 审计日志存储

---

## 第一步：部署架构设计

### 1.1 单机部署（适合小型团队）

```
┌─────────────────────────────────────┐
│         应用服务器                   │
│  ┌─────────┐  ┌─────────────────┐  │
│  │  Nginx  │──│  LiuHao AI OS   │  │
│  └─────────┘  │  (网关+驾驶舱)   │  │
│               └────────┬────────┘  │
│  ┌──────────────┐      │          │
│  │  PostgreSQL  │◄─────┘          │
│  │  + Redis     │                 │
│  └──────────────┘                 │
└─────────────────────────────────────┘
```

**适用场景**：
- 10 人以下小团队
- 日活用户 < 100
- 非核心业务系统

### 1.2 分层部署（适合中大型团队）

```
         ┌─────────────┐
         │  负载均衡器  │
         └──────┬──────┘
       ┌───────┴───────┐
       ▼               ▼
┌───────────┐   ┌───────────┐
│ 应用节点 1 │   │ 应用节点 2 │
│ (网关+前端)│   │ (网关+前端)│
└─────┬─────┘   └─────┬─────┘
      └───────┬───────┘
              ▼
       ┌─────────────┐
       │  PostgreSQL │  (主从复制)
       │   + Redis   │
       └─────────────┘
              │
              ▼
       ┌─────────────┐
       │ 对象存储     │
       └─────────────┘
```

**适用场景**：
- 中大型团队
- 高可用要求
- 需要水平扩展

### 1.3 容器化部署

使用 Docker Compose 或 Kubernetes 部署。

```yaml
# docker-compose.prod.yml 概览
services:
  gateway:
    image: liuhao-ai-os/gateway:latest
    deploy:
      replicas: 2
  
  console:
    image: liuhao-ai-os/console:latest
  
  postgres:
    image: postgres:15
  
  redis:
    image: redis:7-alpine
  
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
```

---

## 第二步：环境配置

### 2.1 生产环境变量

创建生产环境配置文件 `.env.prod`：

```bash
# ===== 基础配置 =====
ENVIRONMENT=production
LOG_LEVEL=WARNING

# ===== 服务配置 =====
GATEWAY_HOST=0.0.0.0
GATEWAY_PORT=8080
CONSOLE_PORT=5173

# ===== 数据库配置 =====
DATABASE_URL=postgresql://user:password@db.example.com:5432/liuhao
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10

# ===== 缓存配置 =====
REDIS_URL=redis://redis.example.com:6379/0
CACHE_TTL=3600

# ===== 模型配置 =====
OLLAMA_BASE_URL=http://ollama.example.com:11434
DEFAULT_MODEL=qwen2.5:7b
EMBEDDING_MODEL=nomic-embed-text
MODEL_TIMEOUT=120

# ===== 安全配置 =====
AUTH_ENABLED=true
JWT_SECRET=your-very-long-secret-key-here-change-in-production
JWT_EXPIRE_MINUTES=1440
ENCRYPTION_KEY=your-encryption-key-32-chars-min

# ===== 安全策略 =====
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS_PER_MINUTE=100
ALLOWED_ORIGINS=https://yourdomain.com

# ===== 审计配置 =====
AUDIT_ENABLED=true
AUDIT_CHAIN_ENABLED=true
AUDIT_RETENTION_DAYS=365

# ===== 备份配置 =====
BACKUP_ENABLED=true
BACKUP_SCHEDULE=0 2 * * *
BACKUP_RETENTION_DAYS=30

# ===== 监控配置 =====
METRICS_ENABLED=true
METRICS_PORT=9090
```

### 2.2 安全配置要点

1. **密钥管理**：
   - 使用强随机密钥
   - 密钥存储在环境变量或密钥管理服务中
   - 定期轮换密钥

2. **网络安全**：
   - 使用 HTTPS（TLS 1.3）
   - 配置安全响应头
   - 限制 CORS 来源
   - 启用速率限制

3. **访问控制**：
   - 启用身份认证
   - 配置最小权限原则
   - 启用操作审计

---

## 第三步：数据库配置

### 3.1 PostgreSQL 生产配置

生产环境推荐使用 PostgreSQL，以下是关键配置：

```sql
-- 创建数据库
CREATE DATABASE liuhao
  ENCODING 'UTF8'
  LC_COLLATE = 'en_US.UTF-8'
  LC_CTYPE = 'en_US.UTF-8'
  TEMPLATE template0;

-- 创建用户
CREATE USER liuhao WITH PASSWORD 'strong-password';
GRANT ALL PRIVILEGES ON DATABASE liuhao TO liuhao;

-- 连接限制
ALTER DATABASE liuhao SET CONNECTION LIMIT = 100;
```

### 3.2 PostgreSQL 性能调优

在 `postgresql.conf` 中调整以下参数：

```ini
# 内存配置（根据服务器内存调整，示例为 16GB 内存）
shared_buffers = 4GB
work_mem = 16MB
maintenance_work_mem = 512MB
effective_cache_size = 12GB

# 连接配置
max_connections = 200

# WAL 配置
wal_level = replica
max_wal_size = 4GB
min_wal_size = 1GB

# 检查点
checkpoint_completion_target = 0.9

# 查询优化
random_page_cost = 1.1
effective_io_concurrency = 200
```

### 3.3 数据库迁移

```bash
# 运行数据库迁移
alembic upgrade head

# 验证迁移状态
alembic current

# 备份后再迁移（推荐）
pg_dump liuhao > backup_before_migration.sql
alembic upgrade head
```

---

## 第四步：监控设置

### 4.1 健康检查

配置负载均衡器的健康检查：

```nginx
# Nginx 配置示例
upstream liuhao_backend {
    server app1:8080 max_fails=3 fail_timeout=30s;
    server app2:8080 max_fails=3 fail_timeout=30s;
}

server {
    listen 443 ssl;
    server_name api.yourdomain.com;
    
    location /v1/health {
        proxy_pass http://liuhao_backend;
        access_log off;
    }
    
    location / {
        proxy_pass http://liuhao_backend;
        health_check interval=10s fails=3 passes=2;
    }
}
```

### 4.2 Prometheus + Grafana 监控

```yaml
# prometheus.yml 配置
scrape_configs:
  - job_name: 'liuhao-gateway'
    static_configs:
      - targets: ['app1:9090', 'app2:9090']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

关键监控指标：

| 指标类别 | 指标名 | 告警阈值 |
|---------|--------|---------|
| 服务状态 | `health_status` | != 1 |
| 请求量 | `http_requests_total` | 突增/突降 |
| 错误率 | `http_error_rate` | > 5% |
| 延迟 | `http_duration_p99` | > 2s |
| 数据库连接 | `db_connections_active` | > 80% 池大小 |
| 内存使用 | `memory_usage_bytes` | > 85% |
| 智能体数量 | `agents_active` | > 上限的 80% |
| 任务积压 | `tasks_pending` | > 100 |

### 4.3 日志管理

```python
# 生产环境日志配置
LOG_FORMAT = json  # 使用 JSON 格式，便于日志分析
LOG_LEVEL = WARNING
LOG_FILE = /var/log/liuhao/app.log
LOG_MAX_SIZE = 100MB
LOG_BACKUP_COUNT = 30

# 重要日志类型
# - 访问日志（access log）
# - 错误日志（error log）
# - 审计日志（audit log）
# - 安全日志（security log）
```

---

## 第五步：备份与恢复

### 5.1 备份策略

| 数据类型 | 备份频率 | 保留时间 | 存储位置 |
|---------|---------|---------|---------|
| 数据库 | 每日全量 + 实时 WAL | 30 天 | 异地对象存储 |
| 配置文件 | 每次变更 | 永久 | 版本控制 |
| 审计日志 | 每日归档 | 365 天 | 冷存储 |
| 用户上传文件 | 每日 | 永久 | 对象存储 |

### 5.2 自动备份脚本

```bash
#!/bin/bash
# backup.sh - 自动备份脚本

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backup/liuhao"
RETENTION_DAYS=30

# 1. 数据库备份
pg_dump -U liuhao liuhao | gzip > "$BACKUP_DIR/db_$DATE.sql.gz"

# 2. 配置文件备份
tar -czf "$BACKUP_DIR/config_$DATE.tar.gz" /etc/liuhao/

# 3. 清理旧备份
find "$BACKUP_DIR" -name "*.gz" -mtime +$RETENTION_DAYS -delete

# 4. 上传到对象存储（可选）
# aws s3 cp "$BACKUP_DIR/db_$DATE.sql.gz" s3://my-backups/liuhao/

echo "备份完成: $DATE"
```

### 5.3 恢复测试

定期进行恢复测试，确保备份有效：

```bash
# 恢复数据库
gunzip -c db_20261001_020000.sql.gz | psql -U liuhao liuhao_restore

# 验证数据完整性
python scripts/verify_backup.py --backup db_20261001_020000.sql.gz
```

---

## 第六步：扩展与优化

### 6.1 水平扩展

当单台服务器无法满足需求时，可以水平扩展：

1. **无状态应用节点**：网关和前端服务可以直接增加实例
2. **数据库扩展**：读写分离、分库分表
3. **缓存层**：增加 Redis 实例或集群
4. **模型推理**：增加 Ollama 实例或使用云端 API

### 6.2 性能优化

**应用层**：
- 启用 HTTP 缓存
- 使用 CDN 加速静态资源
- 优化数据库查询（添加索引）
- 增加缓存层

**模型层**：
- 选择合适大小的模型
- 使用模型量化（如 Q4_K_M）
- 批量处理请求
- 配置模型路由（简单请求用小模型）

### 6.3 成本优化

- **模型选择**：根据任务复杂度选择合适大小的模型
- **缓存策略**：缓存频繁请求的结果
- **资源调度**：闲时降低资源配置
- **监控成本**：跟踪 token 消耗，设置预算告警

---

## 第七步：上线前检查清单

### 安全检查

- [ ] HTTPS 已配置并生效
- [ ] 防火墙规则已配置
- [ ] 默认密码已修改
- [ ] API Key 使用强密钥
- [ ] 敏感配置不提交到代码仓库
- [ ] 速率限制已启用
- [ ] CORS 配置正确

### 功能检查

- [ ] 健康检查端点正常
- [ ] 所有 API 端点可访问
- [ ] 智能体可以正常创建和执行
- [ ] 记忆系统正常工作
- [ ] 插件加载正常
- [ ] 驾驶舱前端可访问

### 数据检查

- [ ] 数据库迁移完成
- [ ] 初始数据已导入
- [ ] 备份机制已配置
- [ ] 数据加密已启用

### 监控检查

- [ ] 监控系统已部署
- [ ] 关键指标正常显示
- [ ] 告警规则已配置
- [ ] 日志收集正常
- [ ] 审计日志已启用

### 性能检查

- [ ] 首页加载时间 < 3s
- [ ] API P95 延迟 < 500ms
- [ ] 并发测试通过
- [ ] 内存使用率 < 80%
- [ ] CPU 使用率 < 70%

---

## 第八步：运维手册

### 常用操作

```bash
# 启动服务
systemctl start liuhao

# 停止服务
systemctl stop liuhao

# 重启服务
systemctl restart liuhao

# 查看状态
systemctl status liuhao

# 查看日志
journalctl -u liuhao -f

# 查看版本
liuhao version

# 健康检查
liuhao health check
```

### 故障排查流程

1. **检查服务状态**：服务是否在运行
2. **检查健康检查**：健康端点是否正常
3. **查看错误日志**：最近的错误信息
4. **检查资源使用**：CPU、内存、磁盘
5. **检查数据库连接**：数据库是否正常
6. **检查模型服务**：Ollama/API 是否可用
7. **查看审计日志**：操作记录是否异常

### 紧急回滚

如果新版本有问题，快速回滚：

```bash
# 1. 停止服务
systemctl stop liuhao

# 2. 恢复代码（根据部署方式）
git checkout <previous-version>
# 或
docker pull liuhao-ai-os:previous-version

# 3. 恢复数据库（如果有 schema 变更）
pg_restore -d liuhao backup_before_upgrade.sql.gz

# 4. 重启服务
systemctl start liuhao

# 5. 验证服务
liuhao health check
```

---

## 下一步

- [架构概览](../knowledge_base/architecture/overview.md) - 系统架构理解
- [安全架构](../knowledge_base/architecture/security.md) - 安全设计详解
- [故障排除](../knowledge_base/troubleshooting/common_issues.md) - 常见问题排查
