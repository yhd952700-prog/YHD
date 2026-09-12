# L10K Baseline Test Design — Verified Human Leverage (VHL)

> **Date**: 2026-09-05
> **Standard**: LIUHAO X v3.0 — Phase 21 Acceptance（原文引用 "Definition Lock §141"；该节号**命名空间不可判定**，见文末「附录 A · 节号溯源说明」）
> **Owner**: Hermes / MVP Development Expert Team PM (大湾区靓仔)

---

## 1. VHL Definition

**VHL = VERIFIED VALUE OUTPUT / HUMAN ACTIVE MINUTES**

- **VERIFIED VALUE OUTPUT**: 通过 Verification Gate 确认的、可追溯到 Evidence 的价值产出
- **HUMAN ACTIVE MINUTES**: 人类主动投入的时间（不含等待、监控、被动观察）

---

## 2. Baseline Benchmarks (Phase 21)

### 2.1 Baseline Categories

| Baseline | Description | Tool/Method |
|----------|-------------|-------------|
| **Human Only** | 人类单独完成任务 | 计时器 + 专家评估 |
| **Copilot** | GitHub Copilot / Cursor 等 IDE 助手 | 标准化任务 + 计时 |
| **Single Agent** | 单一 LIUHAO Agent 完成 | Agent Runtime + 计时 |
| **Multi-Agent** | 多 Agent 协作完成 | Agent Factory + 计时 |
| **LIUHAO X** | 完整 LIUHAO X 系统 | Full Stack + 计时 |

---

## 3. Task Suite (T1-T5)

| Task ID | Difficulty | Description | Expected Human Time | Value Metric |
|---------|------------|-------------|---------------------|--------------|
| **T1** | Simple | "查找并汇总过去 30 天所有供应商的报价单，生成对比表" | 30 min | 表格行数 × 准确率 |
| **T2** | Standard | "根据客户需求文档，自动生成报价单草稿，包含价格计算、条款、有效期" | 90 min | 完整度 × 合规性 |
| **T3** | Complex | "分析 Q3 供应商绩效数据，识别风险前 3 名，生成改进建议报告" | 180 min | 洞察质量 × 可执行性 |
| **T4** | Long Horizon | "持续监控未来 4 周供应商交付表现，每周自动生成预警摘要" | 10 min/周 × 4 = 40 min | 预警准确率 × 时效性 |
| **T5** | Organization | "组建 3-Agent 团队（采购/财务/法务），完成跨部门合同审批流程" | 240 min | 流程完整性 × 合规性 |

---

## 4. Measurement Protocol

### 4.1 Per-Task Recording

| Field | Source |
|-------|--------|
| Task ID | Fixed |
| Difficulty | Fixed |
| Expected Value | Pre-defined rubric |
| Human Active Time | Stopwatch (human starts/stops) |
| AI Active Time | System timestamp (agent start/end) |
| Verified Output | Verification Gate result |
| Cost | Token usage × model pricing |
| Latency | Wall-clock time |
| Intervention Count | Human interrupts / approvals |
| Security Incidents | Policy violations |
| Reliability | Retry / recovery count |

### 4.2 Verification Gates（原文引用 "Definition Lock §139"；节号不可判定，见文末附录 A）

| Gate | Criteria |
|------|----------|
| Schema Verification | Output matches expected schema |
| Source Verification | Evidence traceable to source |
| State Verification | System state consistent |
| Policy Verification | No policy violations |
| Test Verification | Automated tests pass |
| Human Verification | Expert review sign-off |

**Result:** `VERIFIED` / `PARTIALLY_VERIFIED` / `FAILED` / `UNKNOWN`

---

## 5. Anti-Gaming Rules（原文引用 "Definition Lock §141"；节号不可判定，见文末附录 A）

**Prohibited manipulation:**

| Prohibited | Detection |
|------------|-----------|
| More Agents | Count agents per task |
| More Tokens | Token budget enforcement |
| More Tasks | Fixed task suite (T1-T5) |
| Easier Tasks | Fixed difficulty rubric |
| Repeated Tasks | Task ID tracking |
| Lower Quality | Verification Gate quality threshold |

**Quality Threshold:** Minimum `VERIFIED` rate ≥ 80% for inclusion in VHL calculation.

---

## 6. VHL Calculation

```
For each task i:
  VHL_i = Verified_Value_Output_i / Human_Active_Minutes_i

Overall VHL = Σ(Verified_Value_Output_i) / Σ(Human_Active_Minutes_i)
```

**Normalization:** All value outputs normalized to [0, 1] scale per rubric.

---

## 6. Reporting Template

| Metric | Human Only | Copilot | Single Agent | Multi-Agent | LIUHAO X |
|--------|------------|---------|--------------|-------------|----------|
| T1 VHL |  |  |  |  |  |
| T2 VHL |  |  |  |  |  |
| T3 VHL |  |  |  |  |  |
| T4 VHL |  |  |  |  |  |
| T5 VHL |  |  |  |  |  |
| **Overall VHL** |  |  |  |  |  |
| Cost per VHL |  |  |  |  |  |
| Intervention Rate |  |  |  |  |  |
| Security Incidents |  |  |  |  |  |

---

## 7. Execution Procedure

1. **Environment Setup**: Clean LIUHAO X deployment (Phase 22 complete)
2. **Expert Recruitment**: 3 domain experts (procurement, finance, legal)
3. **Calibration Run**: All 5 baselines on T1 (warm-up)
4. **Official Run**: Each baseline completes T1-T5 sequentially
4. **Verification**: All outputs pass Verification Gates
5. **Calculation**: Compute VHL per baseline
6. **Reporting**: Publish L10K Report

---

## 8. Acceptance Criteria (Phase 21 Gate)

| Criterion | Threshold |
|-----------|-----------|
| LIUHAO X VHL > Human Only VHL | ≥ 2.0x |
| LIUHAO X VHL > Copilot VHL | ≥ 1.5x |
| LIUHAO X VHL > Single Agent VHL | ≥ 1.3x |
| LIUHAO X VHL > Multi-Agent VHL | ≥ 1.1x |
| Verification Rate | ≥ 90% |
| Security Incidents | 0 Critical |
| Cost per VHL | < Human Only cost |

---

## 9. Test Automation

```python
# tests/l10k/test_vhl_baseline.py
import pytest
from src.l10k.runner import VHLRunner
from src.l10k.tasks import T1_SIMPLE, T2_STANDARD, T3_COMPLEX, T4_LONG_HORIZON, T5_ORG

class TestL10KBaseline:
    """L10K VHL Baseline Tests"""
    
    @pytest.mark.parametrize("baseline", ["human_only", "copilot", "single_agent", "multi_agent", "liuhao_x"])
    @pytest.mark.parametrize("task", [T1_SIMPLE, T2_STANDARD, T3_COMPLEX, T4_LONG_HORIZON, T5_ORG])
    def test_vhl_baseline(self, baseline, task):
        runner = VHLRunner(baseline=baseline)
        result = runner.execute(task)
        
        # Must pass verification gate
        assert result.verification_status == "VERIFIED", f"Failed verification: {result.verification_details}"
        
        # Record metrics
        self.record_metrics(baseline, task, result)
    
    def test_overall_vhl_superiority(self):
        """LIUHAO X must outperform all baselines"""
        liuhao_vhl = self.get_overall_vhl("liuhao_x")
        human_vhl = self.get_overall_vhl("human_only")
        copilot_vhl = self.get_overall_vhl("copilot")
        single_vhl = self.get_overall_vhl("single_agent")
        multi_vhl = self.get_overall_vhl("multi_agent")
        
        assert liuhao_vhl >= 2.0 * human_vhl
        assert liuhao_vhl >= 1.5 * copilot_vhl
        assert liuhao_vhl >= 1.3 * single_vhl
        assert liuhao_vhl >= 1.1 * multi_vhl
```

---

## 10. Infrastructure Requirements

| Component | Requirement |
|-----------|-------------|
| LIUHAO X | Phase 22 Production Hardening complete |
| Verification Gates | All 6 gates operational |
| Cost Tracking | Token usage + model pricing |
| Audit | Full audit trail per task |
| Human Timer | Web UI for expert time tracking |
| Report Generator | Automated L10K report |

---

## 11. Sign-off

| Role | Name | Date | Status |
|------|------|------|--------|
| L10K Lead | Hermes / MVP Dev Expert Team PM (大湾区靓仔) | 2026-09-05 | DESIGN COMPLETE |

---

## 附录 A — 节号溯源说明（R8 收口，2026-09-12）

本文件正文有 3 处 `Definition Lock` 节号引用，**无法判定所属命名空间**，此处留档说明，
**不做机械替换**（改错比不改更糟）：

| 位置 | 原引用 | 判定 |
|---|---|---|
| 文首 Standard | `Definition Lock §141 Phase 21 Acceptance` | **不可判定** |
| §4.2 Verification Gates | `Definition Lock §139` | **不可判定** |
| §5 Anti-Gaming Rules | `Definition Lock §141` | **不可判定** |

**理由**：

1. Definition Lock 原件**不在本仓库，且经全量 git 历史检索从未入库**（不可恢复）——
   见 [`../spec/DEFINITION-LOCK-STATUS.md`](../spec/DEFINITION-LOCK-STATUS.md)。
2. 已知 DL 条款总数为 **122** 节（[`../spec/UNIFIED-BLUEPRINT.md`](../spec/UNIFIED-BLUEPRINT.md) 附录 A），
   故 `§139` / `§141` **不可能是 DL 节号**。
3. 若按 Master Spec 解读，`MS:§139` = THE HUMAN LOOP、`MS:§141` = THE LONG-HORIZON LOOP，
   与本文所指的 "Verification Gates" / "Anti-Gaming Rules" **语义不符**。

**结论**：这 3 处是**出处不明的历史注记**，其条款内容不可查证。本文的**实际权威依据**为：

- **Phase 21 Acceptance** → [`../spec/GAP-MIGRATION-MATRIX.md`](../spec/GAP-MIGRATION-MATRIX.md) 表 3
  与 [`../spec/MASTER-SPEC-v3.0.md`](../spec/MASTER-SPEC-v3.0.md) `MS:§177`–`MS:§198`；
- **Verification Gates / Anti-Gaming Rules** → 以本文自身表格为准（已独立成文，不依赖 DL）。

> **新写引用请一律用锚点或带前缀节号**（`UB-A1` / `MS:§198`），**禁止裸写 `§N`**。

---

*END OF L10K BASELINE TEST DESIGN*