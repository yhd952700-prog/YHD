# 四层端到端集成演示（Phase 9 / 10 / 12 / 15）

> 一句话：把 L-Core、Multi-Agent 协作、Organization、World Interface 四层串成一个可运行场景，暴露并记录它们之间的真实集成缝。

## 为什么做这个

四层各自已有单测全绿，但「各自能跑」不等于「拼起来能跑」。本演示用一个场景把它们按真实依赖关系组合起来，目标是**暴露集成缝**，而不是再堆一层代码。

## 场景

```
Organization (Phase 12)    建组织：预算 5000、两个部门、一条政策、一个目标，花 1200
   └─ MultiAgentTeam (Phase 10)  招 MANAGER/RESEARCHER/ANALYST/QA，跑 researcher→analyst→qa 流水线出报告
        └─ org.remember("final_report", report)  报告存入组织记忆
   └─ org.hire(ANALYST) + delegate  真实 Agent 执行，出 KPI
   └─ L-Core (Phase 9)   注册一个 capability=`multi_tier_memory` 的 Tool
        └─ World Interface (Phase 15)  Tool.fn 闭包调 WorldInterface.execute 把报告写盘
             └─ World.observe 读回校验一致
```

运行：

```bash
.venv/Scripts/python.exe -m src.ai.e2e_demo
```

## 交付物

| 文件 | 作用 |
|------|------|
| `src/ai/e2e_demo.py` | `run_e2e_demo(output_dir=None, provider=None)` 场景函数 + `main()` 可运行入口 |
| `tests/test_e2e_integration.py` | 2 用例：四层组合正确性 + 每次运行输出目录隔离 |

## 暴露的集成缝（本轮诚实标注，非隐藏重写）

1. **Tool 路由 vs World 契约**：LCore 按 `capability_id → Tool` 路由，World 暴露 `execute(WorldRequest)`；两者靠 `Tool.fn` 闭包桥接。这是自然组合点，但意味着 World 动作的语义封装在 L-Core 的 Tool 层，二者没有自动映射。
2. **GoalDecomposer 关键词映射**：`save/store/remember/memory → multi_tier_memory` 这个固定映射决定了「写盘」Tool 该挂哪个 capability。若未来意图词表扩展，这条映射是瓶颈。
3. **Provider 双通道**：`Organization` 经 `AgentFactory` 读**全局** `get_provider()`，`MultiAgentTeam` 直接收 `provider=`。演示用 `set_provider(...)` + `finally reset_provider()` 对齐两条通道，离线可复现。这是「全局 provider」与「依赖注入」并存的真实张力。
4. **策略分层**：World 的 `authorize`（写盘范围约束）在 World 层独立执行，LCore 的 `authorize`（计划级）在 L-Core 层独立执行；两者尚未串成一条完整策略链（下一步候选）。

## 验证

- 定向回归（kernel 402 + Agent Runtime 16 + 协作 12 + 组织 12 + L-Core 16 + World 11 + 端到端 2）：**471 passed / 0 failed**，零回归。

## 已知历史遗留（与本轮无关，未处理）

全量 `pytest` 有 4 个 collection error，均为既有问题：
- `tests/test_crypto_audit.py`、`tests/test_sec05_sec06.py`：`src.security` 缺 `VaultTransitCrypto` / `RBACManager`。
- `tests/test_memory.py`：与 `tests/kernels/memory/test_memory.py` 重名导致 import mismatch。
- `tests/load/test_load_baseline.py`：load 基线收集错误。

## 下一步候选

1. **Network Phase 14**（A2A / MCP）：World 的网络适配器依赖它，是当前最硬的一块缺口。
2. **Long-Horizon Phase 13**：跨 session 的长程目标记忆。
3. **策略链打通**：把 World 授权 deny 路径接进 LCore 的 policy 环节，形成完整「计划→动作→世界写盘」策略链。
