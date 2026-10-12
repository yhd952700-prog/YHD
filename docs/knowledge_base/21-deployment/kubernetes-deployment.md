# Kubernetes 部署指南

本文档提供将 LiuHao AI OS 部署到 Kubernetes 的完整指南，包含全部组件的 YAML 清单：API 网关、智能体运行时、内核服务、PostgreSQL、Redis 与消息总线。适用于生产环境部署，也适用于本地 minikube/kind 验证。

---

## 目录

- [部署架构](#部署架构)
- [前置要求](#前置要求)
- [命名空间与配置](#命名空间与配置)
- [有状态组件](#有状态组件)
- [无状态组件](#无状态组件)
- [Ingress 与 TLS](#ingress-与-tls)
- [扩缩容策略](#扩缩容策略)
- [滚动升级与回滚](#滚动升级与回滚)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 部署架构

```
                    ┌─────────────┐
        外部流量 ──▶│   Ingress    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        api-gateway   console-ui    websocket-gw     （无状态，HPA）
              │
     ┌────────┼─────────────────┐
     ▼        ▼                 ▼
 agent-runtime  kernel-* (14个)  model-gateway      （无状态，HPA）
     │
     ├──▶ postgres (StatefulSet + PVC)
     ├──▶ redis     (StatefulSet)
     └──▶ nats      (StatefulSet，消息总线)
```

设计原则：

- **内核与运行时无状态**：状态全部外置到 PG/Redis，Pod 可随时重建
- **有状态组件用 StatefulSet**：稳定网络标识 + 持久卷
- **管理面与数据面分离**：metrics/health 端口独立，不被业务洪峰影响

---

## 前置要求

| 项目 | 要求 |
|------|------|
| Kubernetes | ≥ 1.28 |
| kubectl | 已配置集群访问 |
| 存储类 | 支持动态供给（如 `standard`、`gp3`） |
| Ingress 控制器 | nginx-ingress 或 traefik |
| cert-manager | （可选）自动签发 TLS |
| 资源预估 | 最小 4C8G（开发），生产 ≥ 16C32G |

---

## 命名空间与配置

### namespace.yaml

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: liuhao
  labels:
    app.kubernetes.io/part-of: liuhao-ai-os
    pod-security.kubernetes.io/enforce: restricted
```

### configmap.yaml

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: liuhao-config
  namespace: liuhao
data:
  LIUHAO_ENV: "production"
  LIUHAO_LOG_LEVEL: "info"
  LIUHAO_LOG_FORMAT: "json"
  POSTGRES_HOST: "postgres"
  POSTGRES_PORT: "5432"
  POSTGRES_DB: "liuhao"
  REDIS_URL: "redis://redis:6379/0"
  NATS_URL: "nats://nats:4222"
  MODEL_GATEWAY_URL: "http://model-gateway:8080"
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://otel-collector:4317"
```

### secret.yaml

> ⚠️ 生产环境不要直接提交明文 Secret，使用 [密钥管理](../24-security/secrets-management.md) 中的 External Secrets 或 Sealed Secrets。

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: liuhao-secrets
  namespace: liuhao
type: Opaque
stringData:
  POSTGRES_PASSWORD: "change-me-in-production"
  JWT_SIGNING_KEY: "change-me-32-bytes-minimum-key"
  LLM_PROVIDER_API_KEY: "sk-..."
```

---

## 有状态组件

### postgres-statefulset.yaml

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: liuhao
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      securityContext:
        fsGroup: 999
      containers:
        - name: postgres
          image: postgres:16-alpine
          ports:
            - containerPort: 5432
          env:
            - name: POSTGRES_DB
              valueFrom:
                configMapKeyRef: { name: liuhao-config, key: POSTGRES_DB }
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: { name: liuhao-secrets, key: POSTGRES_PASSWORD }
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
          resources:
            requests: { cpu: 500m, memory: 1Gi }
            limits:   { cpu: "2",  memory: 4Gi }
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
          livenessProbe:
            exec: { command: ["pg_isready", "-U", "postgres"] }
            periodSeconds: 10
          readinessProbe:
            exec: { command: ["pg_isready", "-U", "postgres"] }
            periodSeconds: 5
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests: { storage: 50Gi }
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: liuhao
spec:
  clusterIP: None
  selector: { app: postgres }
  ports:
    - port: 5432
```

### redis-statefulset.yaml

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
  namespace: liuhao
spec:
  serviceName: redis
  replicas: 1
  selector:
    matchLabels: { app: redis }
  template:
    metadata:
      labels: { app: redis }
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          command: ["redis-server", "--appendonly", "yes",
                    "--maxmemory", "2gb", "--maxmemory-policy", "allkeys-lru"]
          ports:
            - containerPort: 6379
          resources:
            requests: { cpu: 250m, memory: 512Mi }
            limits:   { cpu: 500m, memory: 2500Mi }
          volumeMounts:
            - name: data
              mountPath: /data
          livenessProbe:
            tcpSocket: { port: 6379 }
            periodSeconds: 10
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests: { storage: 10Gi }
---
apiVersion: v1
kind: Service
metadata:
  name: redis
  namespace: liuhao
spec:
  clusterIP: None
  selector: { app: redis }
  ports:
    - port: 6379
```

### nats-statefulset.yaml（消息总线）

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: nats
  namespace: liuhao
spec:
  serviceName: nats
  replicas: 3                      # JetStream 需要 3 节点保证共识
  selector:
    matchLabels: { app: nats }
  template:
    metadata:
      labels: { app: nats }
    spec:
      containers:
        - name: nats
          image: nats:2.10-alpine
          command: ["nats-server", "-js", "--cluster_name", "liuhao-nats"]
          ports:
            - containerPort: 4222   # 客户端
            - containerPort: 6222   # 集群
            - containerPort: 8222   # 监控
          resources:
            requests: { cpu: 250m, memory: 256Mi }
            limits:   { cpu: "1",  memory: 1Gi }
          volumeMounts:
            - name: data
              mountPath: /data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests: { storage: 10Gi }
---
apiVersion: v1
kind: Service
metadata:
  name: nats
  namespace: liuhao
spec:
  clusterIP: None
  selector: { app: nats }
  ports:
    - { name: client, port: 4222 }
    - { name: monitor, port: 8222 }
```

---

## 无状态组件

### api-gateway-deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  namespace: liuhao
spec:
  replicas: 3
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  selector:
    matchLabels: { app: api-gateway }
  template:
    metadata:
      labels: { app: api-gateway }
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9090"
    spec:
      securityContext:
        runAsNonRoot: true
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: gateway
          image: registry.example.com/liuhao/api-gateway:1.4.2
          ports:
            - { name: http,    containerPort: 8080 }
            - { name: metrics, containerPort: 9090 }
          envFrom:
            - configMapRef: { name: liuhao-config }
            - secretRef:    { name: liuhao-secrets }
          resources:
            requests: { cpu: 250m, memory: 256Mi }
            limits:   { cpu: "1",  memory: 1Gi }
          readinessProbe:
            httpGet: { path: /health/ready, port: http }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /health/live, port: http }
            periodSeconds: 15
          startupProbe:                  # 慢启动保护，避免 liveness 误杀
            httpGet: { path: /health/live, port: http }
            periodSeconds: 3
            failureThreshold: 30
          lifecycle:
            preStop:
              exec: { command: ["sh", "-c", "sleep 5"] }  # 等 endpoints 摘除
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir: {}
      topologySpreadConstraints:         # 反亲和：分散到不同节点/可用区
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: { app: api-gateway }
---
apiVersion: v1
kind: Service
metadata:
  name: api-gateway
  namespace: liuhao
spec:
  selector: { app: api-gateway }
  ports:
    - { name: http, port: 80, targetPort: http }
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-gateway
  namespace: liuhao
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-gateway
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
    - type: Pods
      pods:
        metric: { name: http_requests_per_second }
        target: { type: AverageValue, averageValue: "500" }
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api-gateway
  namespace: liuhao
spec:
  minAvailable: 2
  selector:
    matchLabels: { app: api-gateway }
```

### agent-runtime-deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-runtime
  namespace: liuhao
spec:
  replicas: 5
  selector:
    matchLabels: { app: agent-runtime }
  template:
    metadata:
      labels: { app: agent-runtime }
    spec:
      containers:
        - name: runtime
          image: registry.example.com/liuhao/agent-runtime:1.4.2
          ports:
            - { name: grpc,    containerPort: 50051 }
            - { name: metrics, containerPort: 9090 }
          envFrom:
            - configMapRef: { name: liuhao-config }
            - secretRef:    { name: liuhao-secrets }
          env:
            - name: POD_NAME
              valueFrom: { fieldRef: { fieldPath: metadata.name } }
            - name: RUNTIME_MAX_AGENTS
              value: "200"                # 舱壁：单实例最多承载智能体数
          resources:
            requests: { cpu: "1", memory: 2Gi }
            limits:   { cpu: "4", memory: 8Gi }
          readinessProbe:
            grpc: { port: 50051 }
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: agent-runtime
  namespace: liuhao
spec:
  selector: { app: agent-runtime }
  ports:
    - { name: grpc, port: 50051 }
```

---

## Ingress 与 TLS

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: liuhao
  namespace: liuhao
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/limit-rps: "100"
    nginx.ingress.kubernetes.io/proxy-body-size: 10m
spec:
  ingressClassName: nginx
  tls:
    - hosts: [api.liuhao.example.com]
      secretName: liuhao-tls
  rules:
    - host: api.liuhao.example.com
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: api-gateway
                port: { name: http }
          - path: /ws
            pathType: Prefix
            backend:
              service:
                name: websocket-gw
                port: { number: 8080 }
```

---

## 扩缩容策略

| 组件 | 扩缩方式 | 指标 | 注意 |
|------|---------|------|------|
| api-gateway | HPA | CPU 70% + RPS 500 | 无状态，随意扩 |
| agent-runtime | HPA + 自定义 | 承载智能体数 80% | 缩容前需驱逐智能体 |
| kernel-* | HPA | CPU/延迟 | 无状态 |
| postgres | 垂直 | — | 读写分离后再考虑只读副本 |
| redis | 垂直/哨兵 | — | 集群模式改造见性能章节 |
| nats | 固定 3 节点 | — | 共识协议限制 |

智能体运行时缩容保护（优雅驱逐）：

```python
# agent_runtime/drain.py —— Pod 收到 SIGTERM 后执行
async def drain(signum, frame):
    await runtime.stop_accepting_new_agents()     # 1. 摘流
    await runtime.migrate_agents_to_peers()       # 2. 有状态智能体迁移
    await runtime.wait_inflight(timeout=60)       # 3. 等在途请求
    sys.exit(0)
```

配合 `terminationGracePeriodSeconds: 120` 使用。

---

## 滚动升级与回滚

```bash
# 升级
kubectl -n liuhao set image deployment/api-gateway \
  gateway=registry.example.com/liuhao/api-gateway:1.5.0

# 观察
kubectl -n liuhao rollout status deployment/api-gateway

# 回滚（保留 5 个历史版本）
kubectl -n liuhao rollout undo deployment/api-gateway --to-revision=3
```

数据库迁移必须与部署解耦：先跑向后兼容的 migration（expand），全量升级完成后再跑清理 migration（contract）。详见[数据库优化](../23-performance/database-optimization.md)。

---

## 最佳实践

1. **requests 必须设**：不设 requests 的 Pod 会被调度到超卖节点，抖动时先被驱逐。
2. **limits memory = requests memory × 1.5~2**：内存超卖易 OOMKill；CPU 可以超。
3. **startupProbe 必配**：慢启动应用不配会被 liveness 循环误杀。
4. **PDB + 优雅退出**：升级和节点维护时保证最少可用副本。
5. **镜像固定 tag 或 digest**：禁止 `latest`，回滚时才找得到版本。
6. **NetworkPolicy 默认拒绝**：见[安全加固](../24-security/security-hardening.md)。
7. **日志输出 stdout**：由节点日志代理收集，容器内不写日志文件。

---

## 常见问题

### Q1: Pod 频繁 OOMKilled 怎么办？

`kubectl describe pod` 看 OOM 时的内存曲线。常见原因：limits 太低、Python 进程随负载自然增长（设 `MALLOC_ARENA_MAX=2`）、缓存无上限。先垂直扩容止血，再排查泄漏。

### Q2: agent-runtime 缩容时智能体状态丢失？

运行时无状态设计要求智能体状态外置（Redis/PG）。若使用了内存态智能体，必须实现 drain 迁移逻辑（上文示例），或在 HPA 上为 runtime 关掉自动缩容，改人工操作。

### Q3: 本地开发不想装整个 K8s？

用 kind/minikube + 精简版清单，或直接用 [Docker Compose](docker-compose-setup.md) 单机起全部依赖。

### Q4: PostgreSQL 也放 K8s 里吗？

小规模可以（本文档方案）。生产规模建议用云托管（RDS/CloudSQL）或专门的数据库 operator（CloudNativePG），备份、PITR、故障切换更成熟。

### Q5: 如何做金丝雀发布？

配合 Argo Rollouts 或 Flagger：先 5% 流量到新版本，观察错误率/延迟 10 分钟，自动全量或回滚。Ingress 层面 nginx canary annotation 也能做简单版本。

---

**相关文档**：[Docker Compose 配置](docker-compose-setup.md) · [Helm Charts](helm-charts.md) · [CI/CD 流水线](ci-cd-pipeline.md) · [监控栈](monitoring-stack.md)
