# 自定义集成开发指南

当官方集成（Slack/GitHub/Jira）不满足需求时，你可以基于 LiuHao AI OS 的**集成框架**开发自定义集成：对接内部 ERP、企业微信、自研 DevOps 平台或任何提供 API/Webhook 的系统。本文档介绍集成的标准结构、插件 SDK、安全规范与发布流程。

---

## 目录

- [集成框架概览](#集成框架概览)
- [三种集成形态](#三种集成形态)
- [Webhook 接收型集成](#webhook-接收型集成)
- [轮询型集成](#轮询型集成)
- [出站动作型集成](#出站动作型集成)
- [插件打包与注册](#插件打包与注册)
- [安全规范](#安全规范)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 集成框架概览

所有集成（官方与自定义）遵循统一架构：

```
外部系统 ◀──▶ Integration Plugin（适配器）
                  │  标准接口
                  ▼
        ┌─────────────────────┐
        │  Integration Kernel │
        │  · 事件规范化        │
        │  · 签名/认证验证     │
        │  · 幂等与去重        │
        │  · 速率限制          │
        │  · 审计记录          │
        └──────────┬──────────┘
                   ▼
              消息总线（NATS）
                   ▼
        智能体 / 内核 / 其他集成
```

插件只负责**协议适配**（外部世界的方言 ↔ 内部标准事件），认证、去重、限流、审计由集成内核统一提供——这保证所有集成行为可统一治理。

### 标准事件信封

```python
@dataclass
class IntegrationEvent:
    event_id: str                 # 幂等键，必填
    source: str                   # 集成名，如 "wecom"
    event_type: str               # 点分命名，如 "wecom.message.received"
    occurred_at: float
    tenant_id: str
    payload: dict                 # 规范化后的业务数据
    raw: dict                     # 原始报文（审计用，入审计流）
    external_user: str | None     # 外部用户标识
    correlation_id: str | None    # 关联链
```

---

## 三种集成形态

| 形态 | 触发方向 | 典型系统 | 实现要点 |
|------|---------|---------|---------|
| Webhook 接收型 | 外部 → LiuHao | Slack、GitHub、企业微信回调 | 签名验证、快速 ACK |
| 轮询型 | LiuHao 定期拉取 | RSS、老 ERP、邮件（IMAP） | 水位线、增量拉取 |
| 出站动作型 | LiuHao → 外部 | 发通知、调内部 API | 重试、熔断、幂等 |

一个集成插件可同时实现多种形态（如 Slack 集成既是接收型也是出站型）。

---

## Webhook 接收型集成

以对接企业微信为例，实现 `WebhookReceiver` 协议：

```python
from liuhao.integrations.sdk import (
    IntegrationPlugin, WebhookReceiver, IntegrationEvent,
    verify_hmac_signature, new_event_id,
)

class WeComPlugin(IntegrationPlugin):
    name = "wecom"
    version = "1.0.0"

    def configure(self, config: dict) -> None:
        self._token = config["callback_token"]
        self._encoding_aes_key = config["encoding_aes_key"]

    def receivers(self) -> list[WebhookReceiver]:
        return [WeComMessageReceiver(self)]


class WeComMessageReceiver(WebhookReceiver):
    path = "/integrations/wecom/callback"        # 注册到 api-gateway
    methods = ["GET", "POST"]

    async def handle_get(self, query: dict) -> str:
        # 企业微信 URL 验证：解密 echostr 原样返回
        return decrypt_echostr(query["echostr"], self.plugin._encoding_aes_key)

    async def handle_post(self, headers: dict, body: bytes) -> list[IntegrationEvent]:
        # 1. 验证签名（SDK 提供通用 HMAC 工具）
        verify_hmac_signature(
            secret=self.plugin._token,
            body=body,
            signature=headers.get("x-signature", ""),
            timestamp=headers.get("x-timestamp", ""),
            max_age_s=300,
        )
        # 2. 解密（企业微信特有逻辑）
        message = decrypt_wecom_message(body, self.plugin._encoding_aes_key)

        # 3. 规范化为标准事件
        return [IntegrationEvent(
            event_id=new_event_id("wecom", message["MsgId"]),
            source="wecom",
            event_type=f"wecom.message.{message['MsgType']}",
            occurred_at=message["CreateTime"],
            tenant_id=self.plugin.tenant_id,
            payload={
                "from_user": message["FromUserName"],
                "content": message.get("Content", ""),
                "chat_id": message.get("ChatId"),
            },
            raw=message,
            external_user=message["FromUserName"],
        )]
        # 集成内核负责：幂等去重 → 审计落库 → 发布到总线
```

订阅事件：

```python
@bus.subscribe("wecom.message.text")
async def handle_wecom_text(event: dict) -> None:
    reply = await router_agent.run(event["payload"]["content"],
                                   user=event["external_user"])
    await outbound.send("wecom", action="send_message", params={
        "to_user": event["external_user"], "content": reply.summary})
```

---

## 轮询型集成

实现 `Poller` 协议，框架负责调度与水位线持久化：

```python
from liuhao.integrations.sdk import Poller, IntegrationEvent

class ErpOrderPoller(Poller):
    schedule = "*/2 * * * *"               # 每 2 分钟
    watermark_key = "erp.orders.updated_at"

    async def poll(self, watermark: float | None) -> tuple[list[IntegrationEvent], float]:
        """返回 (事件列表, 新水位线)。框架持久化水位线，重启不重复。"""
        since = watermark or (time.time() - 3600)   # 首次拉最近 1 小时
        orders = await erp_api.list_orders_updated_since(since, page_size=200)

        events = []
        max_ts = since
        for order in orders:
            events.append(IntegrationEvent(
                event_id=new_event_id("erp", f"order-{order['id']}-{order['updated_at']}"),
                source="erp",
                event_type="erp.order.updated",
                occurred_at=order["updated_at"],
                tenant_id=self.tenant_id,
                payload=normalize_order(order),
                raw=order,
            ))
            max_ts = max(max_ts, order["updated_at"])
        return events, max_ts
```

水位线设计要点：

- **事件 ID 含业务键 + 更新时间**：同一订单多次更新是不同事件；同一时刻重复拉取是同事件（幂等）
- **重叠窗口**：`since = watermark - 60s`，容忍下游时钟回拨与事务可见性延迟，靠事件 ID 去重兜底
- **分页**：大结果集逐页拉，单页失败只重试单页

---

## 出站动作型集成

封装外部 API 为可供智能体调用的**动作（Action）**：

```python
from liuhao.integrations.sdk import OutboundAdapter, action

class ErpOutboundAdapter(OutboundAdapter):
    name = "erp"

    @action(
        description="在 ERP 中创建采购申请",
        params_schema={
            "type": "object",
            "properties": {
                "item_code": {"type": "string"},
                "quantity":  {"type": "integer", "minimum": 1},
                "reason":    {"type": "string", "maxLength": 500},
            },
            "required": ["item_code", "quantity", "reason"],
        },
        risk_level="high",              # 高风险：触发策略审批
        idempotency="client_key",       # 客户端生成幂等键
    )
    async def create_purchase_request(self, params: dict, ctx: ActionContext) -> dict:
        # 框架已保证：参数校验 ✓ 策略审批 ✓ 速率限制 ✓ 审计 ✓
        resp = await self._http.post(
            "/api/v1/purchase-requests",
            json={**params, "requester": ctx.external_user,
                  "idempotency_key": ctx.idempotency_key},
            timeout=10.0)
        resp.raise_for_status()
        return {"request_id": resp.json()["id"], "status": "submitted"}
```

`risk_level` 决定治理强度：

| 级别 | 行为 |
|------|------|
| `low` | 直接执行（查询类） |
| `medium` | 执行 + 审计 |
| `high` | 需人类审批后执行（见 [Slack 审批流](slack-integration-guide.md)） |
| `critical` | 双人审批 + 完整审计 + 执行录像 |

---

## 插件打包与注册

### 目录结构

```
plugins/wecom-integration/
├── plugin.yaml               # 元数据
├── pyproject.toml
├── src/
│   └── wecom_integration/
│       ├── __init__.py
│       ├── plugin.py         # IntegrationPlugin 入口
│       ├── receiver.py
│       └── outbound.py
└── tests/
    └── test_receiver.py
```

### plugin.yaml

```yaml
name: wecom-integration
version: 1.0.0
description: 企业微信消息与审批集成
author: platform-team
entrypoint: wecom_integration.plugin:WeComPlugin
capabilities:
  receivers: [wecom.message.*]
  actions: [wecom.send_message, wecom.send_approval]
config_schema:
  type: object
  properties:
    callback_token: { type: string, secret: true }
    encoding_aes_key: { type: string, secret: true }
    corp_id: { type: string }
  required: [callback_token, encoding_aes_key, corp_id]
permissions:                    # 插件权限声明（安装时管理员审批）
  - bus.publish
  - http.egress: ["qyapi.weixin.qq.com"]
```

### 安装与生命周期

```bash
# 本地开发模式（热重载）
liuhao plugin install ./plugins/wecom-integration --dev

# 生产安装
liuhao plugin install registry.example.com/plugins/wecom-integration-1.0.0.tar.gz

# 配置（secret 值写入密钥管理，不落盘）
liuhao plugin config wecom-integration \
  --set corp_id=ww123456 \
  --set-secret callback_token \
  --set-secret encoding_aes_key

# 启停与状态
liuhao plugin enable wecom-integration
liuhao plugin status wecom-integration --events-tail 20
```

---

## 安全规范

自定义集成是攻击面的主要来源，强制规范：

1. **签名校验强制**：无签名机制的 webhook（如 Jira）必须配置 IP 白名单 + 随机路径
2. **`secret: true` 配置项**：由框架注入密钥管理引用，插件代码拿不到明文环境变量之外的持久化副本
3. **出站白名单**：`permissions.http.egress` 声明可达域名，其余出向流量被 egress 网关拒绝
4. **原始报文入审计**：`raw` 字段保留 90 天，争议时可回放
5. **输入不可信**：所有外部 payload 进入智能体前视为不可信内容，防提示注入（见[安全加固](../24-security/security-hardening.md)）
6. **速率限制**：per-source 默认 60 req/min，超限 429

---

## 配置示例

```yaml
# config/integrations/framework.yaml
integration_framework:
  dedup:
    store: redis
    ttl_s: 86400                    # 事件 ID 去重窗口
  audit:
    raw_payload_retention_days: 90
    include_payload: true
  rate_limits:
    default_per_source_per_minute: 60
    overrides:
      github: 500
      erp: 30
  egress:
    default_policy: deny            # 白名单制
    allowed:
      wecom-integration: ["qyapi.weixin.qq.com"]
      erp-integration: ["erp.internal.company.com"]
  pollers:
    max_concurrent: 4
    failure_backoff_s: [60, 300, 900]   # 连续失败退避
```

---

## 最佳实践

1. **适配器薄、内核厚**：插件只做协议转换；去重、限流、审计全交给框架，保证治理一致。
2. **事件 ID 设计先行**：稳定的幂等键是一切可靠性的基础，用"来源 + 业务主键 + 版本/时间戳"。
3. **先观测后动作**：新集成第一版只接收事件并上报指标，验证稳定后再开放出站动作。
4. **失败可见**：接收失败、动作失败都要上指标（`integration_events_total{source,result}`），接入告警。
5. **契约测试**：为外部 API 录制 fixture（vcrpy），CI 中跑契约测试，外部系统升级时及早发现。
6. **灰度开放**：新动作先 `risk_level` 提一级（high→critical），稳定后降级。
7. **文档化事件目录**：在 plugin.yaml 的 `capabilities` 完整声明收发事件，方便他人订阅。

---

## 常见问题

### Q1: 外部系统没有 webhook 也没有增量查询接口，怎么集成？

最后手段是定时全量拉取 + 本地 diff（对快照哈希比对产生变更事件）。数据量大时考虑数据库 CDC（Debezium 直连其库）绕过应用层。

### Q2: 插件崩溃会影响主系统吗？

不会。插件运行在独立进程（插件运行时），与内核通过 gRPC 通信；崩溃由 supervisor 重启，事件在总线中积压不丢失。反复崩溃会触发熔断并告警。

### Q3: 如何调试"事件没进来"？

排查顺序：① `liuhao plugin status` 看接收计数；② 集成内核审计流查 `raw` 是否落库（验证签名没过的话这里没有）；③ 去重存储里事件 ID 是否已存在（重复事件会被静默跳过——查 `integration_events_total{result="duplicate"}`）。

### Q4: 出站动作执行了一半外部系统超时，到底成没成？

这就是幂等键存在的意义：动作用 `ctx.idempotency_key` 重试，外部系统凭幂等键去重。若外部系统不支持幂等，`risk_level` 至少设为 high，失败后人工核对而非自动重试。

### Q5: 能否用其他语言写插件？

可以。插件 SDK 有 Python 与 TypeScript 两个官方实现；任何语言只要实现插件 gRPC 协议（proto 文件在 `sdk/proto/integration.proto`）即可，社区已有 Go 与 Rust 的非官方实现。

---

**相关文档**：[Slack 集成](slack-integration-guide.md) · [GitHub 集成](github-integration-guide.md) · [安全加固](../24-security/security-hardening.md) · [审计日志](../24-security/audit-logging.md)
