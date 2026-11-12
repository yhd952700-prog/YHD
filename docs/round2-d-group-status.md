# 第二轮完善 D 组状态说明（补测试 + 重功能评估）

日期：2026-11-12。分支 `release-0.3.0`（HEAD `d077e1cf`）。
本文件只记录 D 组（补测试 + 重功能评估）的结论，不承诺其他组的工作。

## 1. 重功能逐项结论

| 功能 | 结论 | 说明 |
|---|---|---|
| 知识库持久化 / 向量检索 | 已实现，无需新做 | `src/knowledge/vector_store.py` 的 `PersistentVectorStore` 已有 SQLite 持久化 + FTS5 词法索引 + RRF 融合；`src/knowledge/embedding.py` 有真实 HTTP embedder（OpenAI 兼容 / Ollama），mock 仅在显式开发模式 opt-in 时才建，否则抛 `EmbeddingUnavailableError` fail-closed。D 组用新测试验证了持久化往返与 FTS 关键词检索（真实分数）。注意：`src/knowledge/retriever.py` 的 `Retriever` 默认 `provider_name="mock"`，其 `retrieve` 直接调 pipeline——此为旧路径，网关实际走 `build_embedding_provider`，两者要区分。 |
| 外部锚定 | 已实现（本地参考日志 + 抽象传输诚实拒绝） | `src/kernels/audit/external_anchor.py`：RFC 6962 Merkle 树 / 包含证明 / STH 验算真实；`ExternalLogTransport` 基类方法抛 `NotImplementedError`；唯一 concrete 实现 `LocalReferenceLog` 是进程内自证参考日志，配它时 `ExternalTransparencyAnchor` 的 `self_attested=True`、`independent is False`，不得宣称为独立验证；另有 `src/kernels/audit/transparency_anchor.py`（本地独立锚定日志链）。已有专用测试 12 passed。不做生产外部端点（CT 日志 / OpenTimestamps / 比特币锚定），需要真实外部服务与产品决策。 |
| TPM | 没做，是对的 | `src/security/evidence/tpm_provider.py` 明确 interface-only，`timestamp` / `verify` / `sign` / `generate` / `rotate` / `public_key_pem` 均抛 `NotImplementedError`；最终硬件根选择是保留的人类决策 HD-05（见 `docs/autonomous/HUMAN-DECISION-BACKLOG.md` 与 `docs/adr/ADR-root-of-trust-hd05.md`）。D 组用测试钉住了该行为。不引入 TPM 绑定、不假装实现。 |
| 加密备份 | 已做最小落地 | `src/backup/backup_manager.py`：此前 `encryption_enabled=True` 时 `create_backup` 直接抛 `NotImplementedError`。现已接通真实加密（`cryptography` 为 pyproject 已声明依赖，不引入新依赖）：`derive_backup_key`（PBKDF2-HMAC-SHA256，600_000 次，32 字节）；`encrypt_backup_bytes` / `decrypt_backup_bytes`，格式 `b"LHBAK1" + 12 字节随机 nonce + AESGCM 密文（含 GCM tag）`，解密失败统一报 `ValueError`；无密钥或密钥长度不对时拒绝写入（不写明文、不写密文）；只给密钥未开启时拒绝写明文；写入顺序先 gzip 再加密，后缀 `.enc`；`verify_backup` 与 `restore_backup` 在 `meta.encrypted` 时先解密再解压/解析，无密钥时恢复失败闭合。旧测试 `tests/test_backup_restore_drill.py::test_encryption_flag_is_not_a_silent_noop` 已按新契约更新（行为变更，非删测试凑通过）。另有既有实现：`src/gateway/account_backup.py` 已有 AESGCM 加密；`scripts/ops/backup.py` 已有 Fernet 加密，且加密开启无密钥时拒绝写未加密备份。 |
| A2A / MCP 真实传输 | MCP 已真实，A2A 没做 | `src/adapters/mcp/mcp_adapter.py` 是真实 FastMCP 客户端（stdio / streamable HTTP、工具 / 资源 / 提示调用）。`src/ai/network_gateway.py` 的 `A2AAdapter` / `MCPAdapter`（协议适配层）明确仅进程内投递，无真实外部传输——已配置真实传输时 `send()` 诚实返回 False 并写 `message.metadata["error"] = "real A2A transport not implemented in this build"`；`src/kernels/network/__init__.py` 的 `ProtocolAdapter` 基类 `send` / `receive` 抛 `NotImplementedError`，另有 `InternalAdapter`、`HTTPAdapter`、`WebSocketAdapter`。A2A 真实跨网传输需要远程 agent 端点与产品决策，不假装实现。 |
| accounts.ts 与 api.ts 收敛 | 已做 | `apps/console/console/src/lib/accounts.ts` 的私有 `request()` 原复制了 `api.ts` 的 `apiFetch` 全部机制（令牌双写、15 秒超时、401 清会话、错误体解析），且曾给 FormData 也写 `Content-Type: application/json`（会破坏 multipart 边界）。现 `request()` 改为委托 `apiFetch`，只保留账号面两条映射规则：401 一律中性化成“登录状态已失效，请重新登录”；其余错误保留 apiFetch 归一化的文案与稳定 code，文案为空时按状态补兜底（`isRouteMissing` 依赖 code 为 null 判定路由未挂载，原样透传 code）。`exportAllData` 仍走自己的 fetch（需读原始 Response 的 blob + 文件名），未动。行为差异：超时文案改为 apiFetch 的“请求超时（>15s）……”；网络失败时会多一次 `/v1/ready` 探测。收敛后 `accounts.test.ts` + `api.test.ts` + 4 个新页面/组件测试 + SingleAssistant 共 7 文件 63 测中 62 passed、1 failed（该失败是并行改动导致的 SingleAssistant 旧副标题断言，后已修复并单独重跑通过）。 |
| L4 本地状态同步 | 仓库里没有名为 L4 的同步层，未做 | L0–L7 是权限 / 作用域等级（如 `src/kernels/network/__init__.py` 的 `Route.scope`、`apps/console/console/src/components/OperatorControls.tsx` 的 L0–L7 选择器），不是同步层级；“L4”在 `src/knowledge/memory.py` 仅作为记忆类型注释出现（`PROCEDURAL = "procedural" # L4: Skills & procedures`）。事实上的本地状态同步机制是控制台 `lib/api.ts` 的 `useApi` 轮询 + 任务台账滞后检测（`src/gateway/task_ledger.py` 的 hot goals vs ledger lag）；没有离线队列、乐观更新或冲突解决。需要产品决策才做，不编一个假同步层。 |

## 2. 唯一结合版助手（用户最高优先级纠正，2026-11-12）

不要能切换 Muse 和小龙虾，要两者的结合版（一个助手，不要切换器）。

- 仓库控制台的助手本来就是唯一的（`apps/console/console/src/lib/assistant.ts`：`ASSISTANT_NAME` 取自 `WORKBENCH_NAME_ZH`，即“贾维斯”；`ASSISTANT_TAGLINE = '沉稳能干，活泼亲切——写邮件、总结、翻译、想点子都行'`）。仓库内从未有 Muse / 小龙虾切换器，也没有任何旧测试断言切换——D 组不需要改任何旧测试。
- D 组新增 `apps/console/console/src/components/SingleAssistant.test.tsx`（2 测）：渲染 ChatPanel，断言只出现一个助手身份（`贾维斯` 恰好 1 处，定位语为 `ASSISTANT_TAGLINE` 且含“沉稳”“活泼亲切”）；断言没有任何切换助手入口（无 `切换助手` group/button、无 Muse/小龙虾按钮、无“小龙虾”/“Muse”文本）。
- 同一时段其他组并行加了两处同方向断言（非 D 组）：`apps/console/console/src/components/ChatPanel.test.tsx`（“界面上只有一个助手，没有 Muse / 小龙虾切换器”）与 `apps/console/console/src/lib/assistant.test.ts`（问候与快捷问题不含“小龙虾”，定位语同时点到 Muse 的能力与小龙虾的风格但不展开成两个人）。`assistant.ts` / `assistant.test.ts` 本身也是并行新建，非 D 组所建。
- 切换器实际存在于已交付的两个单文件成品（`~/workspace/your_files/`，非仓库），D 组已把两者改成唯一结合版助手 Muse（能力像 Muse、风格融合小龙虾活泼亲切），并用断言脚本验证 14/14 通过；原文件备份在 `/tmp/d2_console_before_merge.html`、`/tmp/d2_aigongju_before_merge.html`，脚本在 `/tmp/d2_merge_console.py`、`/tmp/d2_merge_aigongju.py`、`/tmp/d2_single_assistant_check.js`。两成品的 `<script>` 提取后 `node --check` 均通过。
- 后续跟进点（未列入 D 组范围，不做）：Joseph 要求工作台/助手名字可改、不是写死的；目前仓库里助手名是常量（`ASSISTANT_NAME` 取自 `WORKBENCH_NAME_ZH`），尚未做成可配置。

## 3. 验证结果（2026-11-12 实测）

- 控制台新测试：`npx vitest run` 4 个新文件 39 passed（ApprovalQueue 9、Audit 9、Backup 8、ApprovalCenter 13）；SingleAssistant 2 passed；`assistant.test.ts` 7 passed（他人所建）；accounts + api 基线 22 passed。
- 控制台全量：`npx vitest run` 81 文件 759 测，**757 passed、2 failed**（161 秒）。2 个失败均不在 D 组文件：`src/App.deepLink.test.tsx`（1 测：无参数时标题应为“企业总览”，实得 null）与 `src/pages/MyEmployees.pagination.test.tsx`（1 测：全量跑时 5 秒超时）。复核：两文件单独重跑时 MyEmployees 通过（超时为全量负载下的偶发），App.deepLink 仍失败；用 `git show HEAD:` 临时还原 `accounts.ts` 后 App.deepLink 照样失败——证明与 D 组的 accounts 收敛无关，是并行改动中的 `App.tsx`（41 行改动）导致，留给相关组修，D 组不碰他人在途改动。恢复后重验 accounts + api + SingleAssistant 24/24 passed。
- 后端：`python3 -m pytest tests/test_d2_core_coverage.py tests/test_backup_restore_drill.py tests/test_backup_comprehensive.py -q` 共 **82 passed**（D2 新文件 30 测）；旧测试 `tests/kernels/audit/test_external_anchor.py` 12 passed、`tests/knowledge/test_hybrid_search.py` 17 passed。
- Frontend：`npm test` 14 passed（`frontend/src/utils/utils.test.ts` 12 测 + `frontend/src/hooks/useTask.test.ts` 2 测）。
- `python3 -m compileall -q` 对 `src/backup/backup_manager.py`、`src/knowledge/vector_store.py`、`src/knowledge/chunker.py`、`src/health/health_check.py`、`src/cost/cost_tracker.py`、`src/kernels/audit/external_anchor.py`、`src/security/evidence/tpm_provider.py` 通过。
- 控制台 `npm run build`（`tsc -b && vite build`）：**失败，exit 2，但 3 个 TS 错误全在 `src/pages/OwnerOverview.tsx`（184/245/272 行，`actions` 属性不存在，应为 `action`），是并行改动改了 Card 组件 prop 名所致；D 组文件零 TS 错误**。构建被他人在途改动阻塞，D 组不修，待相关组修复后重跑即可。

## 4. 仍无测试清单（2026-11-12 核对）

- 控制台页面：44 个非测试页面中 24 个仍无同名测试——AccountSecurity、Apps、Directory、Events、Files、Goals、Identity、KnowledgeEnterprise、Members、MyData、MyKeys、Network、Notifications、Operations、OperatorConsole、OwnerTasks、PermissionMatrix、Preflight、Recovery、RoleTemplates、Settings、Trust、WorkflowCenter、WorkflowEditor。（部分有邻近测试而非同名：Directory 有 `Directory.runtimePagination.test.tsx`；Settings 有 embedding / map / upgrade 三个专项测试；KnowledgeEnterprise 有 KnowledgeCenter / Knowledge 测试覆盖邻近能力。）
- 控制台组件：11 个仍无同名测试——AccountChrome、DesktopDownloadLink、LocalGap、Login、Markdown、MobileAccessCard、OperatorControls、OrgTemplateDrawer、Shell、charts、icons。
- 控制台 lib：未逐项核对同名测试的模块包括 knowledgeEnterprise、modelAccess、skillsTools、taskLedger、operator（部分）、goalWork、localProof、honesty 等；以 `src/lib/*.test.ts` 现状为准。
- 后端：按“模块名是否在 tests/ 中被提及”的粗略统计（非覆盖率实测），仍有 31 个模块未被提及——`src/adapters/observability/{metrics_helper, observability_adapter, opentelemetry_adapter}.py`、`src/ai/memory/{meta_memory, semantic_network, working_memory}.py`、`src/ai/preview_server.py`、`src/ai/reasoning/{chain_of_thought, deductive}.py`、`src/bootstrap_log.py`、`src/common/{input_guard, pid_probe, request_context}.py`、`src/desktop/{repair_ui, tray_icons}.py`、`src/feedback/feedback_api.py`、`src/gateway/{device_sessions, lan_access, log_settings, org_templates, skill_import, skill_install}.py`、`src/infra/infra_charts.py`、`src/kernels/retention/archival.py`、`src/knowledge/translation.py`、`src/model_gateway/provider_adapter.py`、`src/plugins/plugin_manager.py`、`src/plugins/sandbox/backends/docker_backend.py`、`src/security/vault_crypto.py`、`src/selfmod/patcher.py`、`src/selfupgrade/pages_check.py`。D 组已补的 7 个模块（chunker、health_check、cost_tracker、vector_store、backup_manager、external_anchor、tpm_provider）不在其中。
- Frontend：除 utils（12 测）与 useTask 常量（2 测）外，hooks 本体（useAgent、useKnowledge、useMemory、useWebSocket）与全部 22 个组件目录均无测试。刻意选择：frontend 未配 `@testing-library/react`，不引入渲染测试依赖，只测纯函数与导出常量。
