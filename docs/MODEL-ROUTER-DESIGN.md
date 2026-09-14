# Phase 5 — Model Router 设计文档 (MODEL-ROUTER-DESIGN)

> 阶段目标：让模型选择从"一个全局 env 变量"升级为**按任务类型/能力/成本/时延路由**，
> 覆盖 Cloud / Local / Special 三层，复用**既有但离线**的 `src/model_gateway`，不重造、不破坏既有 API。
>
> 设计原则：不增代码量优先 / 可回滚 / 不破 API / 先设计后编码。

---

## 1. 当前状态（实测事实，带 file:line）

| 事实 | 位置 |
|------|------|
| **主链路选择单一**：`get_provider()` 读 env `AI_PROVIDER_TYPE`（默认 mock），构造**唯一**全局 provider 单例 | `src/ai/providers.py:689-741` |
| 支持的 provider：mock/openai/anthropic/google/ollama/moonshot/deepseek | `providers.py:22-29`, `ProviderFactory:611-622` |
| **`src/model_gateway` 已完整但离线**：`ModelRegistry` + `ModelRouter` + `ProviderAdapter`(OpenAI/Anthropic/Mock) + `RouteResult/RoutingRule` | `src/model_gateway/__init__.py:13-21` |
| `ModelRouter.route(required_capabilities, min_context, max_cost_per_token, max_latency_ms, exclude_models, prefer_recent) -> RouteResult` | `model_router.py:135-200` |
| ⚠️ **路由器的成本/时延约束是空的**：`max_cost_per_token` 分支只有 `pass`；`max_latency_ms` 完全未用 | `model_router.py:236-240` |
| `RegisteredModel` **无 cost/latency 字段**（只有 capabilities） | `model_registry.py:43-62` |
| `ProviderCapabilities`：streaming/structured/function_calling/vision/embeddings + context/output/rate | `provider_adapter.py:42-64` |
| `ProviderType`（gateway）已含 ollama | `provider_adapter.py:27-36` |

**结论**：Phase 5 = **把 `model_gateway` 接进主链路**，并**修掉它的成本/时延空转**（根因），而不是新写一个 router。

---

## 2. 目标能力

| 用户要求 | 落地方式 |
|----------|----------|
| task→model（类型/成本/时延/能力） | 新增 `TASK_PROFILES`（任务画像）+ `route_task()`；成本/时延在 bridge 子类里**真正强制执行** |
| Cloud / Local / Special | 新增 `ModelTier`（CLOUD/LOCAL/SPECIAL），按 provider 归类；任务画像可 `prefer_tier` |
| OpenAI / Anthropic / Ollama | 复用 gateway `ProviderType`（已含 ollama）；catalog 注册三类及以上 |
| 复用 `src/model_gateway` | 桥接驱动 `ModelRegistry` + `ModelRouter`（子类），不复制其逻辑 |

---

## 3. 架构

```
   调用方 (chat / agent_runtime / …)
        │  get_provider_for_task(task_type=...)
        ▼
   env LIUHAO_MODEL_ROUTER == off/未设 ──▶  get_provider()   （历史路径，逐字节不变）
        │ on
        ▼
   src/ai/model_router.py   (新增 bridge)
        │  TASK_PROFILES[task_type] → RouteProfile
        ▼
   LiuhaoModelRouter(ModelRouter)  ← 复用 src/model_gateway/model_router.py
        │  覆写 _get_candidates：capability + context + **cost** + **latency** + prefer_tier
        ▼
   ModelRegistry (填自 DEFAULT_CATALOG：cloud/local/special)
        ▼
   RouteResult(selected_model=RegisteredModel)
        ▼
   provider_type_for() → ProviderFactory.create_provider() / 复用既有单例
```

**关键决策**
- **默认 OFF**（`LIUHAO_MODEL_ROUTER` 未设 = off）→ `get_provider()` 行为与现在完全一致（可回滚、零风险）。
- **成本/时延根因修复**：不修改 `src/model_gateway`（保持其可独立使用），而是在子类 `LiuhaoModelRouter` 覆写
  `_get_candidates`，用 side-table（`{model_id: ModelProfile}`）真正过滤 `max_cost_per_token` / `max_latency_ms`，
  并支持 `prefer_tier`。→ 可回滚 = 删除 bridge。
- **fail-closed**：路由到某 provider 但该 provider 需要的 key 缺失/是占位符时**抛错**，不静默换 key。
  路由本身失败（无候选）时**显式 WARNING + 回退到 `get_provider()`**（可观测，非静默）。

---

## 4. 任务画像（TASK_PROFILES，节选）

| task_type | required_capabilities | min_context | max_cost/1k | max_latency_ms | prefer_tier |
|-----------|----------------------|-------------|-------------|----------------|-------------|
| `default` | — | — | — | — | cloud |
| `reasoning` | function_calling | 32000 | — | — | cloud |
| `vision` | vision | 8000 | — | — | cloud |
| `cheap_bulk` | — | 4000 | 0.001 | — | special |
| `privacy` | — | 4000 | — | 3000 | **local** |
| `structured` | structured_output | 8000 | — | — | cloud |

> 数值是**保守默认**，可被 `route_task(**overrides)` 逐次覆盖；不做"猜成本"的隐式网络调用。

---

## 5. 新增公开 API（`src/ai/model_router.py`，全新增）

```python
class ModelTier(str, Enum): CLOUD / LOCAL / SPECIAL
@dataclass ModelProfile: model_id, provider, model_name, tier, capabilities, cost_per_1k_tokens, typical_latency_ms
DEFAULT_CATALOG: List[ModelProfile]
@dataclass TaskProfile: required_capabilities, min_context, max_cost_per_token, max_latency_ms, prefer_tier
TASK_PROFILES: Dict[str, TaskProfile]
class LiuhaoModelRouter(ModelRouter):
    def __init__(self, registry, profiles: Dict[str, ModelProfile])
    def route(*, task_type=None, prefer_tier=None, **kwargs) -> RouteResult   # 覆写，注入 tier
    def _get_candidates(...)  # 覆写：capability+context+cost+latency+prefer_tier
def build_default_registry() -> ModelRegistry
def get_model_router() -> LiuhaoModelRouter
def route_task(task_type=None, **overrides) -> RouteResult
def provider_type_for(result: RouteResult) -> str
def get_provider_for_task(task_type=None, **overrides) -> BaseProvider   # 桥到 providers.py，默认透传 get_provider()
```

---

## 6. 回滚方案
- `src/ai/model_router.py` 纯新增 → `git rm` 即回滚；无既有调用方（除本阶段测试）。
- 不改 `src/model_gateway/*`、不改 `src/ai/providers.py` 任何签名；`LIUHAO_MODEL_ROUTER` 默认 off ⇒ 主链路零变化。

---

## 7. 测试计划（`tests/ai/test_model_router.py`）
1. `test_default_catalog_tiers`：catalog 覆盖 CLOUD/LOCAL/SPECIAL 三层。
2. `test_route_privacy_prefers_local`：`route_task("privacy")` 选中 LOCAL（ollama）。
3. `test_cost_constraint_enforced`：`max_cost_per_token` 真的生效（默认 router 里是 `pass` → 本 bridge 必须过滤掉贵模型）。
4. `test_latency_constraint_enforced`：`max_latency_ms` 生效。
5. `test_capability_and_context_filter`：`vision` 只落到支持 vision 的模型；`min_context` 过滤小窗口。
6. `test_no_candidate_raises`：不可能约束 → 抛错（不静默）。
7. `test_router_off_returns_default_provider`：未开 `LIUHAO_MODEL_ROUTER` → `get_provider_for_task()` 返回 `get_provider()`（同一实例）。

运行：`tests/ai/` 子集，离线（mock/ollama 目录项，无网络）。

---

## 8. 风险
- 成本/时延是**静态目录值**，非实时计费 → 标注为"预算约束"，不声称精确计费。
- `get_provider_for_task` 只在显式开启时切换 provider；默认路径完全不变。
- 与 Phase 4 同源：不引入新依赖。
