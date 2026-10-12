# Jira 集成指南

Jira 集成让 LiuHao AI OS 智能体参与项目管理流程：自动建单、工单分类与优先级建议、状态同步、Sprint 报告生成、需求文档问答。本文档覆盖 Jira Cloud API 认证、Webhook、双向同步与常见自动化场景。Jira Data Center（自建版）的差异在最后说明。

---

## 目录

- [集成架构](#集成架构)
- [认证方式](#认证方式)
- [Jira REST API 基础](#jira-rest-api-基础)
- [Webhook 接收](#webhook-接收)
- [常见自动化场景](#常见自动化场景)
- [双向状态同步](#双向状态同步)
- [配置示例](#配置示例)
- [Jira Data Center 差异](#jira-data-center-差异)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 集成架构

```
Jira Cloud ──(webhook)──▶ Jira Adapter
   ▲                        │
   │ REST API v3            ▼
   │                     消息总线
   │                        │
   │            ┌───────────┼────────────┐
   │            ▼           ▼            ▼
   │      triager agent  reporter   task-sync service
   │      （分类/估点）  （报告生成） （状态双向同步）
   └────────────────────────────────────┘
```

集成模式两种，按需选用：

| 模式 | 说明 | 适用 |
|------|------|------|
| Webhook 驱动 | Jira 事件实时推送到 LiuHao | 自动化响应（推荐） |
| 轮询驱动 | 定时调 API 拉取变更 | Webhook 不可达的内网环境 |

---

## 认证方式

### 方式一：API Token（简单场景）

Jira Cloud：用户邮箱 + API Token 做 Basic Auth。

```python
import httpx

client = httpx.AsyncClient(
    base_url="https://your-domain.atlassian.net/rest/api/3",
    auth=(settings.jira_email, settings.jira_api_token),   # Basic Auth
    headers={"Accept": "application/json"},
    timeout=30.0,
)
```

Token 获取：Atlassian Account → Security → **Create and manage API tokens**。

> ⚠️ API Token 挂在个人账号下，权限即该用户权限。建议创建专用机器人账号（`liuhao-bot@your-domain.com`），按需授权项目。

### 方式二：OAuth 2.0 (3LO) App（多租户/市场应用）

面向最终用户授权的场景，走标准 OAuth 三方授权流，获取 `access_token` + `refresh_token`。本文档以 API Token 方式为主，OAuth 细节参考 Atlassian 官方文档。

---

## Jira REST API 基础

### 关键端点

| 操作 | 方法 & 路径 |
|------|------------|
| 查询工单（JQL） | `GET /search/jql?jql=...` |
| 获取工单 | `GET /issue/{issueKey}` |
| 创建工单 | `POST /issue` |
| 更新字段 | `PUT /issue/{issueKey}` |
| 状态流转 | `POST /issue/{issueKey}/transitions` |
| 评论 | `POST /issue/{issueKey}/comment` |
| 项目元数据 | `GET /project/{key}` |

### JQL 查询示例

```python
async def search_issues(jql: str, max_results: int = 50) -> list[dict]:
    resp = await client.get("/search/jql", params={
        "jql": jql,
        "maxResults": max_results,
        "fields": "summary,status,priority,assignee,labels,created,updated",
    })
    resp.raise_for_status()
    return resp.json()["issues"]

# 常用 JQL
open_bugs = await search_issues(
    'project = PLAT AND type = Bug AND status != Done ORDER BY priority DESC')
stale_issues = await search_issues(
    'project = PLAT AND status = "In Progress" AND updated < -7d')
```

### ADF（Atlassian Document Format）

描述与评论使用 ADF 而非纯文本/Markdown。最简段落：

```python
def text_to_adf(text: str) -> dict:
    """纯文本转最小 ADF（每行一个段落）。"""
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "paragraph",
             "content": [{"type": "text", "text": line} if line else {"type": "hardBreak"}]}
            for line in text.split("\n")
        ],
    }

await client.post(f"/issue/{issue_key}/comment",
                  json={"body": text_to_adf("智能体分析结果：...")})
```

---

## Webhook 接收

### 注册 Webhook

```python
async def register_webhook(target_url: str, jql_filter: str) -> dict:
    resp = await client.post("/webhook", json={
        "url": target_url,
        "webhooks": [
            {"jqlFilter": jql_filter,
             "events": ["jira:issue_created", "jira:issue_updated",
                        "comment_created"]}
        ],
    })
    resp.raise_for_status()
    return resp.json()
```

### 处理事件

```python
@app.post("/integrations/jira/webhooks")
async def jira_webhook(request: Request):
    # Jira webhook 无签名！用来源 IP 白名单 + URL 随机路径防护
    client_ip = request.client.host
    if not await ip_allowlist.is_atlassian(client_ip):
        raise HTTPException(403)

    payload = await request.json()
    event = payload.get("webhookEvent")
    issue = payload.get("issue", {})

    if event == "jira:issue_created":
        await bus.publish("jira.issue_created", {
            "key": issue["key"],
            "project": issue["fields"]["project"]["key"],
            "summary": issue["fields"]["summary"],
            "issuetype": issue["fields"]["issuetype"]["name"],
            "reporter": issue["fields"]["reporter"]["accountId"],
        })
    elif event == "jira:issue_updated":
        changelog = payload.get("changelog", {})
        await bus.publish("jira.issue_updated", {
            "key": issue["key"],
            "changed_fields": [i["field"] for i in changelog.get("items", [])],
        })
    return {"ok": True}
```

> ⚠️ Jira webhook 会触发于**包括本集成自己发起的变更**。必须用 changelog 中的 `accountId` 排除机器人账号，否则会造成"改单 → webhook → 再改单"的死循环。

---

## 常见自动化场景

### 场景 1：新工单自动分类与补全

```python
@bus.subscribe("jira.issue_created")
async def triage_new_issue(event: dict) -> None:
    issue = (await client.get(f"/issue/{event['key']}")).json()
    fields = issue["fields"]

    triage = await triager.analyze(
        summary=fields["summary"],
        description=adf_to_text(fields.get("description")),
        project=event["project"],
    )

    # 更新字段：优先级、标签、组件
    update = {"fields": {}}
    if triage.priority and not fields.get("priority"):
        update["fields"]["priority"] = {"name": triage.priority}
    if triage.labels:
        update["fields"]["labels"] = list(
            set(fields.get("labels", [])) | {f"ai-{l}" for l in triage.labels})
    if triage.components:
        update["fields"]["components"] = [{"name": c} for c in triage.components]
    if update["fields"]:
        await client.put(f"/issue/{event['key']}", json=update)

    # 评论说明依据（透明性）
    await client.post(f"/issue/{event['key']}/comment", json={
        "body": text_to_adf(
            f"🤖 自动分类：{triage.reasoning}\n"
            f"置信度 {triage.confidence:.0%}，如有误判请直接修改。")})
```

### 场景 2：Sprint 报告生成

```python
async def generate_sprint_report(board_id: int, sprint_id: int) -> str:
    issues = await search_issues(f"sprint = {sprint_id}", max_results=200)
    by_status: dict[str, list] = {}
    for i in issues:
        by_status.setdefault(i["fields"]["status"]["name"], []).append(i)

    report = await reporter.write_sprint_report(
        sprint_name=f"Sprint {sprint_id}",
        done=by_status.get("Done", []),
        in_progress=by_status.get("In Progress", []),
        todo=by_status.get("To Do", []),
    )
    # 发到 Confluence 或 Slack
    await slack.post(channel="#team-plat", text=report)
    return report
```

### 场景 3：从 Slack/告警自动建单

```python
async def create_issue_from_alert(alert: dict) -> str:
    resp = await client.post("/issue", json={
        "fields": {
            "project": {"key": "OPS"},
            "issuetype": {"name": "Bug"},
            "summary": f"[告警] {alert['name']}",
            "description": text_to_adf(
                f"告警规则：{alert['name']}\n"
                f"首次触发：{alert['fired_at']}\n"
                f"指标：{alert['expr']}\n"
                f"Runbook：{alert['runbook_url']}"),
            "priority": {"name": "Highest" if alert["severity"] == "critical" else "High"},
            "labels": ["auto-created", "alert", alert["severity"]],
        }})
    resp.raise_for_status()
    return resp.json()["key"]
```

---

## 双向状态同步

场景：LiuHao 任务与 Jira 工单联动——任务完成 → 工单 Done；工单 Reopen → 任务重启。

```python
class JiraTaskSync:
    """以 task_id 为关联键的双向同步，带循环防护。"""

    async def task_to_jira(self, task: dict) -> None:
        issue_key = await self._mapping.get_jira_key(task["id"])
        if not issue_key:
            return
        transition_id = await self._find_transition(
            issue_key, target_status=TASK_TO_JIRA_STATUS[task["status"]])
        if transition_id:
            await client.post(f"/issue/{issue_key}/transitions", json={
                "transition": {"id": transition_id},
                # 标记来源，webhook 侧据此跳过，防循环
                "update": {"comment": [{"add": {
                    "body": text_to_adf(f"🤖 同步自任务 {task['id']}")}}]},
            })
            await self._mapping.mark_synced(task["id"], direction="t2j")

    async def jira_to_task(self, issue_key: str, new_status: str) -> None:
        task_id = await self._mapping.get_task_id(issue_key)
        if not task_id or await self._mapping.was_just_synced(task_id, "t2j"):
            return  # 本系统刚同步过去的状态，跳过
        await tasks.transition(task_id, to=JIRA_TO_TASK_STATUS[new_status],
                               source="jira_sync")
```

循环防护三原则：**标记来源、最近同步窗口跳过、状态值比对（已在目标态则不动作）**。

---

## 配置示例

```yaml
# config/integrations/jira.yaml
jira:
  enabled: true
  base_url: https://your-domain.atlassian.net
  email: liuhao-bot@your-domain.com
  api_token: ${JIRA_API_TOKEN}

  webhook:
    path_suffix: random-64-char-path            # 无签名的补偿措施
    ip_allowlist: atlassian_cloud_ranges        # 定期刷新
    ignore_account_ids: ["5f8e...bot-account"]  # 机器人自身，防循环

  triage:
    enabled: true
    projects: [PLAT, OPS]
    set_priority: true
    label_prefix: "ai-"
    min_confidence: 0.7                         # 低置信度只评论不改字段

  sync:
    enabled: true
    status_mapping:
      task_to_jira: { completed: Done, failed: "To Do", running: "In Progress" }
      jira_to_task: { Done: completed, Reopened: pending }
    loop_guard_window_s: 60

  reports:
    sprint_report:
      cron: "0 18 * * 5"                        # 每周五 18:00
      board_id: 42
      channel: "#team-plat"
```

---

## Jira Data Center 差异

| 项 | Cloud | Data Center |
|----|-------|-------------|
| Base URL | `https://x.atlassian.net/rest/api/3` | `https://jira.company.com/rest/api/2`（注意 v2） |
| 认证 | 邮箱 + API Token | PAT（Personal Access Token）Bearer |
| 用户标识 | `accountId` | `name`（用户名） |
| Webhook | API 注册 | 管理界面配置 |
| 描述格式 | ADF | Wiki 标记 / 纯文本 |

PAT 认证：

```python
client = httpx.AsyncClient(
    base_url="https://jira.company.com/rest/api/2",
    headers={"Authorization": f"Bearer {settings.jira_pat}"},
)
```

---

## 最佳实践

1. **机器人专用账号**：权限收敛到目标项目，操作审计归属清晰，员工离职不影响集成。
2. **AI 操作透明**：所有自动改动附带评论说明依据与置信度，允许人一键纠正。
3. **低置信度只建议**：分类置信度 < 阈值时评论建议而非直接改字段。
4. **防循环是第一要务**：任何双向同步都必须有来源标记 + 窗口跳过。
5. **JQL 加索引字段**：查询条件用 project/status/updated 等索引字段，避免全项目扫描。
6. **分页拉取**：`maxResults` 上限 100/页，大批量用 `nextPageToken` 迭代。
7. **Webhook 失败监控**：Jira webhook 多次失败会被自动禁用，定期 `GET /webhook` 检查状态。

---

## 常见问题

### Q1: Webhook 触发风暴（一次批量操作几百条事件）？

Jira 批量变更会产生事件洪流。处理侧做：事件入队削峰、按 issue key 去重合并（5 秒窗口内同工单事件合并为一条）、非关键事件（如 rank 变更）直接丢弃。

### Q2: 状态流转报 `transition not found`？

目标状态不在当前工作流的可达转移中。先 `GET /issue/{key}/transitions` 拿可用转移列表，按名称匹配；不同项目工作流不同，映射表按项目配置。

### Q3: 自定义字段（故事点、Sprint）怎么写？

自定义字段用 ID 而非名称：`"customfield_10016": 5`。字段 ID 查询：`GET /field`。Sprint 字段通常 `customfield_10020`，但**各实例不同**，必须查。

### Q4: 同步时字段权限不足（`Field cannot be set`）？

机器人账号缺该字段的编辑权限，或字段不在对应 screen 上。Jira 权限模型是"权限 + 界面"双重控制，两者都要配。

### Q5: 如何实现"工单关闭后智能体归档相关记忆"？

订阅 `jira:issue_updated`，changelog 中 status 变为 Done → 发布内部事件 → 记忆内核把该工单关联的工作记忆归档为情景记忆，并打上 `jira:{key}` 标签供后续检索。

---

**相关文档**：[GitHub 集成](github-integration-guide.md) · [Slack 集成](slack-integration-guide.md) · [自定义集成](custom-integration-guide.md)
