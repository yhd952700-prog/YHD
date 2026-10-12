# 密钥管理

LLM API Key、数据库口令、JWT 签名密钥、Webhook Secret——智能体系统的密钥数量多、权限大、泄露后果重。本文档覆盖密钥生命周期管理：存储、注入、轮换、访问审计与泄露应急响应。

---

## 目录

- [密钥分类与分级](#密钥分类与分级)
- [存储方案选型](#存储方案选型)
- [应用注入模式](#应用注入模式)
- [K8s 集成：External Secrets](#k8s-集成external-secrets)
- [密钥轮换](#密钥轮换)
- [访问审计与告警](#访问审计与告警)
- [泄露应急响应](#泄露应急响应)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 密钥分类与分级

| 级别 | 示例 | 泄露影响 | 管理要求 |
|------|------|---------|---------|
| L3 核心 | 数据库主口令、JWT 签名私钥、加密主密钥（KEK） | 全系统沦陷 | HSM/KMS 托管、双人授权、90 天轮换 |
| L2 服务 | LLM API Key、Redis 口令、Webhook Secret | 单服务被冒用 | 密钥管理器、180 天轮换 |
| L1 终端 | 用户 API Key、Webhook 签名密钥（租户级） | 单租户受损 | 哈希存储、自助轮换 |

核心原则：

1. **任何密钥不出现在**：代码、git 历史、配置文件明文、镜像层、日志、错误消息
2. **密钥的密钥（KEK）不进应用**：用 KMS/HSM 信封加密，应用只拿数据密钥
3. **谁用了密钥和谁用了数据一样重要**：访问必须审计

---

## 存储方案选型

| 方案 | 适用 | 优点 | 缺点 |
|------|------|------|------|
| HashiCorp Vault | 自建、多云 | 动态密钥、细粒度 ACL、审计完善 | 运维成本高 |
| 云 KMS/Secrets Manager（AWS/Azure/GCP） | 单云 | 免运维、IAM 集成 | 厂商绑定 |
| Sealed Secrets（K8s） | 小团队 K8s | git 可存密文、简单 | 无轮换/审计 |
| SOPS + age | 小团队 Compose/裸机 | 文件级加密、git 友好 | 手动分发 |

LiuHao AI OS 推荐：**生产用 Vault 或云 Secrets Manager + External Secrets Operator；开发用 SOPS**。

---

## 应用注入模式

### 模式对比

| 模式 | 说明 | 安全性 | 推荐度 |
|------|------|--------|--------|
| 环境变量 | 进程启动时注入 | 中（`/proc/*/environ` 可读、易进 crash dump） | 可接受（L2 以下） |
| 挂载文件 | tmpfs 卷写入 | 高（不落盘、可轮换重载） | ✅ 推荐 |
| 运行时拉取 | 应用直连 Vault 拉取 | 最高（内存中存活） | L3 密钥推荐 |
| Sidecar 代理 | Vault Agent 注入并自动续期 | 高 | ✅ K8s 推荐 |

### 运行时拉取示例（hvac）

```python
import hvac

class SecretProvider:
    def __init__(self):
        self._client = hvac.Client(
            url=settings.vault_addr,
            token=None)
        self._client.auth.kubernetes.authenticate(
            role="liuhao-app",
            jwt=open("/var/run/secrets/kubernetes.io/serviceaccount/token").read())
        self._cache: dict[str, tuple[str, float]] = {}

    def get(self, path: str, key: str, ttl_s: int = 300) -> str:
        ck = f"{path}:{key}"
        if ck in self._cache and self._cache[ck][1] > time.time():
            return self._cache[ck][0]
        resp = self._client.secrets.kv.v2.read_secret_version(path=path)
        value = resp["data"]["data"][key]
        self._cache[ck] = (value, time.time() + ttl_s)
        SECRET_ACCESS.labels(path=path).inc()     # 审计指标
        return value

# 使用：LLM key 每 5 分钟从 Vault 刷新，轮换无需重启
secrets = SecretProvider()
llm_key = secrets.get("liuhao/llm", "provider_api_key")
```

### 防泄露编码规范

```python
class Secret(str):
    """防误打印的密钥包装类型。"""
    def __repr__(self) -> str: return "Secret(***)"
    def __str__(self) -> str: return "***"        # str() 也遮蔽
    def reveal(self) -> str:
        return super().__str__()                 # 唯一取明文入口

# 日志过滤器：兜底拦截
class SecretRedactionFilter(logging.Filter):
    PATTERNS = [re.compile(r"sk-[a-zA-Z0-9]{20,}"),
                re.compile(r"lh_[a-zA-Z0-9]{32,}")]
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in self.PATTERNS:
            if p.search(msg):
                record.msg = p.sub("***REDACTED***", msg)
                record.args = ()
        return True
```

---

## K8s 集成：External Secrets

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata: { name: vault }
spec:
  provider:
    vault:
      server: https://vault.example.com
      path: liuhao
      auth:
        kubernetes:
          mountPath: kubernetes
          role: liuhao-app
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata: { name: liuhao-secrets, namespace: liuhao }
spec:
  refreshInterval: 1h                  # Vault 轮换后 1h 内同步到集群
  secretStoreRef: { name: vault, kind: ClusterSecretStore }
  target:
    name: liuhao-secrets
  data:
    - secretKey: POSTGRES_PASSWORD
      remoteRef: { key: liuhao/db, property: password }
    - secretKey: LLM_PROVIDER_API_KEY
      remoteRef: { key: liuhao/llm, property: api_key }
```

配合 [Reloader](https://github.com/stakater/Reloader) 在 Secret 更新后自动滚动重启：

```yaml
metadata:
  annotations:
    reloader.stakater.com/auto: "true"
```

---

## 密钥轮换

### JWT 签名密钥轮换（零停机）

```python
# 双密钥过渡：新签旧验 → 全部过期 → 撤旧
class KeyRing:
    def __init__(self):
        self.signing_kid = "key-2026-10"        # 当前签名钥
        self.verification_keys = {
            "key-2026-10": load_public("key-2026-10"),
            "key-2026-07": load_public("key-2026-07"),   # 旧钥只验不签
        }

    def sign(self, claims: dict) -> str:
        return jwt.encode(claims, self._signing_key,
                          algorithm="EdDSA",
                          headers={"kid": self.signing_kid})

    def verify(self, token: str) -> dict:
        kid = jwt.get_unverified_header(token)["kid"]
        key = self.verification_keys.get(kid)
        if key is None:
            raise InvalidKeyError(kid)
        return jwt.decode(token, key, algorithms=["EdDSA"],
                          audience="liuhao-api")

# 轮换窗口 = access token TTL（15min）+ 缓冲；refresh token 用另一套密钥
```

### LLM API Key 轮换

```python
# 提供商支持多 key 并存：新 key 生效 → 观察 24h → 撤销旧 key
async def rotate_llm_key(new_key: str) -> None:
    await vault.write("liuhao/llm", api_key=new_key)
    await invalidate_secret_cache("liuhao/llm:api_key")
    await wait_and_verify(no_errors_with_new_key, hours=24)
    await provider_console.revoke(old_key)
```

### 用户 API Key 设计

```
lh_<prefix8>_<secret32>

存储：argon2id(secret32) + prefix8 明文（用于定位）
验证：按 prefix 查行 → argon2id.verify
```

- 前缀定位 + 哈希验证，数据库泄露不出可用密钥
- 创建时只展示一次明文
- 支持多 key 并存自助轮换

---

## 访问审计与告警

每次密钥读取记录：who（服务账号/用户）、what（路径）、when、from（Pod/IP）。Vault 自带审计设备：

```hcl
# vault 审计配置
audit "file" {
  path = "/vault/logs/audit.log"
}
```

告警规则：

| 事件 | 阈值 | 级别 |
|------|------|------|
| 非工作时段读 L3 密钥 | 任何一次 | critical |
| 单服务账号 1min 内读 > 20 个不同密钥 | 突增 | critical |
| 密钥读取失败（权限拒绝） | 5 次/5min | warning |
| 密钥超期未轮换 | 到期前 7 天 | warning |

---

## 泄露应急响应

预案（runbook）：

```
1. 确认范围：泄露了什么？（git 历史 / 日志 / 截图 / 聊天）
2. 立即吊销：提供商控制台撤销该 key（优先于一切调查）
3. 轮换同路径所有密钥（泄露事件常伴生其他暴露）
4. 审计回溯：该 key 过去 90 天的使用记录，排查异常调用
   （LLM 提供商用量曲线突增 = 被盗用实锤）
5. 清除泄露点：git filter-repo / BFG 重写历史；删除日志
6. 复盘：如何泄露的？补哪道防线（pre-commit？扫描规则？）
```

> git 历史清除后必须 force-push 并通知全员 re-clone；已 fork/clone 的副本无法召回——**所以第 2 步吊销永远优先于第 5 步清理**。

---

## 配置示例

```yaml
# config/secrets.yaml
secrets:
  provider: vault                    # vault | aws_sm | sops | env(仅dev)
  vault:
    addr: https://vault.example.com
    auth: kubernetes
    role: liuhao-app
    cache_ttl_s: 300

  paths:
    postgres_password: liuhao/db#password
    jwt_signing_key:   liuhao/jwt#signing_key
    llm_api_key:       liuhao/llm#provider_api_key

  rotation:
    jwt_signing_key_days: 90
    llm_api_key_days: 180
    postgres_password_days: 90
    auto_notify_days_before: 7

  user_api_keys:
    hash: argon2id
    prefix_len: 8
    max_per_user: 5
    rotation_days: 90

  redaction:
    log_filter: true
    patterns: ["sk-*", "lh_*", "xoxb-*", "ghp_*", "-----BEGIN*"]
```

---

## 最佳实践

1. **密钥即代码引用，非代码内容**：配置里写 `path: liuhao/llm#api_key`，运行时解析，git 中永无明文。
2. **轮换必须常态化演练**：没轮换过的密钥等于不能轮换；每季度至少真实轮换一次全部 L2+ 密钥。
3. **动态密钥优先**：Vault 动态数据库凭据（每次签发短 TTL 专属账号）消灭长期口令。
4. **明文只在内存，用完即弃**：密钥变量作用域最小化，避免传入日志/异常/上下文对象。
5. **CI 变量遮蔽 + 掩码**：CI 平台密钥设置 masked/protected，禁止 echo。
6. **密钥清单定期对账**：代码引用的密钥路径 ↔ Vault 中存在的路径 ↔ 实际使用的，三者一致。

---

## 常见问题

### Q1: 开发环境也要 Vault 吗？

不必。开发用 SOPS/age 加密的 `.env.sops` 或开发者个人 Vault namespace。原则是**开发密钥与生产密钥完全隔离**——开发用的 LLM key 是低额度的独立 key。

### Q2: 密钥进了容器环境变量，exec 进去 `env` 就能看到，怎么办？

接受它是 L2 以下密钥的合理风险（能 exec 进 Pod 的人已有更大权限）。L3 密钥用运行时拉取模式，环境不可见。同时限制 exec 权限（RBAC `pods/exec` 仅值班组）。

### Q3: 密钥轮换代价最高的部分是什么？

不是技术，是**依赖方协调**：Webhook Secret 轮换要通知所有调用方更新。设计阶段就约定：签名验证支持双 secret 并存窗口期，轮换流程文档化。

### Q4: 忘记 Vault root token / unseal key 怎么办？

预防胜于治疗：unseal key Shamir 分片多人保管（5 取 3）；root token 生成后立即吊销，日常用 ACL token。真丢了走 Vault 灾难恢复流程（rekey），前提是有备份。

### Q5: 如何防止"日志里打出 Authorization 头"？

三层：框架层中间件统一脱敏请求日志；`SecretRedactionFilter` 兜底正则拦截；CI 静态检查禁止 `print(headers)` 类代码进生产路径。

---

**相关文档**：[安全加固](security-hardening.md) · [审计日志](audit-logging.md) · [Kubernetes 部署](../21-deployment/kubernetes-deployment.md)
