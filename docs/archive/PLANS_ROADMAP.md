# 规划与路线图合集
> **合并说明**：本文件由 6 份独立文档于 2026-09-06 合并整理而成，原文完整保留、未改写。
>
> **路径说明（2026-09-06 目录重组）**：docs 目录已由 11 个子目录精简为 5 个（`architecture/`、`product/`、`operations/`、`l10k/`、`archive/`）。本合集为历史归档，正文中的文件路径**保持整理前的原貌未作改动**；现行路径请查 `docs/README.md`。
> 3 个月冲刺方案、短板能力推进、平台集成、开源集成、UI 开发计划与 Hermes 补全执行指令。
> 原始单文件已从仓库移除，完整备份见 `D:\WorkBuddyFiles\LiuHao-AI-OS-md-backup-2026-09-06.zip`；已提交版本可经 git 历史找回。
## 收录清单
1. `docs/SPRINT_PLAN_3M_V2.md`
2. `docs/SHORTCUT_CAPABILITIES_PLAN.md`
3. `docs/PLATFORM_INTEGRATION_PLAN.md`
4. `docs/OPENSOURCE_INTEGRATION_PLAN.md`
5. `docs/TODO_UI_DEV.md`
6. `补全LiuHao-AIOS-Hermes-Execution-Prompt.md`

---

## 1. 原文：`docs/SPRINT_PLAN_3M_V2.md`

# 鎏灏 AI OS Y1.0 — 3个月冲刺方案 V2（合并版）

**版本:** V2.1
**更新日期:** 2026-08-26
**周期:** 3个月 / 6个冲刺（2026-09-01 ~ 2026-11-30），**全量推进，工期可延后**
**目标:** 主账号指挥 AI 员工群，自动获客 + 多平台运营 + 独立站 SEO + 自我进化，交付 Y1.0 Beta
**范围决策（用户确认）:** 9 大能力全量推进 · 4 平台全接 · 获客数据源 = 社媒+搜索+海关数据

---

## 一、产品定位（合并后）

**一句话定位：** 面向外贸企业主的 AI 操作系统——主账号（老板）指挥一组内外部 AI 员工，自动完成「找客户 → 找供应商 → 多平台触达 → 独立站获客 → 数据分析 → 自我进化」的全流程。

### 核心能力矩阵

| 能力 | 说明 | 优先级 |
|---|---|---|
| 🤖 **AI 员工群** | 可自建/添加内部 AI 员工 + 接入外部 AI 员工，各有分工 | 🔥 核心 |
| 👑 **主/子账号权限** | 鎏灏只听从主账号指令，子账号受限 | 🔥 核心 |
| 📥 **资料导入** | 操作台批量导入供应商/客户/合同/报价资料 | 🔥 核心 |
| 🧲 **自动获客** | 自动寻找/推荐潜在客户 | 🔥 核心 |
| 🌐 **多平台操作** | WhatsApp / Facebook / LinkedIn / 微信 / 谷歌独立站 | 🔥 核心 |
| 🇨🇳 **国内供应商分析** | 国内找供应商 + 自动风险/价格/产能分析 | 🔥 核心 |
| 🌏 **多语言 + 粤语** | 8 种语言自动翻译，粤语优化 | 高 |
| 🧠 **元学习** | 鎏灏读取其他 AI 员工知识，增长自身能力 | 高 |
| 🔄 **自我进化** | 鎏灏自我评估并优化系统方案/流程 | 高 |
| 📈 **独立站 SEO** | 操作谷歌独立站，做谷歌 SEO | 高 |

---

## 二、6 个冲刺总览

```
阶段一 · 基础建设    阶段二 · 核心能力    阶段三 · 智能进化    阶段四 · 上线
S1 ── S2 ────────  S3 ── S4 ────────  S5 ────────────  S6
第1-2周  第3-4周    第5-6周  第7-8周    第9-10周         第11-12周
```

| 冲刺 | 周期 | 主题 | 核心交付 |
|---|---|---|---|
| **S1** | 第1-2周 | 平台基础 + 权限体系 | 主/子账号、AI 员工框架、资料导入 |
| **S2** | 第3-4周 | 多平台接入 | WhatsApp/FB/LinkedIn/微信 连接 |
| **S3** | 第5-6周 | 自动获客 + 供应商分析 | 客户发现、国内供应商数据分析 |
| **S4** | 第7-8周 | 独立站 + SEO | 谷歌独立站操作、SEO 优化 |
| **S5** | 第9-10周 | AI 员工市场 + 元学习 | 内外部 AI 员工、鎏灏自我进化 |
| **S6** | 第11-12周 | 加固 + 上线 | 安全合规、测试、Y1.0 Beta |

---

## 三、冲刺详情

### S1 · 平台基础 + 权限体系（第1-2周）
**目标：** 搭好"主账号指挥 AI 员工"的地基

- [ ] 主账号 / 子账号体系（权限分级：主账号可指挥鎏灏，子账号只读/受限）
- [ ] AI 员工注册框架（内部 AI 员工骨架）
- [ ] 操作台资料导入功能（供应商/客户/合同/报价批量导入）
- [ ] 企业基础信息 + 多租户隔离
- [ ] 验收：主账号可创建子账号并设权限；可导入一批资料

### S2 · 多平台接入（第3-4周）
**目标：** 打通全部 4 个外贸触达渠道（全量）

- [ ] WhatsApp Business API 接入（官方合规，消息收发）
- [ ] LinkedIn 接入（B2B 线索抓取 + 私信）
- [ ] 微信/企业微信接入（国内客户维护，企业微信官方接口）
- [ ] Facebook / Messenger 接入（海外社媒覆盖）
- [ ] 多语言自动翻译（8 语言 + 粤语）
- [ ] 平台统一管理面板（多账号绑定/切换）
- [ ] 验收：可从操作台向 4 平台发消息并收回复

### S3 · 自动获客 + 供应商分析（第5-6周）
**目标：** 让系统自动找客户、找供应商

- [ ] 自动获客引擎（社媒 + 谷歌搜索 + **海关数据**三路线索挖掘）
- [ ] 海关进出口数据接入（定位采购商/进口商）
- [ ] 客户管理 CRM（线索池 + 跟进提醒）
- [ ] 国内供应商发现（搜索/爬取）
- [ ] 供应商数据分析（风险 / 价格 / 产能 AI 分析）
- [ ] 验收：系统从社媒+搜索+海关数据推荐潜在客户；对国内供应商生成分析报告

### S4 · 独立站 + SEO（第7-8周）
**目标：** 建立自己的流量阵地

- [ ] 谷歌独立站操作（内容发布/管理）
- [ ] 谷歌 SEO 引擎（关键词分析、内容优化、排名跟踪）
- [ ] 独立站数据统计（访问量/转化）
- [ ] 验收：可发布独立站内容并看到 SEO 关键词排名

### S5 · AI 员工市场 + 元学习 + 自我进化（第9-10周）
**目标：** AI 员工生态与鎏灏自我成长

- [ ] AI 员工市场（用户可自建内部 AI 员工 / 添加外部 AI 员工）
- [ ] AI 员工技能包（按需安装技能）
- [ ] 元学习：鎏灏读取其他 AI 员工信息，增长自身知识
- [ ] 自我进化：鎏灏评估并优化系统方案/流程
- [ ] 验收：用户可添加新 AI 员工；鎏灏吸收其知识并自我优化

### S6 · 加固 + 上线（第11-12周）
**目标：** 交付 Y1.0 Beta

- [ ] 平台安全合规（WhatsApp/微信风控、数据隐私）
- [ ] 性能优化（API < 200ms）
- [ ] 自动化测试 + 回归
- [ ] 部署 + 监控 + 文档
- [ ] Y1.0 Beta 发布
- [ ] 验收：Beta 版可对外演示

---

## 四、明确不做（延后到 V2.0）

| 项 | 说明 |
|---|---|
| ERP / OA 对接 | 后置 |
| 第三方应用市场 | 后置 |
| 自定义知识库深度功能 | 已有基础，深度后置 |
| 移动端原生 App | Web 优先 |
| 全平台 API 全覆盖 | 先做核心 4 平台 |

---

## 五、关键里程碑

| 日期 | 里程碑 |
|---|---|
| 2026-09-14 | S1 完成：主/子账号 + AI 员工框架 + 资料导入 |
| 2026-09-28 | S2 完成：WhatsApp/FB/LinkedIn/微信 接入 |
| 2026-10-12 | S3 完成：自动获客 + 供应商分析 |
| 2026-10-26 | S4 完成：独立站 + 谷歌 SEO |
| 2026-11-09 | S5 完成：AI 员工市场 + 元学习 + 自我进化 |
| 2026-11-30 | **Y1.0 Beta 上线** |

---

## 六、风险与应对

| 风险 | 概率 | 应对 |
|---|---|---|
| WhatsApp/微信 API 封号 | 高 | 官方 API 优先 + 风控 + 备用渠道 |
| 4 平台接入技术复杂度 | 中 | S2 集中攻坚，分平台迭代 |
| 海关数据源获取成本/合规 | 中 | 评估数据供应商，先 POC |
| 独立站 SEO 见效慢 | 中 | 内容优先 + 关键词跟踪 |
| 元学习/自我进化范围过大 | 中 | S5 做最小可行版本 |
| 全量推进工期紧张 | 高 | 已确认可延后，S6 为弹性上线 |
| 权限越权风险 | 中 | RBAC 严格 + 审计 |

---

**状态:** 📋 已合并用户全部需求
**下一步:** 确认后开始 S1 实施

---

## 2. 原文：`docs/SHORTCUT_CAPABILITIES_PLAN.md`

# 短板能力推进实施计划

> **目标:** 将 4 项短板能力（动态信任 L1→L3、主动经营 L1→L3、AI 集体智能 L1→L2-L3、老板不在线 L0→L2）在现有模块内最小化扩展
> **架构:** 分阶段递进，每阶段 TDD + 回归验证后再推进下一阶段，不新建模块
> **约束:** 不新增 Phase 9/10、不改变总体架构、不新建模块、保持 571+ passed 0 failed

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `src/ai/agent_router.py` | 修改 | 实现真实信任评分 + 路由降权 |
| `src/modules/ceo_dashboard_module.py` | 修改 | 扩展业务级告警 + 摘要报告 |
| `src/knowledge/memory.py` | 修改 | 新增 Agent 经验存储/检索 |
| `src/workforce/employee.py` | 修改 | 执行链路注入经验共享 |
| `tests/ai/test_trust_scoring.py` | 新建 | 信任评分测试 |
| `tests/modules/test_business_alerts.py` | 新建 | 业务告警测试 |
| `tests/ai/test_collective_intelligence.py` | 新建 | 集体智能测试 |
| `tests/modules/test_summary_report.py` | 新建 | 摘要报告测试 |

---

## 阶段 1：动态信任体系 + 主动经营（并行）

### Task 1: 实现能力评分（基于 EmployeePerformanceModel）

**Files:**
- Modify: `src/ai/agent_router.py:136-151`
- Test: `tests/ai/test_trust_scoring.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/ai/test_trust_scoring.py
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock
from src.ai.agent_router import AgentRouter


@pytest.mark.asyncio
async def test_capability_score_with_performance_data():
    """有性能记录时返回真实 success_rate。"""
    session = MagicMock()
    router = AgentRouter(session)

    # Mock: EmployeePerformanceModel 查询返回 success_rate=0.85
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = MagicMock(success_rate=0.85)
    session.execute = AsyncMock(return_value=mock_result)

    score = await router.get_agent_capability_score(employee_id=str(uuid4()))
    assert score == 0.85


@pytest.mark.asyncio
async def test_capability_score_no_data_returns_default():
    """无性能记录时返回 0.5 默认值。"""
    session = MagicMock()
    router = AgentRouter(session)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)

    score = await router.get_agent_capability_score(employee_id=str(uuid4()))
    assert score == 0.5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/ai/test_trust_scoring.py::test_capability_score_with_performance_data -v`
Expected: FAIL (get_agent_capability_score 是同步方法且返回 1.0)

- [ ] **Step 3: 实现**

```python
# src/ai/agent_router.py — 替换 get_agent_capability_score 方法

async def get_agent_capability_score(self, employee_id: str) -> float:
    """
    基于员工历史性能数据计算能力评分。

    数据源: EmployeePerformanceModel.success_rate
    无记录时返回 0.5 中性默认值，不阻塞路由。
    """
    from sqlalchemy import select
    from ..database.models import EmployeePerformanceModel

    try:
        result = await self.session.execute(
            select(EmployeePerformanceModel.success_rate)
            .where(EmployeePerformanceModel.employee_id == employee_id)
            .order_by(EmployeePerformanceModel.period_start.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return float(row)
        return 0.5
    except Exception as e:
        logger.warning(f"Failed to get capability score for {employee_id}: {e}")
        return 0.5
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/ai/test_trust_scoring.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ai/agent_router.py tests/ai/test_trust_scoring.py
git commit -m "feat: 实现基于 EmployeePerformanceModel 的真实能力评分"
```

---

### Task 2: 实现风险评分（基于 FailureRecordModel）

**Files:**
- Modify: `src/ai/agent_router.py`
- Test: `tests/ai/test_trust_scoring.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/ai/test_trust_scoring.py — 追加

@pytest.mark.asyncio
async def test_risk_score_no_failures_returns_low_risk():
    """无失败记录时风险评分低（0.1）。"""
    session = MagicMock()
    router = AgentRouter(session)

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)

    score = await router.get_agent_risk_score(employee_id=str(uuid4()))
    assert score <= 0.2  # 无失败 = 低风险


@pytest.mark.asyncio
async def test_risk_score_with_failures():
    """有未恢复失败时风险评分升高。"""
    session = MagicMock()
    router = AgentRouter(session)

    # Mock: 通过 task_id 关联查到 3 条失败，1 条未恢复
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.total = 3
    mock_row.unrecovered = 1
    mock_result.first.return_value = mock_row
    session.execute = AsyncMock(return_value=mock_result)

    score = await router.get_agent_risk_score(employee_id=str(uuid4()))
    assert score > 0.2  # 有未恢复失败 = 风险升高
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/ai/test_trust_scoring.py::test_risk_score_no_failures_returns_low_risk -v`
Expected: FAIL (方法不存在)

- [ ] **Step 3: 实现**

```python
# src/ai/agent_router.py — 新增方法

async def get_agent_risk_score(self, employee_id: str) -> float:
    """
    基于失败恢复记录计算风险评分。

    通过 tasks.assigned_to 关联 FailureRecordModel，
    统计未恢复失败比例。无记录时返回 0.1（低风险默认）。
    """
    from sqlalchemy import select, func, text

    try:
        # 通过 task_id 关联：tasks 表中 assigned_to 含该 employee_id 的任务
        # → FailureRecordModel 中 task_id 匹配 → 统计 is_successful=False
        sql = text("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN fr.is_successful = 0 OR fr.is_successful IS NULL THEN 1 END) as unrecovered
            FROM failure_records fr
            WHERE fr.task_id IN (
                SELECT id FROM tasks WHERE assigned_to LIKE :emp_pattern
            )
        """)

        result = await self.session.execute(
            sql, {"emp_pattern": f'%{employee_id}%'}
        )
        row = result.first()

        if row and row.total > 0:
            unrecovered_ratio = row.unrecovered / row.total
            # 风险评分 = 未恢复比例（0.0-1.0，越高越危险）
            return min(unrecovered_ratio, 1.0)
        return 0.1  # 无失败 = 低风险
    except Exception as e:
        logger.warning(f"Failed to get risk score for {employee_id}: {e}")
        return 0.5
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/ai/test_trust_scoring.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ai/agent_router.py tests/ai/test_trust_scoring.py
git commit -m "feat: 实现基于 FailureRecordModel 的风险评分"
```

---

### Task 3: 实现信任评分（综合能力+风险+权限）

**Files:**
- Modify: `src/ai/agent_router.py`
- Test: `tests/ai/test_trust_scoring.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/ai/test_trust_scoring.py — 追加

@pytest.mark.asyncio
async def test_trust_score_combines_capability_and_risk():
    """信任评分综合能力和风险。"""
    session = MagicMock()
    router = AgentRouter(session)

    # Mock capability=0.8, risk=0.2
    mock_perf = MagicMock()
    mock_perf.scalar_one_or_none.return_value = MagicMock(success_rate=0.8)
    # 第一次调用返回 performance，第二次返回 failure 统计
    mock_fail = MagicMock()
    mock_fail_row = MagicMock()
    mock_fail_row.total = 5
    mock_fail_row.unrecovered = 1
    mock_fail.first.return_value = mock_fail_row

    session.execute = AsyncMock(side_effect=[mock_perf, mock_fail])

    score = await router.get_agent_trust_score(employee_id=str(uuid4()))
    assert 0.0 <= score <= 1.0
    # 能力 0.8 * 0.4 + (1-风险 0.2) * 0.3 + 权限 0.5 * 0.3 = 0.32+0.24+0.15 = 0.71
    assert score > 0.5  # 高能力低风险 = 高信任
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/ai/test_trust_scoring.py::test_trust_score_combines_capability_and_risk -v`
Expected: FAIL (方法不存在)

- [ ] **Step 3: 实现**

```python
# src/ai/agent_router.py — 新增方法

async def get_agent_trust_score(self, employee_id: str) -> float:
    """
    综合信任评分 = 能力(40%) + 风险(30%) + 权限范围(30%)。

    - 能力: get_agent_capability_score (success_rate)
    - 风险: 1 - get_agent_risk_score (低风险 = 高信任)
    - 权限范围: 基于 RBAC 权限数量归一化（默认 0.5）
    """
    capability = await self.get_agent_capability_score(employee_id)
    risk = await self.get_agent_risk_score(employee_id)

    # 权限范围评分：当前简化为 0.5（后续可从 RBAC 查询权限数量归一化）
    permission_score = 0.5

    trust = (capability * 0.4) + ((1.0 - risk) * 0.3) + (permission_score * 0.3)
    return round(min(max(trust, 0.0), 1.0), 4)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/ai/test_trust_scoring.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ai/agent_router.py tests/ai/test_trust_scoring.py
git commit -m "feat: 实现综合信任评分（能力+风险+权限）"
```

---

### Task 4: 修改路由逻辑（按信任评分排序+降权）

**Files:**
- Modify: `src/ai/agent_router.py:80-134`
- Test: `tests/ai/test_trust_scoring.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/ai/test_trust_scoring.py — 追加

@pytest.mark.asyncio
async def test_low_trust_employee_skipped_in_routing():
    """信任评分低于 0.3 的员工被跳过。"""
    session = MagicMock()
    router = AgentRouter(session)

    # 两个员工，一个高信任一个低信任
    from src.workforce.models import AIEmployee, AIEmployeeStatus, Department, Position

    good_emp = MagicMock()
    good_emp.id = uuid4()
    good_emp.name = "优秀员工"
    good_emp.department = Department.SALES
    good_emp.position = Position.SALES_REPRESENTATIVE
    good_emp.status = AIEmployeeStatus.ACTIVE

    bad_emp = MagicMock()
    bad_emp.id = uuid4()
    bad_emp.name = "低信任员工"
    bad_emp.department = Department.SALES
    bad_emp.position = Position.SALES_REPRESENTATIVE
    bad_emp.status = AIEmployeeStatus.ACTIVE

    router.registry = MagicMock()
    router.registry.list_employees = AsyncMock(return_value=[bad_emp, good_emp])

    # Mock trust scores: bad=0.1, good=0.8
    call_count = [0]
    original_trust = router.get_agent_trust_score

    async def mock_trust(employee_id):
        call_count[0] += 1
        if employee_id == str(bad_emp.id):
            return 0.1
        return 0.8

    router.get_agent_trust_score = mock_trust

    task = {"task_id": str(uuid4()), "name": "test", "agent_type": "sales", "description": "test"}
    assignment = await router.route_task(task)

    # 应选择高信任员工
    assert str(assignment.employee_id) == str(good_emp.id)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/ai/test_trust_scoring.py::test_low_trust_employee_skipped_in_routing -v`
Expected: FAIL (当前路由选第一个，不按信任评分排序)

- [ ] **Step 3: 实现**

```python
# src/ai/agent_router.py — 修改 route_task 方法（第 80-134 行）

async def route_task(self, task: Dict) -> AgentAssignment:
    """Route single task to specific AI employee, sorted by trust score."""
    agent_type = task.get("agent_type", "business")
    task_id = UUID(task["task_id"])

    mapping = self.AGENT_MAPPING.get(agent_type, self.AGENT_MAPPING["business"])

    try:
        employees = await self.registry.list_employees(
            department=mapping["department"], status=AIEmployeeStatus.ACTIVE
        )

        if employees:
            # 按信任评分排序选择：高分优先，低于 0.3 阈值的跳过
            TRUST_THRESHOLD = 0.3
            scored = []
            for emp in employees:
                trust = await self.get_agent_trust_score(str(emp.id))
                if trust >= TRUST_THRESHOLD:
                    scored.append((emp, trust))

            if not scored:
                # 所有员工都低于阈值时仍选最高的，但记录警告
                scored = [
                    (emp, await self.get_agent_trust_score(str(emp.id)))
                    for emp in employees
                ]
                logger.warning(
                    f"All employees below trust threshold {TRUST_THRESHOLD} "
                    f"for task '{task.get('name')}'"
                )

            scored.sort(key=lambda x: x[1], reverse=True)
            employee, trust_score = scored[0]

            assignment = AgentAssignment(
                task_id=task_id,
                task_description=task.get("description", task["name"]),
                agent_type=agent_type,
                employee_id=employee.id,
                employee_name=employee.name,
                department=employee.department.value,
                position=employee.position.value,
                confidence=trust_score,
                reason=f"Selected {employee.name} (trust={trust_score:.2f})",
            )
        else:
            error_msg = (
                f"No {agent_type} AI employee available for task '{task.get('name', 'unknown')}'. "
                "Register an AI employee with the required department/position before activating goals."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

    except Exception as e:
        logger.error(f"Error routing task {task_id}: {e}")
        raise ValueError(f"Failed to route task '{task.get('name', 'unknown')}': {e}") from e

    return assignment
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/ai/test_trust_scoring.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/ai/agent_router.py tests/ai/test_trust_scoring.py
git commit -m "feat: 路由按信任评分排序，低分员工自动降权跳过"
```

---

### Task 5: 实现业务异常扫描（主动经营）

**Files:**
- Modify: `src/modules/ceo_dashboard_module.py`
- Test: `tests/modules/test_business_alerts.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/modules/test_business_alerts.py
import pytest
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock
from src.modules.ceo_dashboard_module import CEODashboardModule


def test_business_anomalies_no_data_returns_empty():
    """无数据时返回空告警列表。"""
    module = CEODashboardModule()
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))

    alerts = module.scan_business_anomalies(session)
    assert alerts == []


def test_business_anomalies_lead_decline():
    """线索下降时生成 lead_decline 告警。"""
    module = CEODashboardModule()
    session = MagicMock()

    # Mock: 本周线索 2 条，上周 10 条 → 下降 80%
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (10, 2)  # (last_week, this_week)
    session.execute = AsyncMock(return_value=mock_result)

    alerts = module.scan_business_anomalies(session)
    lead_alerts = [a for a in alerts if a["type"] == "lead_decline"]
    assert len(lead_alerts) == 1
    assert lead_alerts[0]["level"] == "warning"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/modules/test_business_alerts.py -v`
Expected: FAIL (scan_business_anomalies 方法不存在)

- [ ] **Step 3: 实现**

```python
# src/modules/ceo_dashboard_module.py — 新增方法

def scan_business_anomalies(self, session) -> List[Dict[str, Any]]:
    """扫描业务异常并生成业务级告警。

    检测项：
    - lead_decline: 线索数量周环比下降超过 50%
    - customer_churn: 客户状态变为 lost/churned
    - supplier_risk_change: 供应商风险等级上升
    """
    alerts = []
    now = datetime.now(UTC)

    try:
        # 线索下降检测
        from sqlalchemy import text
        result = session.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM leads WHERE created_at >= date('now', '-7 days')) as this_week,
                (SELECT COUNT(*) FROM leads WHERE created_at >= date('now', '-14 days')
                 AND created_at < date('now', '-7 days')) as last_week
        """)).fetchone()

        if result:
            this_week, last_week = result[0] or 0, result[1] or 0
            if last_week > 0:
                decline_rate = (last_week - this_week) / last_week
                if decline_rate > 0.5:
                    alerts.append({
                        "id": "alert_lead_decline",
                        "type": "lead_decline",
                        "level": "warning",
                        "title": "线索数量下降",
                        "message": f"本周线索 {this_week} 条，较上周 {last_week} 条下降 {decline_rate*100:.0f}%",
                        "timestamp": now.isoformat(),
                    })
    except Exception as e:
        logger.warning(f"Lead decline scan failed: {e}")

    try:
        # 客户流失检测
        from sqlalchemy import text
        result = session.execute(text("""
            SELECT COUNT(*) FROM leads WHERE status = 'lost'
        """)).fetchone()
        if result and result[0] > 0:
            alerts.append({
                "id": "alert_customer_churn",
                "type": "customer_churn",
                "level": "warning",
                "title": "客户流失",
                "message": f"{result[0]} 个客户状态为流失",
                "timestamp": now.isoformat(),
            })
    except Exception as e:
        logger.warning(f"Customer churn scan failed: {e}")

    try:
        # 供应商风险变化检测
        from sqlalchemy import text
        result = session.execute(text("""
            SELECT COUNT(*) FROM suppliers WHERE risk_level = 'high'
        """)).fetchone()
        if result and result[0] > 0:
            alerts.append({
                "id": "alert_supplier_risk",
                "type": "supplier_risk_change",
                "level": "warning",
                "title": "供应商风险升高",
                "message": f"{result[0]} 个供应商风险等级为高",
                "timestamp": now.isoformat(),
            })
    except Exception as e:
        logger.warning(f"Supplier risk scan failed: {e}")

    return alerts
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/modules/test_business_alerts.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/modules/ceo_dashboard_module.py tests/modules/test_business_alerts.py
git commit -m "feat: 实现业务级异常扫描（线索下降/客户流失/供应商风险）"
```

---

### Task 6: 阶段 1 回归验证

- [ ] **Step 1: 运行完整回归测试**

Run: `python -m pytest -q`
Expected: 571+ passed, 0 failed, Failure Recovery Chain 无回归

- [ ] **Step 2: 确认 E2E 链路不受影响**

Run: `python -m pytest tests/integration/test_e2e_chain.py -v`
Expected: 5 passed

---

## 阶段 2：AI 集体智能（阶段 1 验证通过后）

### Task 7: 扩展 MemoryService 支持 Agent 经验存储

**Files:**
- Modify: `src/knowledge/memory.py`
- Test: `tests/ai/test_collective_intelligence.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/ai/test_collective_intelligence.py
import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock
from src.knowledge.memory import MemoryService


@pytest.mark.asyncio
async def test_store_agent_experience():
    """Agent 经验可写入共享知识库。"""
    session = MagicMock()
    service = MemoryService(session)
    emp_id = str(uuid4())

    result = await service.store_agent_experience(
        employee_id=emp_id,
        task_type="sales",
        result_summary="成功完成客户开发，转化率 15%"
    )
    assert result is not None


@pytest.mark.asyncio
async def test_recall_agent_experience():
    """可按 task_type 检索同类经验。"""
    session = MagicMock()
    service = MemoryService(session)

    # Mock 查询返回经验记录
    mock_result = MagicMock()
    mock_row = MagicMock()
    mock_row.content = "成功完成客户开发，转化率 15%"
    mock_row.employee_id = str(uuid4())
    mock_result.scalars.return_value.all.return_value = [mock_row]
    session.execute = AsyncMock(return_value=mock_result)

    experiences = await service.recall_agent_experience(task_type="sales", limit=5)
    assert len(experiences) >= 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/ai/test_collective_intelligence.py -v`
Expected: FAIL (方法不存在)

- [ ] **Step 3: 实现**

```python
# src/knowledge/memory.py — MemoryService 新增方法

async def store_agent_experience(
    self,
    employee_id: str,
    task_type: str,
    result_summary: str,
) -> Optional[Memory]:
    """Agent 执行成功后将经验写入共享知识库。"""
    try:
        memory = Memory(
            id=str(uuid4()),
            memory_type=MemoryType.LONG_TERM,
            content=f"[{task_type}] {result_summary}",
            metadata={"employee_id": employee_id, "task_type": task_type, "shared": True},
            created_at=datetime.now(UTC),
        )
        # 存储逻辑复用现有 MemoryService.store
        await self.store(memory)
        logger.info(f"Agent experience stored: employee={employee_id}, type={task_type}")
        return memory
    except Exception as e:
        logger.error(f"Failed to store agent experience: {e}")
        return None

async def recall_agent_experience(
    self,
    task_type: str,
    limit: int = 5,
) -> List[Memory]:
    """Agent 执行前检索同类经验。"""
    try:
        from sqlalchemy import select
        result = await self.session.execute(
            select(Memory)
            .where(Memory.metadata["task_type"].as_string() == task_type)
            .where(Memory.metadata["shared"].as_boolean() == True)
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    except Exception as e:
        logger.warning(f"Failed to recall agent experience: {e}")
        return []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/ai/test_collective_intelligence.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/knowledge/memory.py tests/ai/test_collective_intelligence.py
git commit -m "feat: MemoryService 支持 Agent 经验存储与检索"
```

---

### Task 8: 执行链路注入经验共享

**Files:**
- Modify: `src/workforce/employee.py`
- Test: `tests/ai/test_collective_intelligence.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/ai/test_collective_intelligence.py — 追加

@pytest.mark.asyncio
async def test_low_trust_agent_cannot_access_shared_experience():
    """低信任 Agent 无法读取共享经验（准入控制）。"""
    # 信任评分低于阈值时 recall 返回空列表
    session = MagicMock()
    service = MemoryService(session)
    service.trust_threshold = 0.3

    # 模拟低信任 Agent
    experiences = await service.recall_agent_experience(
        task_type="sales",
        limit=5,
        requester_trust_score=0.1,  # 低于阈值
    )
    assert experiences == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/ai/test_collective_intelligence.py::test_low_trust_agent_cannot_access_shared_experience -v`
Expected: FAIL (recall_agent_experience 不接受 requester_trust_score 参数)

- [ ] **Step 3: 实现**

修改 `recall_agent_experience` 增加准入控制参数，并在 `employee.py` 执行链路中注入调用。

```python
# src/knowledge/memory.py — 修改 recall_agent_experience 签名

async def recall_agent_experience(
    self,
    task_type: str,
    limit: int = 5,
    requester_trust_score: float = 1.0,
) -> List[Memory]:
    """Agent 执行前检索同类经验，低信任 Agent 被准入控制拦截。"""
    if requester_trust_score < getattr(self, "trust_threshold", 0.3):
        logger.info(f"Low trust ({requester_trust_score}) agent denied shared experience access")
        return []
    # ... 原有检索逻辑
```

```python
# src/workforce/employee.py — 在执行方法中注入经验共享
# 执行前检索同类经验注入 context
# 执行成功后写入经验
# 具体位置在 execute_task 方法中，依赖 AgentRouter.get_agent_trust_score 获取信任评分
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/ai/test_collective_intelligence.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/knowledge/memory.py src/workforce/employee.py tests/ai/test_collective_intelligence.py
git commit -m "feat: 执行链路注入经验共享，低信任 Agent 准入控制"
```

---

### Task 9: 阶段 2 回归验证

- [ ] **Step 1: 运行完整回归测试**

Run: `python -m pytest -q`
Expected: 571+ passed, 0 failed

---

## 阶段 3：老板长期不在线（阶段 2 验证通过后）

### Task 10: 实现经营摘要报告生成

**Files:**
- Modify: `src/modules/ceo_dashboard_module.py`
- Test: `tests/modules/test_summary_report.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/modules/test_summary_report.py
import pytest
from unittest.mock import MagicMock
from src.modules.ceo_dashboard_module import CEODashboardModule


def test_summary_report_with_no_data():
    """无数据时报告中标注'数据不足'。"""
    module = CEODashboardModule()
    session = MagicMock()
    session.execute = MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))

    report = module.generate_summary_report(session)
    assert report["status"] == "generated"
    assert "暂无" in report["goals"]["message"] or report["goals"]["count"] == 0
    assert "暂无" in report["alerts"]["message"] or len(report["alerts"]["items"]) == 0


def test_summary_report_structure():
    """报告结构完整。"""
    module = CEODashboardModule()
    session = MagicMock()

    report = module.generate_summary_report(session)
    assert "timestamp" in report
    assert "kpis" in report
    assert "alerts" in report
    assert "goals" in report
    assert "cost" in report
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/modules/test_summary_report.py -v`
Expected: FAIL (generate_summary_report 方法不存在)

- [ ] **Step 3: 实现**

```python
# src/modules/ceo_dashboard_module.py — 新增方法

def generate_summary_report(self, session) -> Dict[str, Any]:
    """生成经营摘要报告（按需触发，非离线调度）。

    聚合数据：
    - Dashboard 核心 KPI
    - 主动经营告警（scan_business_anomalies 输出）
    - Goal 执行状态和进度
    - AI 成本统计
    """
    now = datetime.now(UTC)
    report = {
        "timestamp": now.isoformat(),
        "status": "generated",
        "kpis": {"items": self._get_kpis_dict()},
        "alerts": {"items": [], "message": "暂无异常"},
        "goals": {"count": 0, "message": "暂无目标"},
        "cost": {"total_usd": 0.0, "message": "暂无成本数据"},
    }

    try:
        # 业务告警
        alerts = self.scan_business_anomalies(session)
        if alerts:
            report["alerts"] = {"items": alerts, "message": f"{len(alerts)} 条告警"}
    except Exception as e:
        logger.warning(f"Alert scan in report failed: {e}")

    try:
        # Goal 进度
        from sqlalchemy import text
        result = session.execute(text("SELECT COUNT(*) FROM goals")).fetchone()
        goal_count = result[0] if result else 0
        if goal_count > 0:
            report["goals"] = {"count": goal_count, "message": f"{goal_count} 个目标"}
    except Exception as e:
        logger.warning(f"Goal query in report failed: {e}")

    try:
        # 成本统计
        from sqlalchemy import text
        result = session.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_cost_records")).fetchone()
        total_cost = float(result[0]) if result else 0.0
        if total_cost > 0:
            report["cost"] = {"total_usd": total_cost, "message": f"${total_cost:.2f}"}
    except Exception as e:
        logger.warning(f"Cost query in report failed: {e}")

    return report
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/modules/test_summary_report.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/modules/ceo_dashboard_module.py tests/modules/test_summary_report.py
git commit -m "feat: 实现按需触发的经营摘要报告生成"
```

---

### Task 11: 最终回归验证

- [ ] **Step 1: 运行完整回归测试**

Run: `python -m pytest -q`
Expected: 571+ passed, 0 failed

- [ ] **Step 2: 运行 E2E 链路验证**

Run: `python -m pytest tests/integration/test_e2e_chain.py -v`
Expected: 5 passed

- [ ] **Step 3: 确认 Failure Recovery Chain 无回归**

检查失败恢复链相关测试全部通过，无新增失败。

---

## 3. 原文：`docs/PLATFORM_INTEGRATION_PLAN.md`

# 外部平台集成详细实施计划

> 版本: 1.0  
> 计划日期: 2026-08-27  
> 当前状态: 框架已存在，需补充真实业务能力

---

## 目录

1. [现状评估](#1-现状评估)
2. [总体架构](#2-总体架构)
3. [实施路线图](#3-实施路线图)
4. [Phase 1: 凭据管理 + 配置化](#4-phase-1-凭据管理--配置化)
5. [Phase 2: WhatsApp 真实集成](#5-phase-2-whatsapp-真实集成)
6. [Phase 3: 企业微信真实集成](#6-phase-3-企业微信真实集成)
7. [Phase 4: Facebook + LinkedIn 集成](#7-phase-4-facebook--linkedin-集成)
8. [Phase 5: 统一收件箱](#8-phase-5-统一收件箱)
9. [Phase 6: 自动化工作流](#9-phase-6-自动化工作流)
10. [Phase 7: 数据同步 + CRM 联动](#10-phase-7-数据同步--crm-联动)
11. [Phase 8: 前端页面](#11-phase-8-前端页面)
12. [Phase 9: 测试 + 部署](#12-phase-9-测试--部署)
13. [API 凭证获取指南](#13-api-凭证获取指南)
14. [风险与依赖](#14-风险与依赖)
15. [工作量估算](#15-工作量估算)

---

## 1. 现状评估

### 1.1 已实现内容

| 模块 | 文件 | 完成度 | 说明 |
|------|------|--------|------|
| 数据模型 | `src/integrations/models.py` | 100% | PlatformAccount/PlatformMessage/PlatformContact 三表完整 |
| 抽象基类 | `src/integrations/base.py` | 100% | PlatformProvider 抽象类 + PlatformRegistry 注册中心 |
| Provider 实现 | `src/integrations/providers.py` | 90% | 4 个真实 Provider + 1 个 MockProvider |
| 平台服务 | `src/integrations/service.py` | 85% | 账号管理/消息收发/联系人/翻译 完整 |
| API 路由 | `src/api/routes/platforms.py` | 90% | 13 个 REST 端点，含权限控制 |
| 翻译服务 | `src/integrations/translation.py` | 80% | 多语言翻译接口完整 |

### 1.2 缺失内容

| 模块 | 优先级 | 现状 | 影响 |
|------|--------|------|------|
| Webhook 接收 | P0 | ❌ 缺失 | 无法接收平台消息 |
| 凭据管理 UI | P0 | ❌ 缺失 | 无法配置真实 API 凭据 |
| 前端平台管理页 | P0 | ❌ 缺失 | 用户无法操作平台 |
| 统一收件箱 | P1 | ❌ 缺失 | 消息分散无法管理 |
| OAuth 流程 | P1 | ❌ 缺失 | 部分平台需 OAuth 授权 |
| 消息模板 | P1 | ❌ 缺失 | WhatsApp 模板消息 |
| CRM 联动 | P1 | ❌ 缺失 | 平台消息→线索 自动化 |
| 自动化工作流 | P2 | ❌ 缺失 | 定时/触发式消息发送 |
| 消息分析 | P2 | ❌ 缺失 | 发送量/回复率等统计 |
| 速率限制 | P2 | ❌ 缺失 | 平台 API 调用配额管理 |

### 1.3 代码质量评估

```
现有代码质量: 🟢 良好

src/integrations/providers.py — 4 个真实 Provider 已实现 API 调用逻辑
  - WhatsAppProvider:   Graph API v19.0, 发送/测试连接 已实现
  - FacebookProvider:   Graph API v19.0, 发送/测试连接 已实现
  - LinkedInProvider:   REST API v2, 发送/测试连接 已实现
  - WeChatWorkProvider: 企业微信 API, 发送/测试连接/Token 管理 已实现

src/integrations/service.py — 服务层编排完整
  - 账号 CRUD ✅
  - 消息发送 + 自动翻译 ✅
  - Mock 模式自动回复 ✅
  - 联系人同步 ✅
  - 权限控制接口已预留 ✅

src/api/routes/platforms.py — 13 个 API 端点
  - 账号管理: 创建/列表/更新/删除/切换/测试连接 (6)
  - 消息: 发送/列表/接收/搜索 (4)
  - 联系人: 列表/同步 (2)
  - 工具: 翻译 (1)
```

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (React)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ 平台管理页面   │  │ 统一收件箱    │  │ 消息分析仪表盘    │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
└─────────┼─────────────────┼────────────────────┼────────────┘
          │                 │                    │
┌─────────▼─────────────────▼────────────────────▼────────────┐
│                     API 层 (FastAPI)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ /platforms/* │  │ /webhooks/*  │  │ /crm/leads/*     │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
└─────────┼─────────────────┼────────────────────┼────────────┘
          │                 │                    │
┌─────────▼─────────────────▼────────────────────▼────────────┐
│                    服务层 (Service)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ PlatformSvc  │  │ WebhookSvc   │  │  CRM Service     │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
└─────────┼─────────────────┼────────────────────┼────────────┘
          │                 │                    │
┌─────────▼─────────────────▼────────────────────▼────────────┐
│                   Provider 层 (集成)                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐  │
│  │ WhatsApp │ │ Facebook │ │ LinkedIn │ │ 企业微信      │  │
│  │ Provider │ │ Provider │ │ Provider │ │ Provider      │  │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬────────┘  │
│       │            │            │              │           │
│  ┌────▼────────────▼────────────▼──────────────▼────────┐  │
│  │              MockProvider (开发回退)                   │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │                 │                    │
┌─────────▼─────────────────▼────────────────────▼────────────┐
│                   外部平台 API                               │
│  WhatsApp │ Facebook │ LinkedIn │ 企业微信 │ ...            │
│  Cloud API│ Graph API│ REST API │ 企业微信API               │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 实施路线图

```
Phase 1: 凭据管理 + 配置化     ████████████░░░░░░  1-2 天
Phase 2: WhatsApp 真实集成     ████████████████░░  2-3 天
Phase 3: 企业微信真实集成        ████████████████░░  2-3 天
Phase 4: Facebook + LinkedIn   ██████████░░░░░░░░  1-2 天
Phase 5: 统一收件箱             ████████████████░░  2-3 天
Phase 6: 自动化工作流           ██████████████████  2-3 天
Phase 7: 数据同步 + CRM 联动    ██████████████░░░░  1-2 天
Phase 8: 前端页面               ██████████████████  2-3 天
Phase 9: 测试 + 部署            ██████████░░░░░░░░  1-2 天
                                   总计: 14-22 天
```

---

## 4. Phase 1: 凭据管理 + 配置化

### 4.1 目标

将 API 凭据从硬编码/环境变量迁移到数据库加密存储，提供凭据管理 API。

### 4.2 新增文件

| 文件 | 用途 |
|------|------|
| `src/core/encryption.py` | 凭据加密/解密工具（AES-256-GCM） |
| `src/api/routes/credentials.py` | 凭据管理 API |

### 4.3 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/integrations/service.py` | `_get_provider()` 增加凭据解密逻辑 |
| `.env` | 增加 `ENCRYPTION_KEY` 环境变量 |
| `src/database/models.py` | 增加 `Credential` 表（可选，复用 `PlatformAccount.credentials` JSON 字段） |

### 4.4 实现细节

```python
# src/core/encryption.py
from cryptography.fernet import Fernet
import os

def get_encryption_key() -> bytes:
    """从环境变量获取加密密钥"""
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY 未配置")
    return key.encode()

def encrypt_credentials(credentials: dict) -> dict:
    """加密敏感凭据字段"""
    cipher = Fernet(get_encryption_key())
    encrypted = {}
    for k, v in credentials.items():
        if k in ("access_token", "token", "secret", "app_secret"):
            encrypted[k] = cipher.encrypt(v.encode()).decode()
        else:
            encrypted[k] = v
    return encrypted

def decrypt_credentials(credentials: dict) -> dict:
    """解密凭据"""
    cipher = Fernet(get_encryption_key())
    decrypted = {}
    for k, v in credentials.items():
        if k in ("access_token", "token", "secret", "app_secret"):
            decrypted[k] = cipher.decrypt(v.encode()).decode()
        else:
            decrypted[k] = v
    return decrypted
```

### 4.5 验收标准

- [ ] 凭据加密存储到数据库
- [ ] 读取时自动解密
- [ ] 未配置 ENCRYPTION_KEY 时自动回退 Mock 模式
- [ ] 凭据管理 API 可用（CRUD）

---

## 5. Phase 2: WhatsApp 真实集成

### 5.1 目标

让 WhatsApp Business API 真实可用：发送消息、接收消息（Webhook）、模板消息。

### 5.2 新增文件

| 文件 | 用途 |
|------|------|
| `src/api/routes/webhooks.py` | Webhook 接收端点 |
| `src/integrations/webhook.py` | Webhook 服务（消息解析 + 入库） |
| `src/integrations/templates.py` | 消息模板管理 |

### 5.3 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/integrations/providers.py` | `WhatsAppProvider.fetch_messages()` 改为通过 Webhook 读取 |
| `src/integrations/service.py` | 增加 `process_webhook()` 方法 |
| `src/api/routes/__init__.py` | 注册 webhooks 路由 |

### 5.4 实现细节

#### 5.4.1 Webhook 接收端点

```python
# src/api/routes/webhooks.py
@router.post("/whatsapp/{account_id}")
async def whatsapp_webhook(
    account_id: int,
    body: dict,
    session: AsyncSession = Depends(get_db),
):
    """接收 WhatsApp Cloud API 的 Webhook 回调"""
    service = PlatformService(session)
    await service.process_webhook(account_id, body)
    return {"status": "ok"}

@router.get("/whatsapp/{account_id}")
async def whatsapp_webhook_verify(
    account_id: int,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """WhatsApp Webhook 验证（Meta 要求）"""
    expected_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    if hub_mode == "subscribe" and hub_verify_token == expected_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="验证失败")
```

#### 5.4.2 消息模板

```python
# src/integrations/templates.py
WHATSAPP_TEMPLATES = {
    "welcome": {
        "name": "welcome_message",
        "language": "zh_CN",
        "components": [
            {"type": "HEADER", "parameters": [{"type": "text", "text": "{{1}}"}]},
            {"type": "BODY", "parameters": [{"type": "text", "text": "{{1}}"}]},
        ],
    },
    "order_update": {
        "name": "order_update",
        "language": "zh_CN",
        "components": [...],
    },
}
```

### 5.5 配置要求

```bash
# .env
WHATSAPP_VERIFY_TOKEN=your_verify_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_ACCESS_TOKEN=your_access_token
```

### 5.6 验收标准

- [ ] 发送真实 WhatsApp 消息到联系人
- [ ] Webhook 接收消息并入库
- [ ] 消息模板管理（创建/列表/发送）
- [ ] 测试连接返回真实状态
- [ ] Mock 模式自动回退

---

## 6. Phase 3: 企业微信真实集成

### 6.1 目标

企业微信消息收发真实可用。

### 6.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/integrations/providers.py` | `WeChatWorkProvider.fetch_messages()` 实现主动拉取 + 增加回调接收 |
| `src/api/routes/webhooks.py` | 增加企业微信 Webhook 回调端点 |

### 6.3 实现细节

企业微信支持两种消息接收方式：
1. **主动拉取** — 调用 `cgi-bin/message/list` 接口（需企业微信服务商）
2. **回调模式** — 配置 HTTP 回调 URL，企业微信推送消息

建议先实现 **主动拉取**，Webhook 回调作为后续优化。

### 6.4 配置要求

```bash
# .env
WECHAT_CORP_ID=your_corp_id
WECHAT_AGENT_SECRET=your_agent_secret
WECHAT_AGENT_ID=your_agent_id
WECHAT_CALLBACK_TOKEN=your_callback_token
WECHAT_CALLBACK_AES_KEY=your_aes_key
```

### 6.5 验收标准

- [ ] 发送真实企业微信消息
- [ ] 主动拉取收件消息
- [ ] 测试连接返回真实状态
- [ ] Mock 模式自动回退

---

## 7. Phase 4: Facebook + LinkedIn 集成

### 7.1 目标

Facebook Messenger 和 LinkedIn 消息收发真实可用。

### 7.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/integrations/providers.py` | 完善 `FacebookProvider` 和 `LinkedInProvider` |
| `src/api/routes/webhooks.py` | 增加 Facebook Webhook 端点 |

### 7.3 实现细节

#### Facebook Messenger
- 需创建 Facebook Page 并获取 Page Access Token
- 通过 Graph API `me/messages` 发送消息
- 通过 Webhook 接收消息（`messages` 字段）
- 需配置 Webhook 订阅（page_messaging 字段）

#### LinkedIn
- 需通过 LinkedIn Developer Portal 创建应用
- 获取 OAuth 2.0 access token（权限: `w_messaging`）
- 调用 `v2/messages` 发送消息
- LinkedIn 消息接收仅支持 Webhook（需申请）

### 7.4 权限要求

| 平台 | 所需权限 |
|------|----------|
| Facebook | `pages_messaging`, `pages_manage_metadata` |
| LinkedIn | `w_messaging`, `r_liteprofile` |

### 7.5 验收标准

- [ ] Facebook Messenger 发送消息
- [ ] Facebook Webhook 接收消息
- [ ] LinkedIn 发送消息
- [ ] 测试连接返回真实状态

---

## 8. Phase 5: 统一收件箱

### 8.1 目标

将四个平台的消息聚合到统一收件箱，支持跨平台回复。

### 8.2 新增文件

| 文件 | 用途 |
|------|------|
| `frontend/src/pages/InboxPage.tsx` | 统一收件箱页面 |
| `frontend/src/services/inbox.ts` | 收件箱 API 服务 |
| `src/api/routes/inbox.py` | 统一收件箱 API |

### 8.3 实现细节

#### 后端 API

```python
# src/api/routes/inbox.py
@router.get("/inbox")
async def get_inbox(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """统一收件箱：跨平台消息聚合"""
    service = PlatformService(session)
    return await service.get_unified_inbox(
        user_ids=visible_user_ids(current_user),
        platform=platform,
        status=status,
        keyword=q,
        page=page,
        page_size=page_size,
    )
```

#### 前端页面

```
统一收件箱布局:
┌─────────────────────────────────────────────────────┐
│ 🔍 搜索  [平台筛选: 全部|WhatsApp|微信|...]         │
├─────────────────┬───────────────────────────────────┤
│  联系人列表      │  消息详情                         │
│                 │                                   │
│  ● WhatsApp     │  ┌─────────────────────────────┐  │
│   张三 (3条未读) │  │ 对方: 收到报价了吗？         │  │
│  ● 企业微信      │  │ 我: 已发送，请查收           │  │
│   李四 (1条未读) │  │ 对方: 好的，谢谢             │  │
│  ○ Facebook     │  └─────────────────────────────┘  │
│   Mark (已读)   │                                   │
│                 │  ┌─────────────────────────────┐  │
│                 │  │ 输入框... [发送] [翻译]     │  │
│                 │  └─────────────────────────────┘  │
├─────────────────┴───────────────────────────────────┤
│  📊 今日消息: 12  待回复: 5  回复率: 58%            │
└─────────────────────────────────────────────────────┘
```

### 8.4 验收标准

- [ ] 跨平台消息聚合展示
- [ ] 平台筛选
- [ ] 关键词搜索
- [ ] 未读标记
- [ ] 统一回复接口
- [ ] 消息统计

---

## 9. Phase 6: 自动化工作流

### 9.1 目标

将平台消息发送集成到 Workflow 引擎，实现自动化触发。

### 9.2 新增文件

| 文件 | 用途 |
|------|------|
| `src/integrations/automation.py` | 自动化规则引擎 |
| `src/workflow/actions/send_message.py` | Workflow 动作：发送消息 |

### 9.3 实现细节

#### 自动化规则

```python
# src/integrations/automation.py
class AutomationRule(BaseModel):
    name: str
    trigger: TriggerType  # NEW_LEAD, NEW_MESSAGE, SCHEDULED
    platform: Optional[str]
    action: ActionType  # SEND_MESSAGE, CREATE_LEAD, UPDATE_CRM
    template: Optional[str]
    filters: dict

class AutomationService:
    async def evaluate(self, event: dict) -> List[AutomationRule]:
        """评估事件匹配的规则"""

    async def execute(self, rule: AutomationRule, context: dict):
        """执行规则"""
```

#### 预置规则示例

| 规则名称 | 触发条件 | 动作 |
|----------|----------|------|
| 新客户自动欢迎 | CRM 创建新线索 | 通过 WhatsApp 发送欢迎消息 |
| 询价自动回复 | 收到供应商消息 | 自动回复"已收到询价，正在处理" |
| 每日跟进提醒 | 定时任务（每天 9:00） | 发送跟进消息给待跟进客户 |
| 异常告警 | 平台连接断开 | 发送企业微信消息给管理员 |

### 9.4 验收标准

- [ ] 自动化规则 CRUD
- [ ] 预置规则 4 条
- [ ] 规则触发执行
- [ ] 执行日志
- [ ] 手动/自动暂停

---

## 10. Phase 7: 数据同步 + CRM 联动

### 10.1 目标

平台联系人与 CRM 线索双向同步，消息自动创建线索活动。

### 10.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `src/integrations/service.py` | 增加 `sync_to_crm()` 方法 |
| `src/crm/service.py` | 增加 `import_from_platform()` 方法 |

### 10.3 实现细节

#### 平台联系人 → CRM 线索

```python
async def sync_contacts_to_crm(self, account_id: int, owner_user_id: int):
    """将平台联系人同步为 CRM 线索"""
    contacts = await self.list_contacts(account_id, owner_user_id)
    for c in contacts:
        # 检查是否已存在
        existing = await lead_service.find_by_phone(c.phone)
        if not existing:
            await lead_service.create_lead(
                name=c.name,
                phone=c.phone,
                source=f"platform:{c.platform.value}",
                owner_user_id=owner_user_id,
            )
```

#### 消息 → 线索活动

```python
async def message_to_activity(self, message: PlatformMessage):
    """平台消息自动创建为 CRM 线索活动"""
    lead = await lead_service.find_by_phone(message.from_id)
    if lead:
        await lead_service.add_activity(
            lead_id=lead.id,
            type="message",
            description=f"[{message.platform.value}] {message.content[:100]}",
        )
```

### 10.4 验收标准

- [ ] 平台联系人可一键导入 CRM 线索
- [ ] 收到的消息自动关联到对应线索
- [ ] 去重（同一联系人不会重复导入）
- [ ] 同步日志

---

## 11. Phase 8: 前端页面

### 11.1 目标

完整的平台管理前端页面。

### 11.2 新增/修改文件

| 文件 | 用途 |
|------|------|
| `frontend/src/pages/PlatformManagementPage.tsx` | 平台管理总页面 |
| `frontend/src/pages/InboxPage.tsx` | 统一收件箱 |
| `frontend/src/pages/MessageTemplatesPage.tsx` | 消息模板管理 |
| `frontend/src/pages/AutomationRulesPage.tsx` | 自动化规则管理 |
| `frontend/src/services/platforms.ts` | 平台 API 服务 |
| `frontend/src/services/inbox.ts` | 收件箱 API 服务 |
| `frontend/src/services/templates.ts` | 模板 API 服务 |

### 11.3 页面路由

```
/platforms          → 平台管理总页面（列表 + 创建 + 配置）
/platforms/inbox    → 统一收件箱
/platforms/templates → 消息模板管理
/platforms/automation → 自动化规则
```

### 11.4 页面设计

#### 平台管理总页面

```
┌─────────────────────────────────────────────────────────┐
│  AI 外部连接中心                                         │
│  连接状态: 4 平台 / 2 已配置                              │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ WhatsApp │  │ 企业微信  │  │ Facebook │  │LinkedIn│ │
│  │ 🟢 已连接 │  │ 🟡 未配置 │  │ 🔴 未连接 │  │ ⚪ 未配置│ │
│  │ 3 联系人  │  │ 0 联系人  │  │ 0 联系人  │  │ 0 联系人│ │
│  │ 12 消息   │  │ 0 消息    │  │ 0 消息    │  │ 0 消息  │ │
│  │ [配置]    │  │ [配置]    │  │ [配置]    │  │ [配置]  │ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
├─────────────────────────────────────────────────────────┤
│  📊 平台消息统计                                         │
│  今日发送: 8  今日接收: 12  待回复: 3  回复率: 60%       │
│  ┌─────────────────────────────────────────────────┐     │
│  │ ████████████████░░░░░░░░░░░░░░░ 60%             │     │
│  └─────────────────────────────────────────────────┘     │
├─────────────────────────────────────────────────────────┤
│  📋 最近消息                                              │
│  WhatsApp · 张三 · "收到报价了吗？" · 2 分钟前           │
│  企业微信 · 李四 · "订单已确认" · 15 分钟前               │
│  Facebook · Mark · "Can you ship to USA?" · 1 小时前    │
├─────────────────────────────────────────────────────────┤
│  [⚡ 创建平台账号]  [📥 统一收件箱]  [🤖 自动化规则]     │
└─────────────────────────────────────────────────────────┘
```

### 11.5 验收标准

- [ ] 平台列表展示（4 平台）
- [ ] 创建/配置平台账号
- [ ] 测试连接
- [ ] 启用/停用
- [ ] 删除账号
- [ ] 统一收件箱
- [ ] 消息模板管理
- [ ] 自动化规则管理

---

## 12. Phase 9: 测试 + 部署

### 12.1 测试计划

#### 单元测试

| 测试文件 | 测试内容 | 预计数量 |
|----------|----------|----------|
| `tests/integration/test_platforms.py` | 平台账号 CRUD | 10 |
| `tests/integration/test_messages.py` | 消息发送/接收 | 8 |
| `tests/integration/test_webhooks.py` | Webhook 处理 | 6 |
| `tests/integration/test_automation.py` | 自动化规则 | 8 |
| `tests/test_encryption.py` | 凭据加密 | 4 |

#### 集成测试

| 测试场景 | 说明 |
|----------|------|
| 创建平台账号 → 测试连接 → 发送消息 → 接收消息 | 完整消息链路 |
| 创建自动化规则 → 触发条件 → 执行动作 | 规则引擎验证 |
| 平台联系人 → 同步 CRM → 创建线索 | 数据同步链路 |

#### Mock 测试

所有外部 API 调用使用 `httpx.MockTransport` 或 `responses` 库模拟。

### 12.2 部署配置

```yaml
# docker-compose.yml 新增
services:
  webhook:
    build: .
    ports:
      - "8001:8000"  # Webhook 接收端口（与主 API 分离）
    env_file:
      - .env.production
    depends_on:
      - db
```

### 12.3 验收标准

- [ ] 单元测试 36+ 通过
- [ ] 集成测试 3+ 通过
- [ ] 回归测试 153+ 通过
- [ ] 前端测试 94+ 通过
- [ ] Docker 部署正常

---

## 13. API 凭证获取指南

### 13.1 WhatsApp Business API

```
1. 前往 https://business.facebook.com/ 创建 Business 账号
2. 前往 https://developers.facebook.com/ 创建应用
3. 添加 WhatsApp 产品
4. 设置 Webhook（配置回调 URL + 验证令牌）
5. 生成 Access Token（权限: whatsapp_business_messaging）
6. 获取 Phone Number ID（测试号或申请正式号）
```

### 13.2 企业微信

```
1. 登录 https://work.weixin.qq.com/ 管理后台
2. 创建应用（应用管理 → 自建 → 创建应用）
3. 获取 Corp ID（我的企业 → 企业信息）
4. 获取 Agent ID 和 Secret（应用详情页）
5. 配置回调 URL（应用详情 → 接收消息 → 设置 API 接收）
```

### 13.3 Facebook Messenger

```
1. 创建 Facebook Page
2. 在 Facebook Developers 创建应用
3. 添加 Messenger 产品
4. 生成 Page Access Token
5. 配置 Webhook（订阅 messages 字段）
6. 提交应用审核（如需要公开发布）
```

### 13.4 LinkedIn

```
1. 前往 https://developer.linkedin.com/ 创建应用
2. 获取 Client ID 和 Client Secret
3. 设置 OAuth 2.0 重定向 URL
4. 请求权限: w_messaging, r_liteprofile
5. 获取 Access Token（通过 OAuth 流程）
6. 提交应用审核（如需要公开发布）
```

---

## 14. 风险与依赖

### 14.1 风险

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 平台 API 变更 | 中 | 抽象 Provider 层，变更只需修改 Provider 实现 |
| API 配额限制 | 中 | 实现速率限制和配额管理 |
| OAuth Token 过期 | 中 | 实现 Token 刷新机制 |
| Webhook 不可达（内网） | 高 | 使用 ngrok/Cloudflare Tunnel 暴露本地服务 |
| 平台审核周期长 | 中 | 先用 Mock 模式开发，审核通过后切换 |
| 数据隐私合规 | 中 | 凭据加密存储，消息内容权限控制 |

### 14.2 外部依赖

| 依赖 | 用途 | 版本要求 |
|------|------|----------|
| `httpx` | 异步 HTTP 请求 | >= 0.27 |
| `cryptography` | 凭据加密 | >= 41.0 |
| `ngrok` | 本地 Webhook 调试（可选） | 最新版 |

### 14.3 前置条件

- [ ] 至少一个平台的真实 API 凭证
- [ ] 公网可访问的 Webhook 接收地址（或 ngrok）
- [ ] 数据库迁移（新增表已存在，无需迁移）

---

## 15. 工作量估算

### 15.1 总工时

| Phase | 描述 | 前端 | 后端 | 测试 | 合计 |
|-------|------|------|------|------|------|
| 1 | 凭据管理 + 配置化 | 0.5 | 1 | 0.5 | **2 天** |
| 2 | WhatsApp 真实集成 | 0 | 2 | 0.5 | **2.5 天** |
| 3 | 企业微信真实集成 | 0 | 1.5 | 0.5 | **2 天** |
| 4 | Facebook + LinkedIn | 0 | 1.5 | 0.5 | **2 天** |
| 5 | 统一收件箱 | 2 | 1 | 0.5 | **3.5 天** |
| 6 | 自动化工作流 | 0.5 | 1.5 | 0.5 | **2.5 天** |
| 7 | 数据同步 + CRM 联动 | 0 | 1 | 0.5 | **1.5 天** |
| 8 | 前端页面 | 2 | 0 | 0.5 | **2.5 天** |
| 9 | 测试 + 部署 | 0 | 0.5 | 1 | **1.5 天** |
| **总计** | | **5 天** | **10 天** | **5 天** | **20 天** |

### 15.2 并行策略

```
Phase 1 + Phase 2 + Phase 3 = 并行（不同开发者）
  ↓
Phase 4 + Phase 8 = 并行（后端 + 前端）
  ↓
Phase 5 + Phase 6 + Phase 7 = 并行（不同开发者）
  ↓
Phase 9 = 收尾
```

### 15.3 建议执行顺序

| 顺序 | Phase | 原因 |
|------|-------|------|
| 1 | Phase 1 | 基础，必须先做 |
| 2 | Phase 2 | 最高优先级平台（WhatsApp 是外贸最常用） |
| 3 | Phase 3 | 第二优先级（中国企业微信高频） |
| 4 | Phase 5 | 统一收件箱提升用户体验最大 |
| 5 | Phase 8 | 前端页面（与 Phase 5 并行） |
| 6 | Phase 7 | CRM 联动（业务价值高） |
| 7 | Phase 6 | 自动化（锦上添花） |
| 8 | Phase 4 | 最低优先级（Facebook/LinkedIn 使用频率较低） |
| 9 | Phase 9 | 收尾 |

---

## 附录

### A. 现有 API 端点清单

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| GET | `/api/v1/platforms/accounts` | 平台账号列表 | ✅ 已存在 |
| POST | `/api/v1/platforms/accounts` | 创建平台账号 | ✅ 已存在 |
| DELETE | `/api/v1/platforms/accounts/{id}` | 删除平台账号 | ✅ 已存在 |
| PATCH | `/api/v1/platforms/accounts/{id}` | 更新平台账号 | ✅ 已存在 |
| POST | `/api/v1/platforms/accounts/{id}/toggle` | 启用/停用 | ✅ 已存在 |
| POST | `/api/v1/platforms/accounts/{id}/test` | 测试连接 | ✅ 已存在 |
| POST | `/api/v1/platforms/accounts/{id}/messages` | 发送消息 | ✅ 已存在 |
| GET | `/api/v1/platforms/accounts/{id}/messages` | 消息列表 | ✅ 已存在 |
| POST | `/api/v1/platforms/accounts/{id}/receive` | 接收消息 | ✅ 已存在 |
| GET | `/api/v1/platforms/accounts/{id}/messages/search` | 搜索消息 | ✅ 已存在 |
| GET | `/api/v1/platforms/accounts/{id}/contacts` | 联系人列表 | ✅ 已存在 |
| POST | `/api/v1/platforms/accounts/{id}/contacts/sync` | 同步联系人 | ✅ 已存在 |
| GET | `/api/v1/platforms/languages` | 支持的语言 | ✅ 已存在 |
| POST | `/api/v1/platforms/translate` | 翻译 | ✅ 已存在 |

### B. 新增 API 端点清单

| 方法 | 路径 | 说明 | Phase |
|------|------|------|-------|
| POST | `/api/v1/webhooks/whatsapp/{account_id}` | WhatsApp Webhook 接收 | 2 |
| GET | `/api/v1/webhooks/whatsapp/{account_id}` | WhatsApp Webhook 验证 | 2 |
| POST | `/api/v1/webhooks/wechat/{account_id}` | 企业微信回调 | 3 |
| POST | `/api/v1/webhooks/facebook/{account_id}` | Facebook Webhook 接收 | 4 |
| GET | `/api/v1/webhooks/facebook/{account_id}` | Facebook Webhook 验证 | 4 |
| GET | `/api/v1/platforms/inbox` | 统一收件箱 | 5 |
| GET | `/api/v1/platforms/inbox/stats` | 收件箱统计 | 5 |
| POST | `/api/v1/platforms/inbox/{message_id}/reply` | 回复消息 | 5 |
| POST | `/api/v1/platforms/templates` | 创建消息模板 | 2 |
| GET | `/api/v1/platforms/templates` | 消息模板列表 | 2 |
| POST | `/api/v1/platforms/templates/{id}/send` | 发送模板消息 | 2 |
| POST | `/api/v1/platforms/automation/rules` | 创建自动化规则 | 6 |
| GET | `/api/v1/platforms/automation/rules` | 规则列表 | 6 |
| PUT | `/api/v1/platforms/automation/rules/{id}` | 更新规则 | 6 |
| DELETE | `/api/v1/platforms/automation/rules/{id}` | 删除规则 | 6 |
| POST | `/api/v1/platforms/automation/rules/{id}/toggle` | 启用/停用规则 | 6 |
| GET | `/api/v1/platforms/automation/logs` | 执行日志 | 6 |
| POST | `/api/v1/platforms/sync-crm` | 平台联系人→CRM 同步 | 7 |

### C. 现有代码快速参考

```
src/integrations/
├── __init__.py
├── base.py              # PlatformProvider (ABC) + PlatformRegistry
├── models.py            # PlatformAccount / PlatformMessage / PlatformContact
├── providers.py         # WhatsAppProvider / FacebookProvider / LinkedInProvider / WeChatWorkProvider / MockPlatformProvider
├── service.py           # PlatformService (账号管理 + 消息收发 + 联系人 + 翻译)
└── translation.py       # TranslationService

src/api/routes/
├── platforms.py         # 13 个 REST API 端点
└── webhooks.py          # ❌ 待创建
```

*报告完*

---

## 4. 原文：`docs/OPENSOURCE_INTEGRATION_PLAN.md`

# LiuHao AI OS (镭灏) — 开源项目集成计划

**基线**: Y1 90.3% 完成 (MASTER_BLUEPRINT_Y1_FINAL.md)  
**目标**: 引入成熟开源组件，补齐能力短板，向 JARVIS 级助手演进  
**原则**: 能用成熟库不造轮子、License 兼容、MCP/A2A 互操作优先、保持架构解耦

---

## 1. 集成优先级矩阵

| 优先级 | 项目 | 替换/增强模块 | 核心价值 | 预估工时 | 风险等级 |
|--------|------|---------------|----------|----------|----------|
| **P0-1** | **Mem0** (mem0ai/mem0) | `src/knowledge/memory.py` (10层记忆) | 多级记忆、自适应个性化、MCP原生、CLI/SDK双模 | 2-3天 | 低 (MIT, 活跃) |
| **P0-2** | **LangGraph** (langchain-ai/langgraph) | `src/ai/goal_task_graph.py` + `src/workflow/` | DAG状态机、持久化检查点、HITL、生产部署文档完善 | 3-5天 | 低 (MIT, 41K⭐) |
| **P1-1** | **Phoenix** (Arize-ai/phoenix) | `src/observability/` + 新增评估层 | OTel tracing + LLM-as-judge + drift detection 一体化 | 2-3天 | 低 (Apache-2.0) |
| **P1-2** | **MCP** (modelcontextprotocol) | `src/ai/tools.py` (Tool System) | 标准化Agent↔Tool协议、100+现成工具、生态互操作 | 2-3天 | 低 (MIT, 标准化) |
| **P2-1** | **Ragas** / **DeepEval** | CI/CD 质量门控 | RAG/通用评估、回归测试、CI集成 | 1-2天 | 低 |
| **P2-2** | **CrewAI** / **AutoGen** | `src/ai/employee.py` (多Agent编排) | 角色制协作、复杂拓扑、代码执行Agent | 3-4天 | 中 (参考而非直接替换) |
| **P3** | **Composio** / **E2B** | 工具生态扩展 | 100+集成、沙箱执行、浏览器自动化 | 2-3天 | 中 |

---

## 2. 详细集成映射

### 2.1 Mem0 → 记忆层 (P0-1)

**现有**: `src/knowledge/memory.py` - 10层记忆、权限控制、过期清理、审计  
**目标**: 增加向量检索、跨会话持久化、自适应个性化、MCP接口

| 现有能力 | Mem0 增强 | 集成点 |
|----------|-----------|--------|
| 10层内存结构 | 向量语义检索 + 图谱关系 | `Memory.search()` → 语义召回 |
| 权限/过期/审计 | 用户/会话/Agent三级隔离 | `Memory.add(user_id, agent_id, metadata)` |
| 模型解耦 | 多LLM提供商支持 (OpenAI/Anthropic/Ollama) | `Memory(llm_client=...)` |
| 无 | MCP Server 原生支持 | `mem0 init --agent` 零配置接入 |

**迁移路径**:
```python
# 当前
from src.knowledge.memory import Memory
mem = Memory()

# 目标 (保持接口兼容)
from src.knowledge.memory import Memory  # 内部委托 Mem0
mem = Memory()  # 自动使用 Mem0 作为后端
```

**验收标准**:
- [ ] 现有单测 100% 通过 (接口不变)
- [ ] 新增: 跨会话记忆召回准确率 > 85%
- [ ] 新增: MCP Client 可连接 Mem0 Server
- [ ] 性能: 检索延迟 < 100ms (p99)

---

### 2.2 LangGraph → Goal→Task Graph + Workflow Engine (P0-2)

**现有**: `src/ai/goal_task_graph.py` + `src/workflow/planner.py` + `executor.py`  
**目标**: 获得生产级 DAG 引擎、状态持久化、Human-in-the-loop、可视化调试

| 现有能力 | LangGraph 增强 | 集成点 |
|----------|----------------|--------|
| Goal→Plan→Task Graph | 编译型 StateGraph、类型安全 State | `GoalTaskGraph` 内部委托 `StateGraph` |
| 任务依赖拓扑 | 条件边、循环、并行分支、Map-Reduce | `add_conditional_edges()` |
| 执行器 | 检查点持久化 (PostgreSQL/SQLite/Redis) | `checkpointer=PostgresSaver()` |
| 无 | Human-in-the-loop 断点 | `interrupt_before=["review"]` |
| 无 | LangGraph Studio 可视化调试 | 开发期生产力提升 |

**迁移路径**:
```python
# 当前: 手写拓扑排序
from src.ai.goal_task_graph import GoalTaskGraph
graph = GoalTaskGraph()
tasks = graph.decompose_goal(goal)

# 目标: LangGraph 编译图
from langgraph.graph import StateGraph
from src.ai.goal_task_graph import GoalTaskGraph  # 适配器

class GraphState(TypedDict):
    goal: GoalDefinition
    tasks: List[Task]
    current_task: Optional[Task]
    results: Dict[str, Any]
    review_feedback: Optional[str]

graph = GoalTaskGraph()  # 内部构建 StateGraph
result = graph.invoke({"goal": goal})  # 自动执行+检查点
```

**验收标准**:
- [ ] 现有 4/4 Integration Tests 通过
- [ ] 新增: 检查点恢复测试 (中断→恢复→完成)
- [ ] 新增: HITL 断点测试 (人工介入→继续)
- [ ] 新增: 并行分支执行正确性
- [ ] 可视化: LangGraph Studio 可加载图结构

---

### 2.3 Phoenix → Observability + Evaluation (P1-1)

**现有**: `src/observability/` (Logging + Metrics + Tracing) + `docker-compose.observability.yml`  
**目标**: 统一 tracing+eval 平台、自托管、Agent 原生仪表板

| 现有能力 | Phoenix 增强 | 集成点 |
|----------|--------------|--------|
| 结构化日志 + Correlation ID | 统一 OTel Collector 接收 | `otel-collector` → Phoenix OTLP endpoint |
| Prometheus Metrics (40+) | LLM 指标自动采集 (token、latency、cost) | `Phoenix.trace()` 自动插桩 |
| Tempo Traces | 评估数据集、实验对比、回归检测 | `phoenix.evals.run()` |
| Grafana 21面板 | Agent 原生仪表板 (traces、spans、evals) | 替代/补充 Grafana |
| AlertManager 3规则 | Drift detection、性能退化告警 | Phoenix alerts → AlertManager |

**架构调整**:
```
当前: App → OTel Collector → Tempo + Prometheus + Loki → Grafana
目标: App → OTel Collector → Phoenix (traces+evals) + Prometheus (metrics) + Loki (logs) → Grafana
```

**验收标准**:
- [ ] 现有 CI Observability Check 通过
- [ ] 新增: Phoenix UI 可看到完整 Agent trace (planner→agents→tools)
- [ ] 新增: 创建 1 个评估数据集 + 1 个回归实验
- [ ] 新增: Drift 告警在 AlertManager 触发

---

### 2.4 MCP → Tool System 标准化 (P1-2)

**现有**: `src/ai/tools.py` - ToolRegistry、ToolDiscovery、ToolInvocation、权限/风险/审计  
**目标**: 对外暴露 MCP Server、接入 MCP 生态工具、双向互操作

| 现有能力 | MCP 增强 | 集成点 |
|----------|----------|--------|
| Tool Schema (JSON Schema) | MCP Tool 定义兼容 | `Tool.to_mcp()` / `MCPTool.to_internal()` |
| 权限/风险/超时/重试 | MCP 权限模型映射 | `ToolPermission` ↔ `MCPAuthorization` |
| 审计日志 | MCP 调用链路追踪 | Correlation ID 透传 |
| 无 | 100+ 现成 MCP Server (文件系统、GitHub、数据库、浏览器...) | `MCPClient.discover_tools()` |
| 无 | 作为 MCP Server 暴露给外部 Agent | `MCPServer.from_tool_registry()` |

**双向模式**:
```
外部 Agent (Claude Code, Cursor) → MCP Client → LiuHao MCP Server → 内部 Tool Registry
LiuHao Agent → MCP Client → 外部 MCP Server (GitHub, PostgreSQL, Browser) → 结果
```

**验收标准**:
- [ ] 现有 Tool 测试 100% 通过
- [ ] 新增: 作为 MCP Server 启动，Claude Code 可调用内部工具
- [ ] 新增: 作为 MCP Client 调用 filesystem/github server
- [ ] 权限/审计/超时在 MCP 边界生效

---

### 2.5 Ragas/DeepEval → CI 质量门 (P2-1)

**现有**: `scripts/ci_observability_check.py` + 11 单元/集成测试  
**目标**: 自动化 LLM 质量回归防护

| 场景 | 工具 | 指标 | CI 集成 |
|------|------|------|---------|
| RAG 问答 | Ragas | Faithfulness、Context Precision、Answer Relevance | `pytest --ragas` |
| Agent 轨迹 | DeepEval | Task Completion、Tool Correctness、Hallucination | `pytest --deepeval` |
| 多 Agent 协作 | 自定义 | Coordination Score、Aggregation Quality | `pytest --agent-eval` |

**CI Pipeline 扩展**:
```yaml
# .github/workflows/ci.yml 新增
- name: RAG Evaluation
  run: python -m pytest tests/eval_rag.py --ragas --fail-under=0.8
- name: Agent Trajectory Evaluation  
  run: python -m pytest tests/eval_agent.py --deepeval --fail-under=0.75
```

---

## 3. 依赖版本锁定策略

```toml
# pyproject.toml 新增
[tool.uv.sources]
mem0 = { git = "https://github.com/mem0ai/mem0", tag = "v1.2.0" }
langgraph = "==0.2.34"
arize-phoenix = "==4.12.0"
mcp = { git = "https://github.com/modelcontextprotocol/python-sdk", tag = "v1.0.0" }
ragas = "==0.1.23"
deepeval = "==1.0.15"

# 约束: 仅锁定主版本，允许补丁自动更新
# 定期 (月度) 运行: uv lock --upgrade-package <pkg>
```

---

## 4. License & 合规清单

| 项目 | License | 商用友好 | 依赖传递风险 | 结论 |
|------|---------|----------|--------------|------|
| Mem0 | MIT | ✅ | 低 | 可直接集成 |
| LangGraph | MIT | ✅ | 低 (LangChain 核心) | 可直接集成 |
| Phoenix | Apache-2.0 | ✅ | 低 | 可直接集成 |
| MCP SDK | MIT | ✅ | 无 | 可直接集成 |
| Ragas | Apache-2.0 | ✅ | 中 (LangChain) | 需验证传递依赖 |
| DeepEval | MIT | ✅ | 低 | 可直接集成 |
| CrewAI | MIT | ✅ | 中 | 仅参考不强依赖 |
| AutoGen | MIT | ✅ | 低 | 仅参考不强依赖 |

**红线**: 无 GPL/AGPL/SSPL 依赖进入核心链路

---

## 5. 迁移风险评估与缓解

| 风险 | 影响 | 可能性 | 缓解措施 |
|------|------|--------|----------|
| LangGraph API 破坏性变更 | 高 | 中 | 锁定版本、编写适配器层、集成测试覆盖核心路径 |
| Mem0 托管版与开源版功能差异 | 中 | 高 | 优先自托管、仅用开源 SDK 功能、避免托管专有特性 |
| Phoenix 资源占用 (Java/ClickHouse) | 中 | 中 | 提供 Lite 模式 (SQLite backend)、资源限制配置 |
| MCP 协议演进不稳定 | 中 | 低 | 锁定协议版本、抽象传输层、保持向后兼容 |
| 依赖链膨胀 | 中 | 高 | 定期 `uv tree --depth=2` 审计、最小化传递依赖 |

---

## 6. 实施里程碑

| 里程碑 | 交付物 | 截止 | 验收标准 |
|--------|--------|------|----------|
| **M1 (Week 1)** | Mem0 集成 + 单测通过 | Day 3 | 现有测试 100% 过、新增跨会话记忆测试过 |
| **M2 (Week 2)** | LangGraph 替换 GoalTaskGraph | Day 8 | 4/4 测试过、检查点恢复测试过、HITL 测试过 |
| **M3 (Week 3)** | Phoenix 部署 + 评估数据集 | Day 12 | UI 可见完整 trace、1 个回归实验跑通 |
| **M4 (Week 4)** | MCP Server/Client 双向互通 | Day 16 | Claude Code 调用内部工具、内部调用外部 MCP |
| **M5 (Week 5)** | CI 质量门 + 文档收尾 | Day 20 | PR 自动跑评估、失败阻断合并 |

---

## 7. 代码仓库结构调整

```
D:\LiuHao-AI-OS├── src/
│   ├── ai/
│   │   ├── providers.py          # 保持 (Provider 抽象)
│   │   ├── employee.py           # 增强: 委托 LangGraph/CrewAI
│   │   ├── goal_task_graph.py    # 重写: LangGraph 适配器
│   │   └── tools.py              # 增强: MCP 双向适配
│   ├── knowledge/
│   │   └── memory.py             # 重写: Mem0 后端适配器
│   ├── observability/
│   │   ├── logging_utils.py      # 保持
│   │   ├── metrics.py            # 保持 + Phoenix 指标桥接
│   │   ├── tracing.py            # 重写: Phoenix OTel 导出
│   │   └── evaluation.py         # 新增: Ragas/DeepEval 封装
│   └── adapters/                 # 新增: 统一适配器层
│       ├── mem0_adapter.py
│       ├── langgraph_adapter.py
│       ├── phoenix_adapter.py
│       └── mcp_adapter.py
├── tests/
│   ├── integration/              # 新增集成测试
│   │   ├── test_mem0_integration.py
│   │   ├── test_langgraph_checkpoint.py
│   │   ├── test_phoenix_eval.py
│   │   └── test_mcp_bidirectional.py
│   └── eval/                     # 新增评估测试
│       ├── test_rag_faithfulness.py
│       └── test_agent_trajectory.py
├── configs/
│   ├── phoenix/                  # Phoenix 配置
│   ├── mcp/                      # MCP Server 配置
│   └── observability/            # 现有 + Phoenix 集成
├── docker-compose.observability.yml  # 更新: 加入 Phoenix
├── docker-compose.mcp.yml        # 新增: MCP Server 栈
├── scripts/
│   ├── ci_observability_check.py # 现有
│   └── ci_quality_gates.py       # 新增: 评估门控
└── docs/
    ├── OPENSOURCE_INTEGRATION_PLAN.md  # 本文档
    └── ARCHITECTURE_DECISIONS.md       # ADR 记录
```

---

## 8. 首周行动清单 (Day 1-3: Mem0 集成)

- [ ] `uv add mem0ai` 安装依赖
- [ ] 创建 `src/adapters/mem0_adapter.py` 实现 `MemoryBackend` 协议
- [ ] 修改 `src/knowledge/memory.py` 注入适配器 (保持对外接口不变)
- [ ] 编写 `tests/integration/test_mem0_integration.py` (跨会话、MCP Client、性能)
- [ ] 运行全测试套件确保无回归
- [ ] 更新 `docs/Y1_REQUIREMENT_TRACEABILITY.md` 记录集成状态

---

## 9. 决策日志 (ADR 模板)

每个集成决策记录一条 ADR：

```markdown
# ADR-001: Adopt Mem0 as Memory Backend
## Status: Accepted
## Context: Y1 10层记忆缺乏语义检索、跨会话持久化、MCP互操作
## Decision: 引入 Mem0 开源版作为底层存储/检索引擎，保持上层接口不变
## Consequences: +语义检索 +MCP +个性化; -新增依赖 -需维护适配器
## Alternatives: Letta(重、耦合LangChain), MemGPT(学术原型), 自研向量检索(工时大)
```

---

## 10. 成功度量 (North Star Metrics)

| 指标 | Y1 Baseline | Target (Post-Integration) | 测量方式 |
|------|-------------|---------------------------|----------|
| **记忆召回准确率** | N/A | > 85% | Mem0 eval dataset |
| **工作流执行成功率** | ~90% (手写) | > 99% (检查点恢复) | LangGraph checkpoint test |
| **可观测性覆盖率** | Traces only | Traces + Evals + Drift | Phoenix dashboard |
| **工具生态覆盖** | ~20 内置 | 100+ (MCP) | MCP server registry count |
| **CI 回归检出率** | 0% (无评估) | > 80% | Ragas/DeepEval gate stats |
| **端到端延迟 (p99)** | ~2s | < 3s (含评估) | Phoenix latency panel |

---

*文档版本: 1.0 | 创建: 2026-09-02 | 依据: GitHub 搜索结果 + Y1 实证基线*

---

## 5. 原文：`docs/TODO_UI_DEV.md`

# UI 前端开发计划

> 记录自 GPT 优化建议中可取的 UI 部分，剔除不可取的部分（3D Avatar、粒子背景等），形成可执行的轻量级方案。

---

## 技术栈

| 层 | 选型 | 说明 |
|---|------|------|
| 框架 | React 18 + TypeScript | 主流方案 |
| 构建 | Vite | 快速 HMR |
| 样式 | Tailwind CSS | 深色主题 |
| 路由 | React Router v6 | SPA 路由 |
| 动画 | GSAP | 页面转场、元素动画 |
| 状态管理 | Zustand | 轻量 |
| 图标 | Lucide React | 一致性好 |
| 图表 | Recharts | 简单够用 |
| 代码高亮 | Shiki | AI 对话需要 |

> 砍掉 Three.js / React Three Fiber / 3D Avatar / VRM 加载器等非核心依赖。

---

## 分步实施

### Week 1: 基础框架 + 核心页面

**Day 1-2: 项目初始化**
- `npm create vite@latest frontend -- --template react-ts`
- 配置 Tailwind CSS（深色赛博朋克主题）
- 设置路由结构（React Router v6）
- 配置 Zustand store

**Day 3-4: 登录页面 + 侧边栏菜单**
- 登录/注册表单（样式参考深色主题）
- 九宫格侧边栏导航（一级菜单 + 二级展开）
- 基础布局骨架（Sidebar + Content）

**Day 5-7: 总览仪表板 + AI对话界面**
- 仪表板：系统状态卡片、快速操作、统计数字
- AI对话：消息气泡、输入框、会话列表
- 对接后端 API（/api/ai-brain, /api/chat 等）

### Week 2: 业务页面 + 完善

**Day 8-9: Token管理面板**
- Token余额展示
- 按 Provider 分类统计
- 子账号 Token 列表
- 对接后端 API

**Day 10-11: 子账号管理界面**
- 子账号列表
- 创建/暂停/删除子账号
- 权限控制

**Day 12-14: 打磨 + 错误处理**
- 加载状态（Skeleton 骨架屏）
- 错误提示（Toast 通知）
- 空状态处理
- 响应式适配（先桌面，后移动）

---

## 赛博朋克主题 Token

```
深空蓝:    #0a0e27
霓虹青:    #00d9ff
电子紫:    #a855f7
琥珀橙:    #f59e0b
翡翠绿:    #10b981
赤红:      #ef4444
玻璃态:    rgba(15, 22, 41, 0.6) + backdrop-blur-xl
字体:      JetBrains Mono (英文) / 思源黑体 (中文)
```

---

## 目录结构

```
frontend/
├── src/
│   ├── components/
│   │   ├── layout/          # Sidebar, TopNav, Layout
│   │   ├── ui/              # Button, Card, Input, GlassPanel
│   │   ├── charts/          # TokenChart, UsageChart
│   │   └── chat/            # MessageBubble, ChatInput, SessionList
│   ├── pages/
│   │   ├── Login.tsx
│   │   ├── Dashboard.tsx
│   │   ├── AIChat.tsx
│   │   ├── TokenManager.tsx
│   │   └── SubAccounts.tsx
│   ├── hooks/               # useAuth, useToken, useTheme
│   ├── services/            # api.ts, auth.service.ts, token.service.ts
│   ├── stores/              # authStore, tokenStore, uiStore
│   ├── styles/              # index.css, cyberpunk.css
│   ├── types/               # index.ts
│   ├── App.tsx
│   └── main.tsx
├── package.json
├── vite.config.ts
├── tailwind.config.js
└── tsconfig.json
```

---

## 完成标准

- [ ] 登录/注册页面可用，对接后端认证
- [ ] 侧边栏菜单导航流畅
- [ ] 仪表板展示系统状态统计数据
- [ ] AI对话界面可收发消息
- [ ] Token管理面板展示余额和消耗
- [ ] 子账号管理可创建/禁用
- [ ] 所有页面错误处理和加载状态完整
- [ ] 赛博朋克主题一致

---

## 6. 原文：`补全LiuHao-AIOS-Hermes-Execution-Prompt.md`

# LiuHao AI OS 补全基线 — Hermes 执行指令

> 本文件是 LiuHao AI OS 的完整补全路线图，按 Phase 0 → Phase 6 逐项列出。
> 作为 Hermes 的输入，要求 Hermes 按阶段、按任务 ID 逐项执行，并在每个 Phase 完成后输出验收报告。

## 1. 项目背景与目标

**项目名称**：LiuHao AI OS
**架构**：FastAPI + 微服务 + React 前端
**当前目标**：以本路线图为「补全基线」，逐项落地基础设施、安全隔离、分布式能力、AI 编排、前端控制台、成本配额、设备抽象及生产验收。

**执行原则**：
- 先 Phase 0 基建，再 Phase 1，再 Phase 2/3 并行，最后 Phase 4/5/6。
- 每个 Phase 必须「所有验收标准全绿」才算完成。

## 2. 工作目录与产物约束

```text
<REPO_PATH>/
├── infra/                    # Phase 0 基础设施
│   ├── postgres/
│   ├── redis/
│   ├── qdrant/
│   ├── etcd/
│   ├── kafka/               # Phase 2 可选
│   └── monitoring/          # Phase 1 监控
├── libs/
│   └── liuhao-core/         # INFRA-06
│   └── liuhao-resilience/   # REL-01
├── src/
│   ├── security/            # SEC-02/03
│   ├── integrations/        # SEC-03 ORM
│   ├── observability/       # SEC-04/05
│   ├── distribution/        # DIST-01/02/03
│   ├── ai/                  # AI-01/02/03/04 + COST
│   ├── gateway/             # REL-02
│   ├── devices/             # DEV-01
│   └── plugins/
├── apps/
│   └── console/             # Phase 4 前端
├── migrations/              # INFRA-07 / SEC-03
├── scripts/ops/             # REL-03 备份/恢复/演练
├── tests/                   # Phase 6
├── config/monitoring/       # 监控规则与 Dashboard
└── docs/                    # ADR / Runbook / 验收报告
```

## 3. 执行方式（Hermes 三选一）

```bash
# 方式 A：Hermes + Codex（能力强，推荐用于复杂任务）
cd <REPO_PATH> && git init && hermes -s codex -z "<粘贴下方完整指令>"

# 方式 B：直接 Codex（非交互场景）
cd <REPO_PATH> && git init && codex exec --skip-git-repo-check -m gpt-5.6-sol "<粘贴下方完整指令>"

# 方式 C：Hermes + OpenCode（免费通道）
cd <REPO_PATH> && git init && hermes --yolo -z "Step by step via terminal: CI=1 opencode run --auto '<粘贴下方完整指令>'"
```

> 注意：OpenCode 非交互调用必须加 `CI=1` 且关闭 PTY，否则会话会被 Node TUI 干掉。

## 4. 总体分期概览

| 阶段 | 时间 | 核心主题 | 关键产出 | 里程碑判定标准 |
|------|------|----------|----------|----------------|
| Phase 0 | Week 1-2 | 基础设施落地 | PostgreSQL/Redis/Qdrant/etcd 真实集群 + 连接池/迁移/健康检查 | 所有模块都连真实 DB/Redis；`alembic upgrade head` 通过 |
| Phase 1 | Week 2-3 | 安全隔离 & 核心模块生产化 | gVisor 沙箱 + Vault 密钥 + 真实 ORM 迁移 + OTel 链路 + Prometheus/Grafana | 插件在 gVisor 中跑通；链路在 Jaeger/Tempo 可见；Alembic 管理所有表 |
| Phase 2 | Week 3-4 | 分布式能力 & 可靠性闭环 | Redis Streams/Kafka 消息总线 + 分布式锁/选举 + 熔断/舱壁/重试库 | 多实例部署通过混沌测试（Pod kill/网络分区/延迟注入）自愈 |
| Phase 3 | Week 3-4 | AI/编排/模型生态补齐 | 真实 LangGraph StateGraph + 补偿事务/人工介入 + 模型注册/路由/成本路由 | 复杂长流程（含人工介入/回滚）可跑通；模型成本实时可见 |
| Phase 4 | Week 5-6 | 前端控制台 & 成本/配额 & 设备抽象 MVP | Admin Console（Agent/插件/监控/配置）+ Token 计费/配额 + 文件/浏览器适配器 | 可在浏览器完成 90% 运维操作；Token 成本实时入账；可调用本地文件/浏览器 |
| Phase 6 | Week 7-8 | 全面测试 & 生产验收 | 覆盖率>85% / E2E / 混沌/性能/安全测试全过 / 验收清单全勾 | 通过《生产验收清单》所有阻断项；30 天跑路指标达标 |

## 5. Phase-by-Phase 任务清单

### Phase 0：基础设施落地（Week 1-2）—— 必须先做，无依赖，全员并行

> 完成标志：运行 `docker-compose -f docker-compose.infra.yml up -d` 所有服务绿灯；运行 `alembic upgrade head` 成功；所有带有 `@not_integration` 的 `pytest tests/ -m "not integration"` 通过（Mock 替换为真实连接）。

#### INFRA-01 部署 PostgreSQL 15+（Primary + Replica）
- **技术选型/关键细节**：Docker Compose / Cloud SQL / Helm Chart `postgresql`；开启 `pg_stat_statements`、`uuid-ossp`、`pg_trgm`
- **产物**：`infra/postgres/docker-compose.yml` 或 `infra/postgres/helm/values.yaml` + `init-extensions.sql`
- **验收标准**：`psql` 可连；`CREATE EXTENSION` 全部成功；主从同步延迟 < 1s
- **负责**：DevOps

#### INFRA-02 部署 Redis 7+ Cluster/哨兵
- **技术选型/关键细节**：3 Master + 3 Replica；开启 AOF + RDB；配置 `maxmemory-policy allkeys-lru`
- **产物**：`infra/redis/docker-compose.yml` + 6 节点 `redis.conf`
- **验收标准**：`redis-cli --cluster check` 通过；`PING < 1ms`；Failover < 5s
- **负责**：DevOps

#### INFRA-03 部署 Qdrant（向量库）
- **技术选型/关键细节**：Docker/Helm `qdrant/qdrant`；启用 quantization 节省内存；配置 `collection_name=liuhao_memories`
- **产物**：`infra/qdrant/docker-compose.yml` 或 Helm values
- **验收标准**：`curl /collections` 返回 200；Python `qdrant-client` 插入/检索 1k 向量 < 50ms
- **负责**：DevOps

#### INFRA-04 部署 etcd（服务发现/分布式锁/配置）
- **技术选型/关键细节**：3 节点集群；启用 auth + TLS；配置 `quota-backend-bytes`
- **产物**：`infra/etcd/docker-compose.yml` + `etcd.conf.yml` + TLS 证书生成脚本
- **验收标准**：`etcdctl endpoint health` 全绿；`put/get/watch` 正常
- **负责**：DevOps

#### INFRA-05 部署 Kafka / Redpanda（可选，Phase 2 用）
- **技术选型/关键细节**：3 Broker；`replication.factor=3`；`min.insync.replicas=2`；Topic：`liuhao.tasks`、`liuhao.events`、`liuhao.audit`
- **产物**：`infra/kafka/docker-compose.yml` + topic 创建脚本
- **验收标准**：`kcat -L` 显示 Topic；生产/消费 10k msg/s 无丢包
- **备注**：本阶段只交付配置 + 文档，不要求立即启动
- **负责**：DevOps

#### INFRA-06 统一基础库落地（公共库 `liuhao-core`）
- **技术选型/关键细节**：`tenacity`（重试）+ `pybreaker`/`pycircuitbreaker`（熔断）+ `prometheus-client`（指标）+ `structlog`（日志）+ `pydantic-settings`（配置）
- **产物**：
  - `libs/liuhao-core/pyproject.toml`
  - `libs/liuhao-core/src/liuhao_core/{retry,circuit_breaker,metrics,logging,config}.py`
  - `libs/liuhao-core/README.md`
- **验收标准**：发布到私有 PyPI 或 `pip install -e .` 成功；所有微服务可无冲突安装
- **负责**：Backend Lead

#### INFRA-07 数据库迁移初始化（Alembic）
- **技术选型/关键细节**：`alembic init migrations`；为 `security.api_keys`、`plugins`、`observability.spans/metrics/alerts.audit`、`plugins.marketplace` 建表脚本
- **产物**：`migrations/` + `migrations/versions/0001_initial.py`
- **验收标准**：`alembic upgrade head` 无报错；`alembic downgrade base` 可回滚；CI 中跑 `alembic check`
- **负责**：Backend

#### INFRA-08 统一健康检查 / Ready Probe 规范
- **技术选型/关键细节**：统一 `/health`（存活） + `/ready`（就绪：DB/Redis/Qdrant/etcd 连通）；返回 JSON `{"status": "ok", "checks": [...]}`
- **产物**：`docs/infrastructure/health-check-spec.md` + `libs/liuhao-core/src/liuhao_core/health.py`
- **验收标准**：`curl /health` 200；`curl /ready` 依赖全绿才 200；K8s Probe 配置生效
- **负责**：Backend

---

### Phase 1：安全隔离 & 核心模块生产化（Week 2-3）

> 完成标志：`docker-compose -f docker-compose.prod.yml up -d` 启动全栈；`pytest tests/ -m "integration" -v` 全过（打真实基建）；Grafana/Tempo/Grafana 可观测全链路；插件在 gVisor 中安全运行。

#### SEC-01 gVisor/Kata Containers 沙箱集成
- **关键实现细节**：
  1. `pip install gvisor-tap-vsock` / 安装 `kata-runtime`
  2. `src/plugins/sandbox/models.py` 新增 `SandboxBackend` 抽象、`gVisorBackend`、`KataBackend`
  3. `plugin_manager.start_plugin` 调用 `backend.create_container(plugin_id, limits)`
  4. 资源限制：`cpu_quota`、`memory_limit`、`pids_limit`、`network=none/bridge`、`readonly_rootfs=True`
- **验收标准**：
  - `pytest tests/plugins/test_sandbox.py -v` 全过
  - 恶意操作如 `os.system("rm -rf /")` 被拦截，容器崩溃不影响宿主
  - `docker stats` 显示 CPU/Memory 限制生效

#### SEC-02 Vault/SealedSecrets 密钥管理
- **关键实现细节**：
  1. 部署 Vault（Dev 模式 → HA）
  2. `src/security/vault_client.py` 封装 `hvac`：`read_secret(path)`、`generate_ttl_key(ttl)`、`rotate_key(path)`
  3. 替换 `src/security/api_keys.py`、`encryption.py` 中的本地密钥为 `vault_client.read_secret("liuhao/keys/master")`
  4. 配置 AppRole 认证 + ttl=1h token 自动续约
- **验收标准**：
  - 重启服务后自动从 Vault 拉取密钥启动成功
  - `vault kv put secret/liuhao/keys/master ...` 后服务热加载（SIGHUP 效应）
  - `vault operator rotate` 根密钥后服务自动适配

#### SEC-03 真实 ORM + Alembic 迁移基地
- **关键实现细节**：
  1. `src/integrations/orm/models.py` 补全 `BaseModel`、`Mixin`、`SessionManager`
  2. `src/security/api_keys.py` 替换 `JSONFileBackend` → `SQLAlchemyBackend`（继承 `StorageBackend`）
  3. `src/plugins/marketplace/store.py` 同理
  4. `alembic revision --autogenerate -m "init all tables"` → 审核 SQL → `alembic upgrade head`
- **验收标准**：
  - `alembic upgrade head` 无报错
  - `pytest tests/security/test_api_keys.py -v` 全过（打真实 PG）
  - `vault history` 显示完整版本本

#### SEC-04 OTel SDK + Collector + Tempo/Jaeger 接入
- **关键实现细节**：
  1. `pip install opentelemetry-api opentelemetry-sdk opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-requests opentelemetry-instrumentation-sqlalchemy opentelemetry-instrumentation-redis`
  2. `src/observability/tracing.py` 替换为真实 OTel SDK，导出到 OTLP/gRPC
  3. 部署 `otel-collector`（Docker/DA）→ Tempo 存储 → Grafana 查询（指向 Collector:4317）
  4. `src/observability/tracing.py` 导出 `get_trace()` 供全局使用
- **验收标准**：
  - `curl -X POST http://localhost:8080/v1/test` → Grafana Tempo 查到完整 Trace
  - Trace 包含 `http.status_code`、`db.statement`、`span.kind`、`error` 标签

#### SEC-05 Prometheus + Alertmanager + Grafana/Loki
- **关键实现细节**：
  1. `docker-compose.monitoring.yml` 启动 Prometheus + Alertmanager + Grafana + Loki + Promtail
  2. 导入 `config/monitoring/prometheus_rules.yml`（20+ 规则）
  3. Grafana 导入 `config/monitoring/grafana_dashboards/*.json`（Overview/业务/资源/安全/插件）
  4. Alertmanager 配置 Email/Slack/Webhook 接收器
- **验收标准**：
  - `curl http://localhost:9090/-/healthy` 200
  - Grafana 打开 Dashboard 无红框
  - 手动触发 `high_error_rate` → Alertmanager 触发 → Slack/邮件收到告警

---

### Phase 2：分布式能力 & 可靠性闭环（Week 3-4）

> 完成标志：`docker-compose -f docker-compose.prod.yml up -d --scale api=3` 启动 3 副本；`hey -n 100000 -c 100 http://localhost:8080/v1/chat` 无 5xx；`kubectl kill pod <leader>` → 5s 内新 Leader 服务正常；备份/恢复演练通过。

#### DIST-01 Redis Streams 消息总线真实后端
- **关键实现细节**：
  1. `src/distribution/msg_bus.py` 实现 `RedisStreamBackend`
  2. `XADD` 发布 + `XREADGROUP` 消费组消费
  3. `XACK` 确认 + `XPENDING` 重试 + `XCLAIM` 死信迁移
  4. `XRANGE` 回溯 / `XTRIM MAXLEN ~ 10000` 清理
  5. `message_bus(mode="redis")` 自动切换后端
  6. 实现 `MessageBus.health_check()` 检查 `XINFO STREAM`
- **验收标准**：
  - `pytest tests/distribution/test_msg_bus.py -v` 全过（打真实 Redis）
  - 杀死消费者进程 → 消息在 Pending List → 另一实例 Claim 并消费成功
  - 发布 10k msg/s 持续 5min 无丢包、无 OOM

#### DIST-02 分布式锁/选举/服务发现（etcd/Redis）
- **关键实现细节**：
  1. `src/distribution/lock.py` 实现 `EtcdLockBackend` / `RedisLockBackend`
  2. `acquire(key, ttl=30)` / `renew()` / `release()`
  3. Fencing Token 防止羊群效应
  4. `src/distribution/election.py` 实现 `EtcdElectionBackend`
  5. `Campaign(ctx, name)` / `Resign()` / `Observe()`
  6. Lease Keep-Alive + Revision 版本控制
  7. `src/distribution/service_bus.py` 注册服务实例到 etcd `/services/liuhao/{service}/{instance_id}`（TTL 10s 心跳）
- **验收标准**：
  - 启动 3 实例 → 自动选举出 1 Leader → Kill Leader → 5s 内新 Leader 当选
  - 并发 100 协程抢锁 → 无死锁、无活锁、吞吐 > 5k ops/s
  - 服务注册表在 etcd 实时可见，下线自动清理

#### DIST-03 分布式锁/选举在插件/任务调度中落地
- **关键实现细节**：
  1. `plugin_manager.load_plugin = lock.acquire(f"plugin:{name}:load")` 防止并发加载
  2. `GoalTaskGraph.execute_task = lock.acquire(f"task:{task_id}:exec")` 防重复执行
  3. `src/ai/employee.py` `AgentPool` 启动时 `election.campaign("agent-pool-leader")` 仅 Leader 分发任务
- **验收标准**：
  - 并发启动 10 实例 → 仅 1 个加载插件、1 个分发任务
  - 日志显示 `acquired lock` / `released lock` 成对出现

#### REL-01 统一熔断/舱壁/重试/慢启动库（`liuhao-resilience`）
- **关键实现细节**：
  1. 封装 `pybreaker` + `tenacity` + `asyncio.Semaphore`（舱壁）
  2. 装饰器示例：
     ```python
     @circuit_breaker(failure_threshold=5, recovery_timeout=30)
     @retry(stop=stop_after_attempt(3), wait=wait_exponential(jitter=1, max=10))
     @bulkhead(max_concurrent=10)
     async def call_external_api(...): ...
     ```
  3. 替换 `src/ai/providers.py` `generate_with_retry`、`src/integrations/*` 外部调用
  4. 暴露 `circuit_breaker.state` 到 `/metrics`（Prometheus）
- **验收标准**：
  - 模拟下游 50% 失败 → 熔断器 OPEN → 直接拒绝不再请求 → 30s 后 HALF-OPEN → 恢复 CLOSED
  - 并发 100 → 信号量限制并发 10 → 其余排队/快速失败

#### REL-02 熔断/限流/慢启动在网关/Provider/外部调用全链路生效
- **关键实现细节**：
  1. `src/gateway/rate_limiter.py` 实现 `TokenBucketLimiter` / Redis Lua 脚本原子扣减 / `SlidingWindowLimiter`
  2. `src/gateway/main.py` 中间件挂载 `limiter.limit(key, cost=1)`
  3. `src/ai/providers.py` 接入 `circuit_breaker` + `Bulkhead` + `Retry`
- **验收标准**：
  - `hey -n 1000 -c 100 http://localhost:8080/v1/chat` → 429 比例符合配额
  - 下游 Provider 延迟 10s → 网关 30s 超时返回 504 → 熔断器记录 → 后续请求快速失败

#### REL-03 备份-恢复-灾难演练自动化
- **关键实现细节**：
  1. `scripts/ops/backup.py` 接入真实 `pg_dump` + Redis `BGSAVE` + Qdrant snapshot + 配置文件 → 加密（GPG/Age）→ 上传 S3/MinIO
  2. `scripts/ops/restore.py` 支持时间点恢复（PITR）+ 单表恢复
  3. GitHub Actions schedules: `"0 2 * * *"` 触发备份 → verify 下载校验 → cleanup Job 清理 30 天前
  4. 季度灾难演练脚本 `scripts/ops/dr_drill.py`：模拟主库挂掉 → 升级 Replica → 修改 DNS/ConfigMap → 验证服务恢复
  5. RTO 30min 目标
- **验收标准**：
  - 手动触发 `python scripts/ops/backup.py create` → S3 出现加密包 → restore 到新 DB → 服务启动数据一致
  - 季度演练文档化记录；RTO ≤ 18min，RPO ≤ 5min

---

### Phase 3：AI/编排/模型生态补齐（Week 3-4，与 Phase 2 并行）

> 完成标志：复杂多 Agent 长流程（含工具调用/人工审批/补偿回滚/模型自动路由/成本记录）在 LangGraph + 真实 Provider 下完整跑通；Grafana 可见全链路 Trace + 成本 Dashboard。

#### AI-01 真实 LangGraph StateGraph 集成
- **关键实现细节**：
  1. `src/ai/langgraph_workflow.py` 实现 `StateGraph` 编排检查点/流式执行
  2. 使用 `SqliteSaver` / `checkpointer` 持久化状态
  3. `app = workflow.compile(checkpointer=...)`
  4. `GoalTaskGraph` 集成 `app.astream()` / `app.ainvoke()` 支持流式/检查点/人工介入
- **验收标准**：
  - `pytest tests/ai/test_langgraph_workflow.py -v` 全过
  - 复杂流程（含循环/条件分支/工具调用）在 LangGraph 可视化界面可跑通
  - 中断后 `app.ainvoke(..., config={"configurable": {"thread_id": "..."}})` 从检查点恢复

#### AI-02 补偿事务 / 人工介入节点 / 长流程持久化
- **关键实现细节**：
  1. `GoalTaskGraph` 新增 `CompensatableTaskNode`：`execute()` / `compensate()`
  2. `ExecutionEngine` 维护 SagaLog（任务 ID → 补偿动作）
  3. 任务失败 → 自动逆序执行已完成任务的 `compensate()`
  4. `HumanInTheLoopNode`：`status=WAITING_HUMAN` → 写入 DB → Webhook/轮询等待人工审批 → approve/reject 回调继续
- **验收标准**：
  - 模拟：任务 A 成功 → 任务 B 失败 → 自动执行 `compensate_A()` 回滚 → 状态 `COMPENSATED`
  - 人工介入节点：前端点击 [批准] → 后端收到回调 → 流程继续执行后续任务

#### AI-03 模型注册中心 + 动态路由 + 成本感知路由
- **关键实现细节**：
  1. `src/ai/model_registry.py`：
     - `ModelRegistry.register(name, provider, model, capabilities, cost_per_1k_tokens, max_concurrency)`
     - `get_best_model(task_type, budget_per_call, latency_requirement)` → 返回最优 Provider
  2. `ProviderFactory.create_provider()` 支持 `model="auto"` 自动路由
  3. `src/ai/cost_tracker.py`：Redis 计数器 `cost:user:{uid}:daily` + `cost:model:{model}:daily` + 告警规则 `cost_exceed_budget`
- **验收标准**：
  - `pytest tests/ai/test_model_registry.py` 全过
  - `GoalTaskGraph` 设置 `budget_per_call=0.01` → 自动路由到 `gpt-4o-mini` 而非 `gpt-4o`
  - Grafana Dashboard 显示实时 Token 消耗/成本/模型分布

#### AI-04 Agent 通信协议（ACL）+ 工具调用标准化
- **关键实现细节**：
  1. 定义 `AgentMessage`（JSON-RPC 2.0 风格）：`{id, method, params, context}`
  2. `src/ai/employee.py` 实现 `AgentPool.broadcast()` / `send_to(agent_id)` / `reply_to(message_id)`
  3. Tool Calling 标准化：`ToolSpec(name, description, json_schema)` → `ToolExecutor` 沙箱执行 → 结果自动序列化为 `ToolResult` 返回 LLM
- **验收标准**：
  - 两个 Agent 实例通过 `message_bus` 协作完成任务（如 Researcher 搜索 → Writer 写作）
  - Tool 调用在沙箱执行，结果自动序列化为 `toolResult` 返回 LLM

---

### Phase 4：前端控制台 & 成本/配额 & 设备抽象 MVP（Week 5-6）

> 完成标志：打开 `http://console.liuhao.ai` → 完成「创建 Agent → 安装 Plugin → 发起任务 → 查看 Trace → 查看成本账单」全流程，无需 SSH/CLI。

**技术栈**：React 18 + TypeScript + Vite + Ant Design Pro / Mantine + React Query + Zustand + Socket.io + Recharts/ECharts

#### UI-01 Admin Console 基础框架
- **关键实现细节**：
  1. `apps/console/` 初始化：Vite + React + TS + Ant Design Pro 布局（菜单/面包屑/国际化/权限）
  2. 登录：接入 Keycloak/SSO/OAuth2
  3. RBAC 前端权限指令 `<auth resource="plugin" action="write">`
- **验收标准**：
  - `npm run dev` 启动无报错
  - 登录跳转 → 成功后显示权限内菜单
  - 刷新页面 Token 自动刷新

#### UI-02 Agent/Plugin 管理面板
- **关键实现细节**：
  1. Agent 列表：状态/版本/资源日志/弹窗/重启/查看 Trace
  2. Plugin 市场：列表/详情/安装/卸载/升级/配置/查看沙箱 Trace
  3. 插件开发者门户：上传/版本管理/审核状态/下载统计
- **验收标准**：
  - 点击 [安装 Plugin] → 后端下载 → 沙箱启动 → 列表刷新显示 RUNNING
  - 点击 Agent [查看 Trace] → 跳转 Grafana Trace 页面

#### UI-03 监控/告警/日志查询面板
- **关键实现细节**：
  1. Overview：请求量/延迟/P99/错误率/活跃 Agent/插件数
  2. Trace 查询：按 Trace/Service/时间/标签检索 → 火焰图/瀑布图
  3. 日志查询：Loki 查询构建器（label/时间/关键字）+ 高亮/下载
  4. 告警中心：当前告警/历史/静默/通知记录
- **验收标准**：
  - 打开 Dashboard 无红框、数据实时刷新（WebSocket/SSE）
  - 复制 TraceID 粘贴一秒级打开完整瀑布图
  - 告警点击 [静默 1 小时] → Alertmanager 生效

#### UI-04 配置/Secret/部署管理
- **关键实现细节**：
  1. 配置中心：K/V 编辑/版本历史/一键回滚/灰度发布（百分比）
  2. Secret 管理：创建/轮换/撤销/查看引用关系（哪个服务用了哪个 Secret）
  3. 部署流水线可视化：Pipeline 图/构建日志/部署历史/一键回滚
- **验收标准**：
  - 修改配置 [保存] → 后端热加载 → 前端 Toast [生效]
  - 点击 [回滚到 v1.2.3] → 服务配置一致 → 进度条跑完 → 版本回退

#### COST-01 Token 计费/配额/限价引擎
- **关键实现细节**：
  1. `src/ai/cost_tracker.py`：
     - `record_usage(user_id, model, prompt_tokens, completion_tokens, cost_usd)` → Redis 计数器
     - `get_usage(user_id, period=daily/monthly)` → 聚合
     - `check_quota(user_id, model)` → 预估
  2. `src/ai/model_registry.py` 接入 `cost_per_1k_tokens` 定价表（可配置）
  3. `scripts/ops/cost_report.py` 每日/月度账单生成（CSV/PDF）、邮件发送
- **验收标准**：
  - 调用 `record_usage("user1", "gpt-4o", 1000, 500, 0.045)` → Redis 计数器增
  - `check_quota("user1", 0.1)` → 超额返回 `QuotaExceededError`
  - Grafana Dashboard：用户/模型/日/月 成本趋势图

#### COST-02 配额执行/软硬限制/告警
- **关键实现细节**：
  1. `src/ai/providers.py` `generate` 前调用 `cost_tracker.check_quota()`
  2. 软限制：邮件通知、返回 `quota_exceeded retry_after=3600`
  3. 硬限制：直接返回 429 + error code
  4. Alertmanager 规则：`daily cost exceed budget` / `model daily cost exceed threshold`
- **验收标准**：
  - 模拟用户超额调用 → 返回 429 + `{"error": "quota_exceeded", "retry_after": 3600}`
  - 告警触发 → Slack 收到 [用户 user1 日成本超 $10]

#### DEV-01 设备/浏览器/可插拔 MVP
- **关键实现细节**：
  1. `src/devices/` 抽象基类：
     - `FileSystemAdapter`（本地文件/MinIO/OSS/GCS 统一接口：read/write/list/stat/copy/move/delete）
     - `BrowserAdapter`（Playwright 封装：navigate/click/type/screenshot/extract_text + 隐身代理/指纹伪装）
     - `ShellAdapter`（沙箱命令执行：run/cmd/timeout、sudo、exec）资源限制
  2. `DeviceManager` 注册/发现/健康检查
  3. 插件通过 `device_manager.get("filesystem")` 获取适配器
- **验收标准**：
  - 插件调用 `fs.write("s3://bucket/key", data)` → 透明写入 S3
  - 插件调用 `browser.navigate("https://example.com")` → 返回截图/HTML
  - 沙箱内 `shell.run("python script.py")` → 返回 stdout/stderr/exit_code

---

### Phase 6：全面测试 & 生产验收（Week 7-8）

> 完成标志：所有测试层级通过，最终交付 4 份文档，5 个角色签收。

| 测试层级 | 工具/命令 | 覆盖范围 | 通过标准 |
|----------|-----------|----------|----------|
| 单元测试 | `pytest tests/ -m "unit" --cov=src --cov-fail-under=85` | 所有公共函数/类/边界条件/异常分支 | Coverage ≥ 85%；`pytest tests/ -m "unit"` 全绿 |
| 集成测试 | `pytest tests/ -m "integration" -v --tb=short` | DB/Redis/Qdrant/etcd/Kafka/外部 API（Mock/真实）交互 | 100% 通过；无 Flaky Test（重跑 3 次全过） |
| E2E 测试 | `pytest tests/e2e/ -v --tb=short -m e2e` | 完整用户旅程：部署→启动→注册→创建 Agent→安装 Plugin→执行任务→查看结果/成本/日志 | 核心 10 条关键路径 100% 通过；无人工干预 |
| 性能基准 | `pytest -m performance targets api_baseline + api_load + api_spike + api_soak (4h)` | P99 < 2s / 错误率 < 0.1% / 吞吐 ≥ 2x 预期峰值 / 4h 无内存泄漏 | 报告自动生成 HTML → CI 门禁 `p99_latency < 2.0` |
| 压力/极限测试 | `locust -f tests/load/locustfile.py --headless -u 500 -r 50 -t 10m` | 断点测试：逐步施压至错误率 > 5% 或 P99 > 10s | 记录断点 RPS；自动扩容触发验证（HPA） |
| 安全测试 | `bandit -r src/ -f json -o bandit.json` + `trivy fs .` + `pip-audit` + OWASP ZAP 主动扫描 | 无 Critical/High 漏洞；依赖无已知 CVE；容器镜像扫描通过 | CI 门禁 `bandit --severity-level high` 通过 |
| 混沌工程 | `litmuschaos` / `chaos-mesh`：Pod Kill / Network Partition / CPU Hog / Disk Fill / DNS 故障 | 单实例故障自愈 < 30s；集群故障自愈 < 2min；数据零丢失（RPO=0） | 混沌实验报告；MTTR < 30s；无数据丢失；告警触发并自愈 |
| 安全渗透测试 | 内部红队/外部厂商：API Fuzzing / 认证绕过 / 注入 / 权限提升 / 沙箱逃逸 | 无 Critical/High 漏洞；沙箱逃逸 PoC 失败 | 渗透测试报告；风险等级 ≤ Medium |

**最终交付物**：
1. 《生产验收清单》（含所有测试报告链接、覆盖度、性能基线、安全扫描结果、混沌实验报告）
2. 《运维手册 / Runbook》（联系人/升级/回滚/扩容/灾难恢复）
3. 《架构决策记录 ADR》（关键技术选型理由）
4. 签收单：Platform Lead / Security Lead / Engineering Lead / Product Owner / On-Call Engineer

---

## 6. 关键技术选型锁定表

以下选型为本项目基准，**执行过程中禁止私自替换**。如需变更，必须更新 ADR 并通过架构评审。

| 领域 | 选型 | 版本/备注 | 理由 |
|------|------|-----------|------|
| 主数据库 | PostgreSQL | 15+ / `pgvector` 扩展可选 | 成熟、ACID、JSONB、扩展生态 |
| 缓存/分布式锁/消息队列 | Redis | 7.2+ Cluster / `redis-py` 5.0+ / Lua 脚本原子操作 | 单线程高性能、数据结构丰富、Stream 原生消费组 |
| 向量库 | Qdrant | 1.8+ / `qdrant-client` 1.7+ / 二进制量化 | Rust 写入快、过滤/负载均衡好、Python SDK 完善 |
| 服务发现/锁/选举 | etcd | 3.5+ / `aetcd3` / Lease + Watch | Raft 强一致、Watch 机制、Lease TTL 天然心跳 |
| 消息队列（大吞吐/持久化） | Redpanda / Kafka | Redpanda 23.3+ / `aiokafka` / 兼容 Kafka 协议 | 无 JVM、资源占用低、WASM 转换、Schema Registry |
| 容器沙箱 | gVisor / Kata Containers | gVisor 202312+ / `runsc` + containerd shim v2 | 用户态内核、系统调用拦截、兼容 OCI/containerd |
| 密钥管理 | HashiCorp Vault | 1.15+ / krew 1.2+ / AppRole + TTL Token | 企业级、动态秘钥、审计日志、PKI 引擎 |
| OTel Collector | otelcol-contrib | 0.106+ / otlphttp / otlpgrpc / 批处理/尾采样 | 统一收集/转换/导出、厂商无关 |
| 指标/告警 | Prometheus + Alertmanager | 2.47+ / 0.26+ | 标准生态、PromQL 强大、生态成熟 |
| 链路追踪 | Tempo / Jaeger | 2.4+ / `opentelemetry-exporter-otlp` | 对象存储后端便宜、Grafana 原生集成 |
| 日志聚合 | Loki + Promtail | 2.9+ / `logcli` / 标签索引 | 成本低、标签索引快、Grafana 原生 |
| 前端框架 | React + TS + Vite + Ant Design Pro | 18.2 + TS 5.3 + Vite 5 + Ant Design Pro 7 | 生态成熟、组件丰富、Pro 布局开箱即用 |
| CI/CD | GitHub Actions + ArgoCD | actions/setup-python + docker/build-push-action + argo-cd | 云原生 GitOps、环境晋升、自动回滚 |

---

## 7. 避坑清单（强制约束）

这些是从审计中发现的最高频 10 个坑，执行过程中必须作为红线约束：

| # | 坑 | 表现 | 预防措施 |
|---|-----|------|----------|
| 1 | 把 [Mock 能跑] 当成 [生产就绪] | 本地全绿，上线即崩 | Phase 0 必须上真实基建，CI 强制跑 Integration Test |
| 2 | 忽略 [连接池/重试/超时] 默认值 | 突发流量把下游打挂，连接池漏 | 统一基础库 `liuhao-core` 强制注入 `pool_size=20`, `timeout=30s`, `max_retries=3` |
| 3 | 忽略 [幂等性/幂等 Key] 设计 | 重试导致重复扣费/重复发消息/脏数据 | 所有外部调用强制要求 `idempotency_key`，DB 唯一索引兜底 |
| 4 | 忽略 [配置热加载/版本/回滚] | 改配置重启服务，改错无法秒回滚 | 配置中心必须支持版本历史 + 一键回滚 + 灰度发布 |
| 5 | 忽略 [Token 计费/配额] 从 Day 1 接入 | 上线后发现成本失控/用户盗用 | `cost_tracker` 从第一行 Provider 调用代码就接入 |
| 6 | 忽略 [沙箱逃逸/资源耗尽] 测试 | 恶意插件把宿主搞挂 | CI 强制跑沙箱逃逸 PoC（memtest + cgroup 逃逸 PoC） |
| 7 | 忽略 [分布式事务/补偿] 设计 | 长流程失败留下脏数据 | 所有长流程必须有 `compensate()` 实现，Saga Log 持久化 |
| 8 | 忽略 [OTel 语义约定/语义化标签] | 链路查不出来，Dashboard 空白 | 强制遵循 OTel Semantic Conventions (`http.route`, `db.statement`, `span.kind`) |
| 9 | 忽略 [测试金字塔/契约测试/混沌工程] | 上线后频繁回滚/事故 | CI 门禁：覆盖率 ≥ 85% + 契约测试 + 月度混沌演练 |
| 10 | 忽略 [文档/Runbook/ADR] 同步更新 | 人员流动后无人会用/敢改 | 代码合并强制要求更新 Runbook/ADR，CI 检查 `docs/` 变更 |

---

## 8. Hermes 执行策略

1. **分阶段执行**：
   - 第一轮：只做 Phase 0（INFRA-01 ~ INFRA-08），完成后输出 Phase 0 验收报告。
   - 第二轮：Phase 1（SEC-01 ~ SEC-05），完成后输出报告。
   - 第三轮：Phase 2 与 Phase 3 并行（DIST/REL + AI），由 Hermes 拆分子任务并行派发。
   - 第四轮：Phase 4（UI + COST + DEV）。
   - 第五轮：Phase 6 测试与验收。

2. **每个任务的执行动作**：
   - 创建/修改代码、配置、测试
   - 本地 `docker-compose up -d` 启动依赖
   - 运行对应 `pytest` 验收
   - 更新 `docs/progress/<PHASE>-<TASK>.md` 小报告

3. **遇到阻塞**：
   - 立即在报告中标记 ❌ 并说明原因
   - 不要跳过阻塞项继续下一阶段
   - 如需技术选型变更，引用第 6 节选型表，必须获得明确授权

4. **产物规范**：
   - Python 代码遵循 PEP 8，使用 `pyproject.toml`
   - 配置使用 `.env` 或 K8s Secret，禁止硬编码密码
   - 每个模块必须带 `README.md` 说明用法和验证命令
   - 所有测试必须可重复运行，禁止 Flaky Test

---

## 9. 报告格式（每个 Phase 完成后必须输出）

```markdown
## Phase X 验收报告

### 任务完成情况
| 任务 ID | 状态 | 关键证据 / 输出 |
|---------|------|-----------------|
| XXX-01  | ✅/❌ | <命令 + 结果摘要> |
| ...     | ...  | ...             |

### 已交付文件清单
- `<REPO_PATH>/path/to/file`: <一句话说明>

### 阻塞 / 失败项
- <任务 ID>: <原因> <建议解决方案>

### 性能/安全关键指标
- 单元测试覆盖率: XX%
- P99 延迟: X.XXs
- 关键漏洞: 0 Critical / 0 High
- RTO / RPO: XXmin / XXmin

### 下一步
- <建议>
```

---

## 10. 最终完整交付物清单

当所有 Phase 完成后，仓库中必须存在：

```text
<REPO_PATH>/
├── docker-compose.yml                    # 全栈本地启动
├── docker-compose.infra.yml              # Phase 0 专用
├── docker-compose.prod.yml               # Phase 1+ 生产镜像
├── docker-compose.monitoring.yml         # 监控栈
├── pyproject.toml / requirements.txt
├── apps/console/                         # React 前端
├── libs/liuhao-core/                     # 公共基础库
├── libs/liuhao-resilience/               # 熔断/重试/舱壁库
├── src/                                  # 全部后端源码
├── migrations/                           # Alembic 迁移
├── tests/                                # 单元/集成/E2E/性能/混沌
├── scripts/ops/                          # 备份/恢复/演练/账单
├── config/monitoring/                    # Prometheus/Grafana/Loki/Alertmanager
├── infra/                                # 各服务部署配置
└── docs/
    ├── infrastructure/                   # 部署/健康检查文档
    ├── runbook.md                        # 运维手册
    ├── adr/                              # 架构决策记录
    └── production-acceptance.md          # 生产验收清单 + 签收单
```

---

**现在开始执行。先从 Phase 0 开始，逐任务完成并输出 Phase 0 验收报告。**

---
