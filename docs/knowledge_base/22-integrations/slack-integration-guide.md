# Slack 集成指南

通过 Slack 集成，团队可以在频道中直接与 LiuHao AI OS 智能体交互：下达任务、接收告警、审批决策、查询记忆。本文档覆盖 Slack App 创建、事件订阅、斜杠命令、交互组件（按钮/审批）、以及安全验证的完整流程。

---

## 目录

- [集成架构](#集成架构)
- [创建 Slack App](#创建-slack-app)
- [接收事件（Events API）](#接收事件events-api)
- [斜杠命令](#斜杠命令)
- [交互组件与审批流](#交互组件与审批流)
- [发送消息与 Block Kit](#发送消息与-block-kit)
- [签名验证与安全](#签名验证与安全)
- [配置示例](#配置示例)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 集成架构

```
Slack ──(HTTPS webhook)──▶ Slack Adapter (api-gateway 插件)
                              │  1. 验证签名
                              │  2. 3 秒内 ACK
                              │  3. 转内部事件
                              ▼
                         消息总线 (NATS)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        agent-runtime    policy-kernel    notify-service
        （执行任务）      （审批门禁）      （回写 Slack）
```

关键约束：**Slack 要求 webhook 在 3 秒内返回 200**，任何耗时处理都必须异步化——先 ACK，处理完后通过 `response_url` 或 Web API 回写结果。

---

## 创建 Slack App

1. 访问 https://api.slack.com/apps → **Create New App** → From scratch
2. 记录 **Basic Information** 页的 `Signing Secret`
3. **OAuth & Permissions** 添加 Bot Token Scopes：

| Scope | 用途 |
|-------|------|
| `chat:write` | 发送消息 |
| `commands` | 斜杠命令 |
| `app_mentions:read` | 接收 @机器人 提及 |
| `channels:history` | 读取频道消息（如需上下文） |
| `im:history` / `im:write` | 私聊 |
| `reactions:write` | 表情反馈（处理中 ⏳ / 完成 ✅） |

4. **Install to Workspace** → 获取 `xoxb-` Bot Token
5. 凭据存入密钥管理（见[密钥管理](../24-security/secrets-management.md)），**不要硬编码**

---

## 接收事件（Events API）

### 订阅配置

**Event Subscriptions** → Enable Events → Request URL 填：

```
https://api.liuhao.example.com/integrations/slack/events
```

订阅 Bot Events：`app_mention`、`message.im`。

### 处理 URL 验证与事件

```python
from fastapi import FastAPI, Request, HTTPException
import hmac, hashlib, time

app = FastAPI()

def verify_slack_signature(request: Request, body: bytes, signing_secret: str) -> None:
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    if abs(time.time() - int(ts)) > 300:            # 防重放：5 分钟窗口
        raise HTTPException(401, "stale timestamp")
    sig_basestring = f"v0:{ts}:{body.decode()}".encode()
    expected = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-Slack-Signature", "")):
        raise HTTPException(401, "invalid signature")

@app.post("/integrations/slack/events")
async def slack_events(request: Request):
    body = await request.body()
    verify_slack_signature(request, body, settings.slack_signing_secret)
    payload = await request.json()

    # 1. URL 验证挑战（一次性）
    if payload.get("type") == "url_verification":
        return {"challenge": payload["challenge"]}

    # 2. 事件去重（Slack 会重试未 ACK 的事件）
    event_id = payload.get("event_id")
    if await dedup.seen(event_id):
        return {"ok": True}
    await dedup.mark(event_id, ttl=3600)

    # 3. 异步派发，立即 ACK
    event = payload["event"]
    if event.get("type") == "app_mention" and not event.get("bot_id"):
        await bus.publish("slack.app_mention", {
            "channel": event["channel"],
            "user": event["user"],
            "text": strip_mention(event["text"]),
            "ts": event["ts"],
            "thread_ts": event.get("thread_ts", event["ts"]),
        })
    return {"ok": True}
```

### 智能体处理与回复

```python
@bus.subscribe("slack.app_mention")
async def handle_mention(event: dict) -> None:
    # 表情反馈：告知用户"已受理"
    await slack.reactions_add(channel=event["channel"],
                              timestamp=event["ts"], name="hourglass_flowing_sand")
    try:
        agent = await agent_registry.get_channel_agent(event["channel"])
        result = await agent.run(event["text"], context={
            "user": event["user"],
            "platform": "slack",
        })
        await slack.chat_postMessage(
            channel=event["channel"],
            thread_ts=event["thread_ts"],        # 在线程中回复，保持频道整洁
            text=result.summary,
            blocks=render_result_blocks(result),
        )
        await slack.reactions_add(channel=event["channel"],
                                  timestamp=event["ts"], name="white_check_mark")
    except Exception as exc:
        await slack.reactions_add(channel=event["channel"],
                                  timestamp=event["ts"], name="x")
        await slack.chat_postMessage(channel=event["channel"],
                                     thread_ts=event["thread_ts"],
                                     text=f"处理失败：{exc}")
```

---

## 斜杠命令

**Slash Commands** → Create New Command：

| 字段 | 值 |
|------|-----|
| Command | `/agent` |
| Request URL | `https://api.liuhao.example.com/integrations/slack/commands` |
| Short Description | 与智能体交互 |

处理器：

```python
@app.post("/integrations/slack/commands")
async def slack_command(request: Request):
    body = await request.body()
    verify_slack_signature(request, body, settings.slack_signing_secret)
    form = dict(parse_qsl(body.decode()))

    command_text = form["text"].strip()
    response_url = form["response_url"]        # 异步回写入口（30 分钟有效）

    if command_text.startswith("status"):
        # 快查询：可直接同步返回（< 3s）
        stats = await agent_registry.stats()
        return {"response_type": "ephemeral",   # 仅命令者可见
                "text": f"活跃智能体 {stats.active}，今日任务 {stats.tasks_today}"}

    # 慢任务：立即 ACK + 异步执行
    task_id = await tasks.submit(command_text, user=form["user_id"])
    asyncio.create_task(run_and_report(task_id, response_url))
    return {
        "response_type": "in_channel",
        "text": f"任务 `{task_id}` 已受理 ⏳ 完成后会在此通知。",
    }

async def run_and_report(task_id: str, response_url: str) -> None:
    result = await tasks.wait(task_id, timeout=1800)
    async with httpx.AsyncClient() as client:
        await client.post(response_url, json={
            "replace_original": True,
            "text": f"任务 `{task_id}` 完成 ✅\n{result.summary}",
        })
```

---

## 交互组件与审批流

Slack 集成的杀手锏是把**人类主权审批**嵌入消息：

```python
def render_approval_blocks(task: dict, policy: dict) -> list[dict]:
    return [
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"⚠️ 任务 `{task['id']}` 触发策略 *{policy['name']}*\n"
                          f"操作：{task['action']}\n风险等级：{policy['risk_level']}"}},
        {"type": "actions",
         "block_id": f"approval_{task['id']}",
         "elements": [
             {"type": "button",
              "text": {"type": "plain_text", "text": "✅ 批准"},
              "style": "primary",
              "action_id": "approve",
              "value": task["id"]},
             {"type": "button",
              "text": {"type": "plain_text", "text": "❌ 拒绝"},
              "style": "danger",
              "action_id": "reject",
              "value": task["id"]},
             {"type": "button",
              "text": {"type": "plain_text", "text": "🔍 详情"},
              "action_id": "detail",
              "value": task["id"]},
         ]},
    ]
```

交互回调（**Interactivity** → Request URL：`/integrations/slack/interactions`）：

```python
@app.post("/integrations/slack/interactions")
async def slack_interactions(request: Request):
    body = await request.body()
    verify_slack_signature(request, body, settings.slack_signing_secret)
    payload = json.loads(dict(parse_qsl(body.decode()))["payload"])

    action = payload["actions"][0]
    task_id = action["value"]
    approver = payload["user"]["id"]

    # 权限校验：只有指定角色可审批
    if not await authz.can_approve(approver, task_id):
        return {"response_action": "errors",
                "errors": {"approval": "您没有该任务的审批权限"}}

    if action["action_id"] == "approve":
        await tasks.approve(task_id, approver=approver, channel="slack")
        verdict = "✅ 已批准"
    elif action["action_id"] == "reject":
        await tasks.reject(task_id, approver=approver, channel="slack")
        verdict = "❌ 已拒绝"
    else:
        detail_url = f"{settings.console_url}/tasks/{task_id}"
        return {"response_type": "ephemeral", "text": f"详情：{detail_url}"}

    # 更新原消息：移除按钮，留下审批记录（设计为不可篡改；审计链运行时完整性当前 UNVERIFIED，见 HC-01）
    return {
        "replace_original": True,
        "text": f"任务 `{task_id}` {verdict}（审批人：<@{approver}>）",
    }
```

审批结果连同 `approver`、时间戳写入审计流（见[审计日志](../24-security/audit-logging.md)），这是主权内核"关键操作人类确认"的落地。

---

## 发送消息与 Block Kit

常用 Block Kit 模式：

```python
# 任务进度卡片
blocks = [
    {"type": "header",
     "text": {"type": "plain_text", "text": f"📋 任务 {task_id}"}},
    {"type": "section",
     "fields": [
         {"type": "mrkdwn", "text": f"*状态*\n{status_emoji} {status}"},
         {"type": "mrkdwn", "text": f"*智能体*\n{agent_name}"},
         {"type": "mrkdwn", "text": f"*已耗时*\n{elapsed}"},
         {"type": "mrkdwn", "text": f"*LLM 成本*\n${cost:.4f}"},
     ]},
    {"type": "divider"},
    {"type": "context",
     "elements": [{"type": "mrkdwn",
                   "text": f"trace: `{trace_id}` | 由 LiuHao AI OS 驱动"}]},
]
```

要点：

- 长内容用线程（`thread_ts`），避免刷屏
- `unfurl_links: false` 防止链接预览轰炸
- 消息更新用 `chat.update` 而非发新消息（进度场景）

---

## 签名验证与安全

必备四道防线：

1. **签名验证**（上文 `verify_slack_signature`）：HMAC-SHA256 + 5 分钟时间窗防重放
2. **事件去重**：`event_id` 幂等，Slack 重试不产生重复任务
3. **权限映射**：Slack user_id → LiuHao 账号，未绑定用户只能使用只读命令
4. **频道白名单**：敏感操作（审批、配额调整）限定在私有频道

频道到智能体的映射配置见下节。

---

## 配置示例

```yaml
# config/integrations/slack.yaml
slack:
  enabled: true
  signing_secret: ${SLACK_SIGNING_SECRET}       # 从密钥管理注入
  bot_token: ${SLACK_BOT_TOKEN}

  request:
    signature_max_age_s: 300
    ack_timeout_ms: 2500                        # 必须 < 3000

  channels:
    "#ai-research":
      agent: researcher-01
      permissions: [task_submit, memory_query]
    "#ai-ops":
      agent: ops-assistant
      permissions: [task_submit, approve, quota_manage]   # 审批频道
      approval_policy: require_ops_role

  defaults:
    reply_in_thread: true
    progress_emoji: hourglass_flowing_sand
    done_emoji: white_check_mark
    fail_emoji: x
    unfurl_links: false

  rate_limit:
    per_user_per_minute: 20
    per_channel_per_minute: 60

  alerts:                                       # 告警路由（Alertmanager 对接）
    critical: "#ai-ops"
    warning: "#ai-ops-digest"
```

---

## 最佳实践

1. **一切异步**：webhook 只做验证 + 入队 + ACK，处理结果走 `response_url` 或 Web API 回写。
2. **线程化回复**：一个任务一个线程，频道保持可读。
3. **表情即状态机**：⏳ 受理 → ✅ 完成 / ❌ 失败，零成本进度反馈。
4. **审批消息更新而非删除**：批准后替换按钮为文字记录，保留审计痕迹。
5. **DM 与频道分离**：默认配置只监听提及（app_mention），不读全频道历史（最小权限）。
6. **失败也要回写**：任务失败必须在原线程告知原因，否则用户会重复提交。
7. **密钥轮换演练**：Slack token 泄露时，在 api.slack.com 一键轮换 + 更新密钥管理中的值。

---

## 常见问题

### Q1: 事件收到两次，任务执行了两遍？

Slack 未收到 3 秒内 ACK 会重试，且 `app_mention` 与可能的 `message` 事件会重复订阅。两道防线：严格 3 秒内 ACK；`event_id` 去重（TTL ≥ 1h）。

### Q2: 回复消息提示 `channel_not_found`？

Bot 未被邀请进频道：`/invite @YourBot`。私有频道必须显式邀请。

### Q3: response_url 过期了？

`response_url` 30 分钟内有效、最多用 5 次。长任务（>30min）完成后改用 `chat.postMessage` 发新消息并 @提交者。

### Q4: 如何区分多个工作区（Enterprise Grid）？

payload 中的 `team_id` 标识工作区，`enterprise_id` 标识组织。多工作区部署需按 `team_id` 路由到对应的 token 与配置。

### Q5: 大文件/长报告怎么发？

>3000 字符的消息可读性差。方案：`files.upload` 传 PDF/Markdown 文件，或发摘要 + 控制台详情链接。Block Kit 单消息上限约 50 blocks / 400KB。

---

**相关文档**：[GitHub 集成](github-integration-guide.md) · [自定义集成](custom-integration-guide.md) · [审计日志](../24-security/audit-logging.md)
