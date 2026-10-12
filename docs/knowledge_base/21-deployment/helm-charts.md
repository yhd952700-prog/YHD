# Helm Charts 部署

Helm 是 Kubernetes 的包管理器。LiuHao AI OS 提供官方 Helm Chart，把 [Kubernetes 部署](kubernetes-deployment.md) 中的全部清单参数化为可复用、可版本化的 Chart。本文档覆盖 Chart 结构、values 配置、模板编写、发布与回滚、以及自建 Chart 仓库。

---

## 目录

- [Chart 结构](#chart-结构)
- [核心 values.yaml](#核心-valuesyaml)
- [模板示例](#模板示例)
- [安装与管理](#安装与管理)
- [多环境管理](#多环境管理)
- [子 Chart 与依赖](#子-chart-与依赖)
- [Chart 测试](#chart-测试)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## Chart 结构

```
charts/liuhao-ai-os/
├── Chart.yaml                  # Chart 元数据
├── values.yaml                 # 默认配置
├── values-dev.yaml             # 开发环境覆盖
├── values-prod.yaml            # 生产环境覆盖
├── templates/
│   ├── _helpers.tpl            # 模板辅助函数
│   ├── configmap.yaml
│   ├── secret.yaml             # 仅 dev；生产用 ExternalSecrets
│   ├── api-gateway/
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   ├── hpa.yaml
│   │   └── pdb.yaml
│   ├── agent-runtime/
│   │   ├── deployment.yaml
│   │   └── service.yaml
│   ├── postgres/
│   │   ├── statefulset.yaml
│   │   └── service.yaml
│   ├── ingress.yaml
│   ├── networkpolicy.yaml
│   └── tests/
│       └── smoke-test.yaml     # helm test 钩子
└── charts/                     # 子 Chart（bitnami/postgresql 等）
```

### Chart.yaml

```yaml
apiVersion: v2
name: liuhao-ai-os
description: LiuHao AI OS - Human-Sovereign Agent Operating System
type: application
version: 1.4.2              # Chart 版本（模板变更递增）
appVersion: "1.4.2"         # 应用版本（镜像 tag 默认值）
keywords: [ai, agents, llm]
maintainers:
  - name: LiuHao Platform Team
dependencies:
  - name: postgresql
    version: "15.x.x"
    repository: https://charts.bitnami.com/bitnami
    condition: postgresql.enabled
  - name: redis
    version: "19.x.x"
    repository: https://charts.bitnami.com/bitnami
    condition: redis.enabled
```

---

## 核心 values.yaml

```yaml
# values.yaml —— 所有可配置项的默认值与文档
global:
  imageRegistry: registry.example.com
  imagePullSecrets: []
  storageClass: ""            # 空 = 集群默认

image:
  repository: liuhao/api-gateway
  tag: ""                     # 空 = 使用 appVersion
  pullPolicy: IfNotPresent

apiGateway:
  replicaCount: 3
  resources:
    requests: { cpu: 250m, memory: 256Mi }
    limits:   { cpu: "1",  memory: 1Gi }
  autoscaling:
    enabled: true
    minReplicas: 3
    maxReplicas: 20
    targetCPUUtilizationPercentage: 70
  pdb: { enabled: true, minAvailable: 2 }

agentRuntime:
  replicaCount: 5
  maxAgentsPerPod: 200
  resources:
    requests: { cpu: "1", memory: 2Gi }
    limits:   { cpu: "4", memory: 8Gi }
  terminationGracePeriodSeconds: 120

postgresql:
  enabled: true               # false = 用外部数据库
  auth:
    database: liuhao
    existingSecret: liuhao-db-credentials
  primary:
    persistence: { size: 50Gi }
externalDatabase:             # postgresql.enabled=false 时使用
  host: ""
  port: 5432
  database: liuhao

redis:
  enabled: true
  auth: { existingSecret: liuhao-redis-credentials }

ingress:
  enabled: true
  className: nginx
  host: api.liuhao.example.com
  tls:
    enabled: true
    secretName: liuhao-tls
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod

secrets:
  create: false               # dev 可 true；生产务必 false + ExternalSecrets
  jwtSigningKey: ""
  llmProviderApiKey: ""

observability:
  serviceMonitor:
    enabled: true             # 需要 prometheus-operator
  networkPolicy:
    enabled: true
```

---

## 模板示例

### _helpers.tpl

```yaml
{{- define "liuhao.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "liuhao.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "liuhao.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "liuhao.labels" -}}
app.kubernetes.io/name: {{ include "liuhao.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "liuhao.image" -}}
{{- $tag := .tag | default .ctx.Chart.AppVersion -}}
{{- printf "%s/%s:%s" .ctx.Values.global.imageRegistry .repository $tag }}
{{- end }}
```

### templates/api-gateway/deployment.yaml

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "liuhao.fullname" . }}-api-gateway
  labels:
    {{- include "liuhao.labels" . | nindent 4 }}
    app.kubernetes.io/component: api-gateway
spec:
  {{- if not .Values.apiGateway.autoscaling.enabled }}
  replicas: {{ .Values.apiGateway.replicaCount }}
  {{- end }}
  selector:
    matchLabels:
      app.kubernetes.io/component: api-gateway
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/component: api-gateway
        app.kubernetes.io/instance: {{ .Release.Name }}
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
    spec:
      containers:
        - name: gateway
          image: {{ include "liuhao.image" (dict "repository" .Values.image.repository "tag" .Values.image.tag "ctx" .) }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - { name: http, containerPort: 8080 }
          resources:
            {{- toYaml .Values.apiGateway.resources | nindent 12 }}
          readinessProbe:
            httpGet: { path: /health/ready, port: http }
          livenessProbe:
            httpGet: { path: /health/live, port: http }
```

> `checksum/config` 注解让 ConfigMap 变更时自动触发滚动更新——这是最常被遗漏的关键细节。

### templates/tests/smoke-test.yaml

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: {{ include "liuhao.fullname" . }}-smoke-test
  annotations:
    "helm.sh/hook": test
spec:
  restartPolicy: Never
  containers:
    - name: curl
      image: curlimages/curl:8.8.0
      command: ["sh", "-c"]
      args:
        - |
          curl -fsS --retry 10 --retry-delay 3 \
            http://{{ include "liuhao.fullname" . }}-api-gateway/health/ready
```

---

## 安装与管理

```bash
# 添加依赖
helm dependency build charts/liuhao-ai-os

# 渲染检查（不写集群）
helm template liuhao charts/liuhao-ai-os -f values-prod.yaml | less

# dry-run 安装
helm install liuhao charts/liuhao-ai-os -n liuhao --create-namespace \
  -f values-prod.yaml --dry-run=server

# 正式安装
helm install liuhao charts/liuhao-ai-os -n liuhao --create-namespace \
  -f values-prod.yaml --timeout 10m

# 测试
helm test liuhao -n liuhao

# 升级（带原子性与超时）
helm upgrade liuhao charts/liuhao-ai-os -n liuhao \
  -f values-prod.yaml --atomic --cleanup-on-fail --timeout 10m

# 历史与回滚
helm history liuhao -n liuhao
helm rollback liuhao 3 -n liuhao

# 查看实际生效的 values（排查配置漂移）
helm get values liuhao -n liuhao --all
```

---

## 多环境管理

推荐**一个 Chart + 多份 values** 而非多个 Chart：

```bash
helm upgrade liuhao charts/liuhao-ai-os -n liuhao-dev  -f values-dev.yaml
helm upgrade liuhao charts/liuhao-ai-os -n liuhao-prod -f values-prod.yaml
```

values 分层（后者覆盖前者）：

1. `values.yaml` —— 安全默认值
2. `values-prod.yaml` —— 环境差异（副本数、域名、资源）
3. `--set` —— CI 注入的动态值（镜像 tag）

```bash
helm upgrade liuhao ./charts/liuhao-ai-os \
  -f values-prod.yaml \
  --set image.tag=$GIT_SHA        # CI 中注入
```

> 原则：values 文件里**只放环境差异**，共性留在默认值。发现 values-prod 和 values-dev 大面积重复，说明默认值没设计好。

---

## 子 Chart 与依赖

PostgreSQL/Redis 这类通用组件直接用 bitnami 子 Chart，不要自己维护：

```yaml
# values-prod.yaml 中覆盖子 Chart 配置
postgresql:
  enabled: false                # 生产用 RDS
redis:
  enabled: true
  architecture: replication
  sentinel: { enabled: true }
```

锁定依赖版本：

```bash
helm dependency update          # 生成 Chart.lock
git add Chart.lock charts/*.tgz # lock 与 tgz 都提交，保证可重现
```

---

## Chart 测试

三层测试：

1. **静态检查**：`helm lint charts/liuhao-ai-os`
2. **渲染快照**：`helm template` 输出与基线 diff（CI 中执行，防止模板改坏）
3. **集群内测试**：`helm test` 运行 hook Pod（上文 smoke-test.yaml）

CI 流水线示例（GitHub Actions 片段）：

```yaml
- name: Lint and render
  run: |
    helm lint charts/liuhao-ai-os
    for env in dev prod; do
      helm template liuhao charts/liuhao-ai-os \
        -f charts/liuhao-ai-os/values-$env.yaml \
        --set image.tag=test > /tmp/render-$env.yaml
    done

- name: Kind cluster smoke test
  run: |
    kind create cluster
    helm install liuhao charts/liuhao-ai-os -f charts/liuhao-ai-os/values-dev.yaml \
      --wait --timeout 5m
    helm test liuhao
```

---

## 最佳实践

1. **Chart 版本与应用版本分开**：`version` 动模板递增，`appVersion` 动应用递增；两者不强制同步。
2. **所有资源加标准 labels**（`app.kubernetes.io/*`），否则 NetworkPolicy、监控选择器都会很难写。
3. **ConfigMap 变更触发重启**：`checksum/config` 注解（上文模板示例）。
4. **秘钥不入 Chart**：values 里永不放真实秘钥，生产用 ExternalSecrets 引用 Vault/AWS SM。
5. **`--atomic` 升级**：失败自动回滚，避免半升级的残破 release。
6. **helm-diff 插件评审**：`helm diff upgrade` 在 CI 评论中展示变更，人工确认后 apply。
7. **不用 `latest` tag**，镜像 tag 默认取 `appVersion`。

---

## 常见问题

### Q1: helm upgrade 后 Pod 没变化？

两个常见原因：镜像 tag 没变（K8s 不会重拉相同 tag）——用 `checksum/config` 或唯一 tag；values 改的是未被模板引用的键——`helm template` 渲染验证。

### Q2: release 卡在 pending-upgrade？

上次升级中断留下锁。`helm history` 确认后 `helm rollback` 回退，或删除 `sh.helm.release.v1.<name>.v*` 中状态为 pending 的 Secret（谨慎）。

### Q3: 如何给同一集群装两套（蓝绿）？

release 名不同 + 命名空间不同：`helm install liuhao-blue ... -n liuhao-blue`。共享组件（PG）通过 `postgresql.enabled=false` 指向同一外部实例。

### Q4: 子 Chart 的 values 覆盖不生效？

子 Chart 配置必须嵌套在其名称键下（`postgresql:` 下的内容传给 postgresql 子 Chart）。层级写错是最常见错误，`helm template` 验证。

### Q5: 私有 Chart 仓库怎么搭？

简单方案：GitHub Pages + chart-releaser；企业方案：Harbor（OCI 模式，`helm push` OCI 引用）。Helm 3.8+ 推荐 OCI 仓库，与镜像仓库统一管理。

---

**相关文档**：[Kubernetes 部署](kubernetes-deployment.md) · [CI/CD 流水线](ci-cd-pipeline.md) · [Docker Compose](docker-compose-setup.md)
