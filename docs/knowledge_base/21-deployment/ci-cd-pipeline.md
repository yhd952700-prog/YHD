# CI/CD 流水线

本文档描述 LiuHao AI OS 推荐的持续集成与持续交付流水线：从代码提交到生产部署的完整自动化路径，包含质量门禁、安全扫描、镜像构建、渐进式交付与回滚策略。示例使用 GitHub Actions，概念同样适用于 GitLab CI、Jenkins 与 ArgoCD 生态。

---

## 目录

- [流水线总览](#流水线总览)
- [持续集成（CI）](#持续集成ci)
- [镜像构建与签名](#镜像构建与签名)
- [持续交付（CD）](#持续交付cd)
- [渐进式交付](#渐进式交付)
- [回滚策略](#回滚策略)
- [数据库迁移门禁](#数据库迁移门禁)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 流水线总览

```
push/PR ──▶ CI（5~10min）                merge ──▶ CD
            ├─ lint / format                       ├─ 构建镜像 + 签名
            ├─ 单测 + 覆盖率门禁                    ├─ 推送仓库 + SBOM
            ├─ 类型检查 (mypy strict)              ├─ 部署 dev（自动）
            ├─ 安全扫描 (依赖/密钥/SAST)            ├─ 部署 staging（自动）
            └─ 集成测试 (kind 集群)                 ├─ 金丝雀 prod 5%
                                                  └─ 全量 prod（自动/审批）
```

设计原则：

- **CI 要快**：10 分钟内反馈，慢的测试下沉到夜间任务
- **制品不可变**：一次构建的镜像贯穿 dev→staging→prod，只改配置不重构建
- **每个环节可回滚**：部署是声明式的，回滚 = 重新指向旧版本

---

## 持续集成（CI）

### .github/workflows/ci.yaml

```yaml
name: CI
on:
  push: { branches: [main] }
  pull_request:

concurrency:                        # 同一 PR 新提交取消旧运行，省资源
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install ruff mypy
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy --strict src/

  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ".[test]"
      - run: |
          pytest tests/unit -q \
            --cov=src --cov-report=xml --cov-fail-under=85
      - uses: codecov/codecov-action@v4

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Dependency scan
        run: pip install pip-audit && pip-audit --strict
      - name: Secret scan
        uses: trufflesecurity/trufflehog@main
        with: { extra_args: --only-verified }
      - name: SAST
        uses: github/codeql-action/analyze@v3

  integration:
    runs-on: ubuntu-latest
    needs: [lint, unit-tests]
    steps:
      - uses: actions/checkout@v4
      - uses: helm/kind-action@v1
      - run: pip install -e ".[test]"
      - name: Deploy to kind
        run: |
          docker compose build
          helm install liuhao charts/liuhao-ai-os \
            -f charts/liuhao-ai-os/values-dev.yaml --wait --timeout 5m
      - run: pytest tests/integration -q --timeout=300

  e2e-smoke:
    runs-on: ubuntu-latest
    needs: [integration]
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - run: pytest tests/e2e/smoke -q   # 见 testing-strategies.md
```

### 质量门禁标准

| 门禁 | 阈值 | 失败处理 |
|------|------|---------|
| 单元测试覆盖率 | ≥ 85%（增量 90%） | 阻断合并 |
| mypy strict | 0 错误 | 阻断合并 |
| ruff | 0 错误 | 阻断合并 |
| pip-audit | 无高危/严重漏洞 | 阻断合并 |
| 集成测试 | 全绿 | 阻断合并 |

---

## 镜像构建与签名

### .github/workflows/build.yaml

```yaml
name: Build
on:
  push: { branches: [main], tags: ["v*"] }

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write             # OIDC，用于无密钥签名
    strategy:
      matrix:
        component: [api-gateway, agent-runtime, model-gateway, console-ui]
    steps:
      - uses: actions/checkout@v4

      - name: Docker metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: registry.example.com/liuhao/${{ matrix.component }}
          tags: |
            type=sha,prefix=
            type=semver,pattern={{version}}

      - uses: docker/setup-buildx-action@v3

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: deploy/docker/${{ matrix.component }}.Dockerfile
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          provenance: true          # 构建来源证明
          sbom: true                # 软件物料清单

      - name: Sign image (cosign keyless)
        uses: sigstore/cosign-installer@v3
      - run: |
          cosign sign --yes \
            registry.example.com/liuhao/${{ matrix.component }}@${{ steps.build.outputs.digest }}

      - name: Vulnerability scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: registry.example.com/liuhao/${{ matrix.component }}:${{ github.sha }}
          severity: CRITICAL,HIGH
          exit-code: "1"            # 高危漏洞阻断
```

供应链三件套：

- **SBOM**：镜像内软件物料清单，漏洞响应时快速定位影响面
- **Provenance**：SLSA 构建来源证明，设计为防篡改（构建产物完整性验证当前 UNVERIFIED）
- **Cosign 签名**：部署端校验，未签名镜像拒绝运行（见[安全加固](../24-security/security-hardening.md)）

---

## 持续交付（CD）

### GitOps 模式（推荐）

部署状态存于 git 仓库，ArgoCD 负责收敛：

```
deploy-repo/
├── environments/
│   ├── dev/kustomization.yaml      # 自动跟踪 main 分支镜像
│   ├── staging/kustomization.yaml  # 自动
│   └── prod/kustomization.yaml     # 需 PR 审批 + 金丝雀
```

CD 流水线只更新 deploy-repo 中的镜像 tag：

```yaml
# .github/workflows/cd.yaml 片段
- name: Update deploy repo
  run: |
    git clone https://x-access-token:${{ secrets.DEPLOY_TOKEN }}@github.com/org/deploy-repo
    cd deploy-repo/environments/staging
    kustomize edit set image \
      registry.example.com/liuhao/api-gateway=registry.example.com/liuhao/api-gateway:${{ github.sha }}
    git commit -am "deploy: ${{ github.sha }} to staging"
    git push
```

ArgoCD Application：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: liuhao-staging
spec:
  source:
    repoURL: https://github.com/org/deploy-repo
    path: environments/staging
  destination:
    server: https://kubernetes.default.svc
    namespace: liuhao-staging
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true]
```

---

## 渐进式交付

### 金丝雀发布（Argo Rollouts）

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: api-gateway
  namespace: liuhao-prod
spec:
  replicas: 10
  strategy:
    canary:
      steps:
        - setWeight: 5              # 5% 流量
        - pause: { duration: 10m }  # 观察窗口
        - setWeight: 25
        - pause: { duration: 10m }
        - setWeight: 50
        - pause: { duration: 5m }
      canaryAnalysis:               # 自动分析，不达标自动回滚
        templates:
          - templateName: success-rate
        args:
          - name: service-name
            value: api-gateway
  selector:
    matchLabels: { app: api-gateway }
  template:
    metadata:
      labels: { app: api-gateway }
    spec:
      containers:
        - name: gateway
          image: registry.example.com/liuhao/api-gateway:sha-abc123
---
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
spec:
  args:
    - name: service-name
  metrics:
    - name: error-rate
      interval: 1m
      failureLimit: 2               # 连续 2 次不达标则回滚
      successCondition: result[0] < 0.01
      provider:
        prometheus:
          address: http://prometheus:9090
          query: |
            sum(rate(http_requests_total{
              service="{{args.service-name}}", status=~"5.."
            }[1m]))
            /
            sum(rate(http_requests_total{service="{{args.service-name}}"}[1m]))
```

### 各环境晋级策略

| 环境 | 触发 | 发布方式 | 审批 |
|------|------|---------|------|
| dev | merge 到 main | 直接全量 | 无 |
| staging | dev 稳定 30min | 直接全量 + e2e 套件 | 无 |
| prod | staging e2e 通过 | 金丝雀 5%→25%→50%→100% | 版本管理员一键确认 |

---

## 回滚策略

### 自动回滚（首选）

金丝雀分析失败自动回滚，无需人工。Argo Rollouts 处理。

### 手动回滚

```bash
# K8s 原生
kubectl -n liuhao-prod rollout undo deployment/api-gateway

# Helm
helm rollback liuhao 5 -n liuhao-prod

# GitOps（推荐：保持 git 为唯一事实源）
git revert <deploy-commit> && git push    # ArgoCD 自动收敛
```

### 回滚决策矩阵

| 症状 | 动作 |
|------|------|
| 错误率 > 1% 持续 2min | 自动回滚（金丝雀门禁） |
| P99 延迟恶化 50% | 自动回滚 |
| 业务指标异常（如智能体成功率下降） | 人工评估后回滚 |
| 数据库 migration 失败 | **不要回滚应用**，修复 migration（见下节） |

---

## 数据库迁移门禁

迁移是部署中最危险的部分。采用 **expand-contract** 模式：

```
Release N:   expand migration   （加列/加表，向后兼容）
             部署新应用          （新旧代码都能跑）
Release N+1: 验证无回滚需求后
             contract migration （删旧列/旧表）
```

CI 中的迁移检查：

```yaml
- name: Migration safety check
  run: |
    python scripts/check_migration_safety.py
    # 规则：
    # 1. 禁止在同一 PR 中 DROP COLUMN + 删除引用代码
    # 2. 加 NOT NULL 列必须有 DEFAULT
    # 3. 索引必须 CONCURRENTLY 创建
    # 4. 单 migration 预估锁表时间 < 1s
```

部署顺序：

```bash
helm upgrade liuhao ./charts --set image.tag=$SHA \
  --set migrations.enabled=true        # Job 先跑 migration（pre-upgrade hook）
helm test liuhao                       # 通过后流量才切过来
```

---

## 最佳实践

1. **CI < 10 分钟**：超时先并行化/加缓存，再砍测试。慢 CI 是提交频率的最大杀手。
2. **制品只构建一次**：dev/staging/prod 用同一 digest 的镜像，差异全在配置。
3. **git 是部署的唯一事实源**（GitOps）：禁止 `kubectl apply` 直接改生产。
4. **签名 + 准入控制**：集群只允许运行 cosign 验签通过的镜像。
5. **回滚演练**：每月在生产真实回滚一次（选低峰期），保持回滚路径可信。
6. **变更可观测**：每次部署在 Grafana 打 annotation，故障时能立刻对上变更时间线。
7. **机密只在 CI 平台**：LLM API Key 等存 GitHub Secrets/Vault，永不入代码与镜像。

---

## 常见问题

### Q1: 没有 ArgoCD/Rollouts，能用原生 K8s 做金丝雀吗？

可以但简陋：两个 Deployment（stable/canary）+ Service  selector 切换，流量比例靠副本数近似（10 副本里 1 个 canary ≈ 10%）。没有自动分析，需人工盯指标。建议小规模先用 Helm 的 `--atomic` 全量发布 + 快速回滚，规模上来再引入 Rollouts。

### Q2: CI 集成测试太慢怎么办？

分层：单测（秒级，每次跑）→ 集成（分钟级，每次跑）→ e2e 全量（夜间跑 + 发布前跑）。测试本身优化：testcontainers 复用、并行 shard、砍掉重复覆盖场景的用例。

### Q3: 多组件版本如何协调？

LiuHao AI OS 各组件独立版本化，但 Helm Chart 锁定一组**经过联测的版本组合**（`appVersion` 矩阵）。生产只部署 Chart 锁定组合，禁止单独升级某组件跳版本。

### Q4: secrets 轮换需要重新部署吗？

用 ExternalSecrets + 自动重载：秘钥在 Vault 轮换后，External Secrets Operator 更新 K8s Secret，配合 reloader 注解触发滚动重启。应用侧也应支持热加载（见[密钥管理](../24-security/secrets-management.md)）。

### Q5: 如何防止"周五部署"事故？

制度化：周五 14:00 后冻结生产部署（CI 中加时间窗检查）；紧急修复走 hotfix 通道（简化审批但完整测试）。技术手段只能辅助，关键是值班与回滚能力可信。

---

**相关文档**：[Kubernetes 部署](kubernetes-deployment.md) · [Helm Charts](helm-charts.md) · [监控栈](monitoring-stack.md) · [测试策略](../best-practices/testing-strategies.md)
