# 首次安装端到端自检（可复现步骤记录）

模拟“陌生人刚下载安装完”的机器：**空数据目录、不预置任何模型配置**，只通过控制台与公开 HTTP 接口完成「填 Key → 保存 → 测试连接 → 真实对话」。

自动化脚本：[scripts/verify_first_run_e2e.py](../scripts/verify_first_run_e2e.py)

## 一条命令重跑

```bash
# 真实 Key 只从参数或环境变量取，不写进仓库
python scripts/verify_first_run_e2e.py --api-key sk-xxxx
# 或
LIUHAO_E2E_API_KEY=sk-xxxx python scripts/verify_first_run_e2e.py
```

附加参数：`--model` 覆盖预设模型名；`--keep` 保留临时数据目录用于排查（否则跑完删除）。

退出码：`0` = 全部通过；`1` = 有失败项；`2` = 没给 Key。

## 步骤与断言

| # | 动作 | 断言 |
|---|------|------|
| 1 | 全新临时数据目录，不预置模型，启动 `python -m src.gateway` | `/v1/health` 60s 内 200 |
| 2 | 打开控制台 `/` | 返回 console 首页；构建产物含「去配置模型 / 添加模型 / API Key」指引 |
| 3 | 登录（管理员由安装阶段 `scripts.first_run_wizard.register_admin` 创建） | `POST /v1/auth/login` 返回 access_token；`GET /v1/models` = 0 |
| 4 | 填 Key 保存（`POST /v1/models`，deepseek 预设） | 立即生效（**无需重启**），响应无明文 Key |
| 5 | `POST /v1/models/{id}/test` | `ok=true`，记录上游延迟 |
| 6 | 立刻 `POST /v1/chat` | 返回真实回复（非模板/非错误串），记录端到端耗时 |
| 7 | 只读校验数据目录中的 SQLite | 文件内**不含明文 API Key**（仅读取，不写库） |
| 8 | 杀进程，同一数据目录重启，再对话 | 模型配置仍在、无需重填，对话成功 |
| 9 | 错误 Key 测试连接 | `reason=invalid_key` 且 detail 有中文结论，不是笼统“调用失败” |
| 10 | 被吊销 / 额度耗尽的 Key | 无法凭空取得真实凭据 → 明确 SKIP，由归类单测覆盖 |

## 环境隔离

脚本把所有持久化状态指向临时目录，等价于新机器：

`LIUHAO_MODEL_CONFIG_DB`、`LIUHAO_MODEL_CONFIG_KEY_FILE`、`LIUHAO_HUMAN_IDENTITIES_BACKEND/FILE`、`LIUHAO_AUTH_SECRETS_FILE`、`CONVERSATION_DB_PATH`、`PERSONAL_CONTEXT_DB_PATH`、`LIUHAO_KNOWLEDGE_DB`、`AUDIT_DB_PATH`、`LIUHAO_JWT_REVOCATION_DB`。

全程不手工改配置文件、不手工写数据库。

## 错误分类（界面能说清“该改什么”）

`POST /v1/models/{id}/test` 失败时返回 `reason` / `hint` / `detail`：

| reason | 含义 | 用户下一步 |
|--------|------|-----------|
| `invalid_key` | Key 填错/复制不完整 | 重新粘贴完整 Key |
| `key_revoked` | Key 被吊销/停用（401/403 + revoked、deactivated 等） | 到提供方控制台重新生成 |
| `quota_exhausted` | 额度耗尽（402 / insufficient balance 等） | 充值或提升额度 |
| `rate_limited` | 上游限流 | 稍后重试 |
| `model_not_found` | 模型名不存在（404） | 核对模型 ID |
| `unreachable` / `timeout` | 网络或 base_url 问题 | 检查地址与网络 |

`hint` 为中文结论，`detail` 以「结论。后端原文：…」拼接，界面即便只显示 `detail` 也能读懂。

## 已知限制

- “被吊销”“额度耗尽”两类无法用真实凭据复现，由 `tests/test_gateway_models_api.py` 的归类单测保证（402 → `quota_exhausted`，401/403 + revoked/deactivated → `key_revoked`）。
- 脚本需要一个真实可用的提供方 Key，才能验证第 5–8 步；没有 Key 时只能跑其余断言。
