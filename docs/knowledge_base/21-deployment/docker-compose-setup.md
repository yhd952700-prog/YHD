# Docker Compose 部署配置

Docker Compose 是 LiuHao AI OS 推荐的**单机部署**方式，适用于开发环境、POC 验证、小型团队自建实例（≤ 50 个并发智能体）。本文档提供完整的 `docker-compose.yml`、环境变量说明、数据备份与升级流程。生产高可用部署请使用 [Kubernetes](kubernetes-deployment.md)。

---

## 目录

- [适用场景与限制](#适用场景与限制)
- [完整 docker-compose.yml](#完整-docker-composeyml)
- [环境变量（.env）](#环境变量env)
- [启动与验证](#启动与验证)
- [数据卷与备份](#数据卷与备份)
- [资源限制调优](#资源限制调优)
- [升级流程](#升级流程)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 适用场景与限制

**适合：**

- 本地开发与联调（一条命令起全套依赖）
- 演示与 POC 环境
- 小团队生产实例（单点故障可接受，有备份策略）

**不适合：**

- 需要零停机升级的生产环境
- 多节点水平扩展
- 强 SLA 要求（Compose 无自愈调度）

---

## 完整 docker-compose.yml

```yaml
# docker-compose.yml
name: liuhao-ai-os

x-default-logging: &default-logging
  driver: json-file
  options:
    max-size: "50m"
    max-file: "5"

services:
  # ---------- 基础设施 ----------
  postgres:
    image: postgres:16-alpine
    container_name: liuhao-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-liuhao}
      POSTGRES_USER: ${POSTGRES_USER:-liuhao}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./deploy/postgres/init:/docker-entrypoint-initdb.d:ro
    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"   # 只绑本机，不暴露公网
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-liuhao}"]
      interval: 5s
      timeout: 3s
      retries: 20
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 4G }
    logging: *default-logging

  redis:
    image: redis:7-alpine
    container_name: liuhao-redis
    restart: unless-stopped
    command:
      - redis-server
      - --appendonly=yes
      - --maxmemory=2gb
      - --maxmemory-policy=allkeys-lru
      - --requirepass=${REDIS_PASSWORD:?REDIS_PASSWORD is required}
    volumes:
      - redis-data:/data
    ports:
      - "127.0.0.1:${REDIS_PORT:-6379}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20
    deploy:
      resources:
        limits: { cpus: "0.5", memory: 2500M }
    logging: *default-logging

  nats:
    image: nats:2.10-alpine
    container_name: liuhao-nats
    restart: unless-stopped
    command: ["nats-server", "-js", "-sd", "/data", "-m", "8222"]
    volumes:
      - nats-data:/data
    ports:
      - "127.0.0.1:${NATS_PORT:-4222}:4222"
      - "127.0.0.1:8222:8222"          # 监控端点
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8222/healthz"]
      interval: 5s
      timeout: 3s
      retries: 20
    logging: *default-logging

  # ---------- 应用层 ----------
  model-gateway:
    image: registry.example.com/liuhao/model-gateway:${LIUHAO_VERSION:-latest}
    container_name: liuhao-model-gateway
    restart: unless-stopped
    env_file: .env
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      NATS_URL: nats://nats:4222
    depends_on:
      redis: { condition: service_healthy }
      nats:  { condition: service_healthy }
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 2G }
    logging: *default-logging

  agent-runtime:
    image: registry.example.com/liuhao/agent-runtime:${LIUHAO_VERSION:-latest}
    container_name: liuhao-agent-runtime
    restart: unless-stopped
    env_file: .env
    environment:
      POSTGRES_URL: postgres://${POSTGRES_USER:-liuhao}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-liuhao}
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      NATS_URL: nats://nats:4222
      MODEL_GATEWAY_URL: http://model-gateway:8080
      RUNTIME_MAX_AGENTS: "200"
    depends_on:
      postgres:      { condition: service_healthy }
      redis:         { condition: service_healthy }
      nats:          { condition: service_healthy }
      model-gateway: { condition: service_started }
    deploy:
      resources:
        limits: { cpus: "4.0", memory: 8G }
    logging: *default-logging

  api-gateway:
    image: registry.example.com/liuhao/api-gateway:${LIUHAO_VERSION:-latest}
    container_name: liuhao-api-gateway
    restart: unless-stopped
    env_file: .env
    environment:
      AGENT_RUNTIME_URL: http://agent-runtime:8080
      POSTGRES_URL: postgres://${POSTGRES_USER:-liuhao}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-liuhao}
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
    ports:
      - "${API_PORT:-8080}:8080"       # 唯一对外端口
    depends_on:
      agent-runtime: { condition: service_started }
      postgres:      { condition: service_healthy }
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/health/ready"]
      interval: 10s
      timeout: 3s
      retries: 12
    deploy:
      resources:
        limits: { cpus: "2.0", memory: 2G }
    logging: *default-logging

  console-ui:
    image: registry.example.com/liuhao/console-ui:${LIUHAO_VERSION:-latest}
    container_name: liuhao-console
    restart: unless-stopped
    ports:
      - "${CONSOLE_PORT:-3000}:80"
    depends_on:
      - api-gateway
    logging: *default-logging

  # ---------- 可观测（可选 profile） ----------
  prometheus:
    image: prom/prometheus:v2.53.0
    container_name: liuhao-prometheus
    restart: unless-stopped
    profiles: ["observability"]
    volumes:
      - ./deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    ports:
      - "127.0.0.1:9090:9090"
    logging: *default-logging

  grafana:
    image: grafana/grafana:11.1.0
    container_name: liuhao-grafana
    restart: unless-stopped
    profiles: ["observability"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin}
    volumes:
      - grafana-data:/var/lib/grafana
      - ./deploy/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
    ports:
      - "127.0.0.1:3001:3000"
    depends_on:
      - prometheus
    logging: *default-logging

volumes:
  postgres-data:
  redis-data:
  nats-data:
  prometheus-data:
  grafana-data:

networks:
  default:
    name: liuhao-net
```

---

## 环境变量（.env）

```bash
# .env —— 勿提交到 git，见 secrets-management.md
LIUHAO_VERSION=1.4.2

# 数据库
POSTGRES_DB=liuhao
POSTGRES_USER=liuhao
POSTGRES_PASSWORD=生成一个32位随机串

# Redis
REDIS_PASSWORD=生成一个32位随机串

# 端口
API_PORT=8080
CONSOLE_PORT=3000

# LLM 提供商
LLM_PROVIDER_API_KEY=sk-...
LLM_DEFAULT_MODEL=gpt-5

# 安全
JWT_SIGNING_KEY=生成一个64位随机串
ADMIN_BOOTSTRAP_TOKEN=首次启动后失效

# Grafana（可选）
GRAFANA_PASSWORD=admin-change-me
```

生成随机串：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 启动与验证

```bash
# 1. 基础启动
docker compose up -d

# 2. 带可观测组件
docker compose --profile observability up -d

# 3. 验证健康
curl -fsS http://localhost:8080/health/ready | jq .
docker compose ps                                # 全部 healthy/running

# 4. 冒烟测试：创建一个智能体
curl -X POST http://localhost:8080/api/v1/agents \
  -H "Authorization: Bearer $ADMIN_BOOTSTRAP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "smoke-test", "role": "general"}'

# 5. 看日志
docker compose logs -f agent-runtime
```

---

## 数据卷与备份

### 备份脚本

```bash
#!/usr/bin/env bash
# deploy/backup.sh —— 每日备份，保留 14 天
set -euo pipefail
BACKUP_DIR="/var/backups/liuhao/$(date +%F)"
mkdir -p "$BACKUP_DIR"

# PostgreSQL：逻辑备份
docker exec liuhao-postgres pg_dump -U liuhao -Fc liuhao \
  > "$BACKUP_DIR/postgres.dump"

# Redis：触发 AOF 重写后拷贝
docker exec liuhao-redis redis-cli -a "$REDIS_PASSWORD" BGREWRITEAOF
sleep 5
docker cp liuhao-redis:/data/appendonlydir "$BACKUP_DIR/redis-aof"

# NATS JetStream：快照
docker exec liuhao-nats nats stream backup "$BACKUP_DIR/nats" 2>/dev/null || true

# 保留策略
find /var/backups/liuhao -maxdepth 1 -mtime +14 -exec rm -rf {} +
echo "backup completed: $BACKUP_DIR"
```

crontab：`0 3 * * * REDIS_PASSWORD=xxx /opt/liuhao/deploy/backup.sh`

### 恢复

```bash
# 停应用，保留数据容器
docker compose stop api-gateway agent-runtime model-gateway

# 恢复 PG
cat postgres.dump | docker exec -i liuhao-postgres \
  pg_restore -U liuhao -d liuhao --clean --if-exists

docker compose start model-gateway agent-runtime api-gateway
```

---

## 资源限制调优

单机 16C32G 推荐分配：

| 服务 | CPU | 内存 | 说明 |
|------|-----|------|------|
| postgres | 2.0 | 4G | shared_buffers 1G |
| redis | 0.5 | 2.5G | maxmemory 2G |
| agent-runtime | 4.0 | 8G | 主力，按智能体数线性扩 |
| model-gateway | 2.0 | 2G | 主要是 IO |
| api-gateway | 2.0 | 2G | |
| 余量 | — | ~10G | OS + 突发 |

> Compose 的 `deploy.resources` 仅在 `docker compose up`（v2）下生效；swarm 模式语义不同。

---

## 升级流程

```bash
# 1. 备份（必做）
./deploy/backup.sh

# 2. 拉新镜像
LIUHAO_VERSION=1.5.0 docker compose pull

# 3. 数据库迁移（向后兼容的 expand migration 先跑）
docker compose run --rm api-gateway liuhao-migrate up

# 4. 滚动重启（有短暂停机）
LIUHAO_VERSION=1.5.0 docker compose up -d

# 5. 验证
curl -fsS http://localhost:8080/health/ready
docker compose logs --tail=100 agent-runtime | grep -i error

# 6. 出问题则回滚
LIUHAO_VERSION=1.4.2 docker compose up -d
# 若 migration 是 expand-only，回滚安全；contract 需等全量稳定后单独执行
```

---

## 最佳实践

1. **`.env` 不进 git**，用 `.env.example` 记录键名。生产用 [密钥管理](../24-security/secrets-management.md)。
2. **数据库端口只绑 127.0.0.1**，对外只暴露 api-gateway。
3. **所有服务配 healthcheck + restart: unless-stopped**，宿主机重启后自动恢复。
4. **日志限额**（max-size/max-file），否则磁盘会被 LLM 调用日志写满。
5. **版本号锁定**：`LIUHAO_VERSION` 固定具体 tag，升级是显式动作。
6. **备份即演练**：每季度做一次恢复演练，没恢复过的备份等于没有。
7. **`docker compose config` 校验**：改完 yml 先渲染检查再 up。

---

## 常见问题

### Q1: 容器间连不上，报 DNS 解析失败？

自定义网络名冲突或容器名写错。`docker compose ps` 确认容器名（本文档显式指定了 `container_name`）；同一 compose 项目内用服务名互访即可。

### Q2: agent-runtime 启动报 Postgres 连接拒绝？

`depends_on` 只保证启动顺序，不保证就绪——务必配 `condition: service_healthy`（compose v2.24+）。旧版本需要在应用内实现连接重试。

### Q3: 内存持续上涨最后 OOM？

`docker stats` 定位是哪个容器。常见：agent-runtime 承载了过多智能体（调低 `RUNTIME_MAX_AGENTS` 或垂直扩容）；Redis 无 maxmemory（本文档已配）。

### Q4: 如何在 Windows/macOS 上跑？

Docker Desktop 直接支持。注意：文件挂载性能差（用 named volume 而非 bind mount）；内存上限在 Docker Desktop 设置里调（默认 2G 不够）。

### Q5: 能从 Compose 平滑迁移到 K8s 吗？

配置映射是直接的：service → Deployment+Service，volume → PVC，healthcheck → probes。建议用 [Helm Charts](helm-charts.md) 而非手写转换。数据迁移用备份恢复流程。

---

**相关文档**：[Kubernetes 部署](kubernetes-deployment.md) · [监控栈](monitoring-stack.md) · [性能调优](../23-performance/performance-tuning.md)
