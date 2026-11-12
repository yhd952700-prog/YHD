"""管家"真拆解 / 真派 / 真汇总"三件事的回归。

覆盖：
* 一句话里多件事 → 拆成多个任务（不是一条待办）；
* 模型挑人只能落在真实花名册上，理由必须与能力匹配；
* 模型挑错人/挑不存在的人 → 管家按能力改派并写明理由；
* 关键词计划里带不可执行的计算任务时，不能阻止模型计划（整条失败回归）；
* 汇总把"谁做了什么、为什么、产物在哪、哪件失败、要不要重试"写清楚。
"""
from __future__ import annotations

import json

from src.ai.llm_decomposer import LLMTaskDecomposer
from src.ai.llm_support import compose_execution_summary
from src.kernels.execution import Goal, GoalDecomposer


ROSTER = [
    {
        "name": "小研",
        "role": "调研员",
        "capabilities": ["network_bus"],
        "agent_ids": ["emp-yan-a0"],
    },
    {
        "name": "小文",
        "role": "撰稿/前端",
        "capabilities": ["execution_pipeline", "file_write"],
        "agent_ids": ["emp-wen-a0"],
    },
]


def _goal(text: str = "帮我把官网首页改版，顺便调研一下同行都怎么写首页文案") -> Goal:
    return Goal(id="goal-butler", natural_language=text, scope="L1")


def _two_task_plan() -> str:
    return json.dumps(
        [
            {
                "name": "改版官网首页",
                "description": "产出一版新的首页 HTML",
                "capability": "file_write",
                "inputs": {"path": "site/index.html", "content": "<h1>新首页</h1>"},
                "depends_on": [],
                "assign_to": "小文",
                "assign_reason": "职责包含前端改写，能直接落盘首页文件",
            },
            {
                "name": "调研同行首页文案",
                "description": "找出同行首页文案的常见写法",
                "capability": "network_bus",
                "inputs": {"query": "同行官网首页文案 写法"},
                "depends_on": [],
                "assign_to": "小研",
                "assign_reason": "调研员默认能力是网络检索，适合先找素材",
            },
        ],
        ensure_ascii=False,
    )


def _decomposer(reply, roster=ROSTER, capabilities=None, calls=None):
    def _generate(prompt: str) -> str:
        if calls is not None:
            calls.append(prompt)
        return reply(prompt) if callable(reply) else reply

    return LLMTaskDecomposer(
        GoalDecomposer(),
        generator=_generate,
        capability_provider=lambda: capabilities
        or {"network_bus", "execution_pipeline", "file_write"},
        employee_provider=lambda: roster,
    )


class TestButlerAssignment:
    def test_one_sentence_with_two_jobs_becomes_two_assigned_tasks(self):
        prompts = []
        tasks = _decomposer(_two_task_plan(), calls=prompts).decompose(_goal())

        assert [task.name for task in tasks] == ["改版官网首页", "调研同行首页文案"]
        by_name = {task.name: task for task in tasks}
        assert by_name["改版官网首页"].assigned_agent == "emp-wen-a0"
        assert by_name["改版官网首页"].assigned_employee == "小文"
        assert "前端" in by_name["改版官网首页"].assignment_reason
        assert by_name["调研同行首页文案"].assigned_agent == "emp-yan-a0"
        assert by_name["调研同行首页文案"].assigned_employee == "小研"
        assert "检索" in by_name["调研同行首页文案"].assignment_reason

        # 提示词必须真的包含"多件事拆开"和花名册，否则模型无从遵守。
        prompt = prompts[0]
        assert "assign_to" in prompt
        assert "小研" in prompt and "小文" in prompt
        assert "多件不同的事" in prompt

    def test_cached_plan_keeps_the_same_assignee_and_reason(self):
        decomposer = _decomposer(_two_task_plan())
        first = decomposer.decompose(_goal())
        second = decomposer.decompose(_goal())

        assert [t.assigned_agent for t in first] == [t.assigned_agent for t in second]
        assert [t.assignment_reason for t in first] == [
            t.assignment_reason for t in second
        ]

    def test_single_named_employee_gets_every_compatible_task(self):
        """「让小文…」时，能力兼容的任务全部强派给小文，不听模型逐任务挑人。"""
        tasks = _decomposer(_two_task_plan()).decompose(
            _goal("让小文把官网首页改版，顺便调研一下同行都怎么写首页文案")
        )
        by_name = {task.name: task for task in tasks}

        doc = by_name["改版官网首页"]
        assert doc.assigned_employee == "小文"
        assert doc.assigned_agent == "emp-wen-a0"
        assert doc.assignment_reason == "用户点名指派"

        # 小文没有 network_bus 能力：不越过能力门，仍交给能执行的小研。
        research = by_name["调研同行首页文案"]
        assert research.assigned_employee == "小研"
        assert research.assigned_agent == "emp-yan-a0"

    def test_single_named_employee_without_capability_is_not_forced(self):
        """点名的人能力不兼容时，不能把任务硬塞给他。"""
        def _plan(prompt: str) -> str:
            return json.dumps(
                [
                    {
                        "name": "调研同行首页文案",
                        "description": "找出同行首页文案的常见写法",
                        "capability": "network_bus",
                        "inputs": {"query": "同行官网首页文案 写法"},
                        "depends_on": [],
                        "assign_to": "小研",
                        "assign_reason": "调研员默认能力是网络检索",
                    }
                ],
                ensure_ascii=False,
            )

        tasks = _decomposer(_plan).decompose(
            _goal("让小文帮我把官网首页改版，顺便调研一下同行都怎么写首页文案")
        )
        assert [task.assigned_employee for task in tasks] == ["小研"]

    def test_two_named_employees_do_not_trigger_forced_assignment(self):
        """名字出现两个时用户没说「全交给他」，保持按能力分派。"""
        tasks = _decomposer(_two_task_plan()).decompose(
            _goal("让小文和小研把官网首页改版，顺便调研同行文案")
        )
        assert all(task.assignment_reason != "用户点名指派" for task in tasks)

    def test_model_picking_a_wrong_or_unknown_employee_is_reassigned(self):
        def _plan(prompt: str) -> str:
            return json.dumps(
                [
                    {
                        "name": "调研同行首页文案",
                        "description": "找出同行首页文案的常见写法",
                        "capability": "network_bus",
                        "inputs": {"query": "同行官网首页文案 写法"},
                        "depends_on": [],
                        # 小文没有 network_bus 能力；另一个名字根本不存在。
                        "assign_to": "小文",
                        "assign_reason": "模型编的理由",
                    },
                    {
                        "name": "改版官网首页",
                        "description": "产出一版新的首页 HTML",
                        "capability": "file_write",
                        "inputs": {"path": "site/index.html", "content": "<h1>新首页</h1>"},
                        "depends_on": [],
                        "assign_to": "不存在的员工",
                        "assign_reason": "",
                    },
                ],
                ensure_ascii=False,
            )

        tasks = _decomposer(_plan).decompose(_goal())
        by_name = {task.name: task for task in tasks}

        research = by_name["调研同行首页文案"]
        assert research.assigned_agent == "emp-yan-a0"
        assert "network_bus" in research.assignment_reason
        assert "改派" in research.assignment_reason

        write = by_name["改版官网首页"]
        assert write.assigned_agent == "emp-wen-a0"
        assert "file_write" in write.assignment_reason

    def test_without_a_roster_no_employee_is_invented(self):
        tasks = _decomposer(_two_task_plan(), roster=[]).decompose(_goal())
        assert all(getattr(task, "assigned_employee", None) is None for task in tasks)
        assert all(getattr(task, "assignment_reason", None) is None for task in tasks)


class TestDeterministicPlanContract:
    def test_doomed_compute_task_does_not_block_the_model_plan(self):
        """提到 Python 不等于要求计算：关键词计划不能抢走拆解权。"""
        calls = []
        model_plan = json.dumps(
            [
                {
                    "name": "查 Python 3.13 新特性",
                    "description": "找出官方列出的新特性",
                    "capability": "network_bus",
                    "inputs": {"query": "Python 3.13 新特性 官方"},
                    "depends_on": [],
                },
                {
                    "name": "整理三条要点",
                    "description": "把新特性整理成三条",
                    "capability": "execution_pipeline",
                    "inputs": {"instruction": "整理成三条要点"},
                    "depends_on": ["查 Python 3.13 新特性"],
                },
            ],
            ensure_ascii=False,
        )
        decomposer = _decomposer(
            model_plan,
            roster=[],
            capabilities={"network_bus", "execution_pipeline", "python_compute"},
            calls=calls,
        )
        tasks = decomposer.decompose(
            _goal("查一下 Python 3.13 有哪些新特性，并整理成三条要点")
        )

        assert calls, "关键词计划含 python_compute 却缺 code/expression，应改问模型"
        assert [task.capability_id for task in tasks] == [
            "network_bus",
            "execution_pipeline",
        ]
        assert "Compute" not in [task.name for task in tasks]


class TestReadableSummary:
    def test_summary_prompt_carries_owner_reason_and_artifacts(self):
        prompts = []

        def _generate(prompt: str) -> str:
            prompts.append(prompt)
            return "首页已改版并落到 site/index.html，文案调研也已完成。"

        records = [
            {
                "name": "改版官网首页",
                "capability": "file_write",
                "status": "completed",
                "result": {"path": "site/index.html", "result": "首页已更新"},
                "assigned_employee": "小文",
                "assignment_reason": "职责包含前端改写",
            },
            {
                "name": "调研同行首页文案",
                "capability": "network_bus",
                "status": "completed",
                "result": {"result": "同行多用一句话价值主张"},
                "assigned_employee": "小研",
                "assignment_reason": "调研员默认能力是网络检索",
            },
        ]
        summary = compose_execution_summary("改首页并调研文案", records, _generate)

        prompt = prompts[0]
        assert "执行人：小文" in prompt
        assert "指派理由：职责包含前端改写" in prompt
        assert "产物：site/index.html" in prompt
        assert "是否建议重试" in prompt
        assert "不要把子任务的原始输出整段粘贴" in prompt
        # 没有外链时不许再要求"至少 3 条来源"（旧行为会逼模型编链接）。
        assert "至少 3 条来源" not in prompt
        assert summary.startswith("首页已改版")

    def test_model_summary_claiming_all_done_is_corrected_by_records(self):
        records = [
            {
                "name": "改版官网首页",
                "capability": "file_write",
                "status": "completed",
                "result": {"path": "site/index.html", "result": "首页已更新"},
            },
            {
                "name": "调研同行首页文案",
                "capability": "network_bus",
                "status": "failed",
                "error": "HTTP 429 rate limit",
            },
        ]
        summary = compose_execution_summary(
            "调研文案", records, lambda prompt: "全部完成，调研结果很充分。"
        )
        assert "没有完成" in summary
        assert "调研同行首页文案" in summary
        assert "HTTP 429 rate limit" in summary
        assert "建议直接重试" in summary

    def test_copywriting_prompt_does_not_demand_sources(self, monkeypatch):
        import src.ai.tools_local as tools_local

        prompts = []

        def _fake_call(prompt: str) -> str:
            prompts.append(prompt)
            return "新品上线，更快更稳。"

        monkeypatch.setattr(tools_local, "_model_call", _fake_call)
        result = tools_local.llm_synthesis(
            instruction="写一段 200 字宣传文案", goal="官网首页改版"
        )

        assert result["success"] is True
        assert result["result"].startswith("新品上线")
        assert "不要求来源" in prompts[0]
        assert "缺少来源" in prompts[0]

    def test_fallback_summary_names_failure_reason_and_retry_advice(self):
        records = [
            {
                "name": "改版官网首页",
                "capability": "file_write",
                "status": "completed",
                "result": {"path": "site/index.html", "result": "首页已更新。"},
                "assigned_employee": "小文",
                "assignment_reason": "职责包含前端改写",
            },
            {
                "name": "调研同行首页文案",
                "capability": "network_bus",
                "status": "failed",
                "error": "模型调用超过 150 秒未返回",
            },
        ]
        summary = compose_execution_summary("改首页并调研文案", records)

        assert "已完成 1 项任务" in summary
        assert "分工：" in summary
        assert "小文" in summary and "职责包含前端改写" in summary
        assert "产物：site/index.html" in summary
        assert "仍有 1 项任务失败" in summary
        assert "调研同行首页文案" in summary
        assert "模型调用超过 150 秒未返回" in summary
        assert "建议直接重试" in summary
        assert "全部完成" not in summary


class TestNoFabrication:
    """P0：没有真实来源就不许有引用；没有产出就不许说完成。"""

    def test_offline_network_task_never_invents_a_domain(self):
        records = [
            {
                "name": "调研今日 AI 新闻",
                "capability": "network_bus",
                "status": "failed",
                "error": "网络不可用：Connection refused",
            }
        ]
        summary = compose_execution_summary("调研今天的 AI 新闻", records)

        assert "http://" not in summary and "https://" not in summary
        assert "example" not in summary.lower()
        assert "未检索到来源" in summary
        assert "失败" in summary

    def test_model_summary_with_example_domain_is_rejected(self):
        records = [
            {
                "name": "整理周报",
                "capability": "execution_pipeline",
                "status": "completed",
                "result": {"result": "本周完成了三件事。"},
            },
            {
                "name": "调研竞品动态",
                "capability": "network_bus",
                "status": "failed",
                "error": "网络不可用：无法访问外网",
            },
        ]
        fabricated = (
            "本周周报已完成，参考 https://example1.com/weekly 与 "
            "https://example2.com/brief（抓取时间 2026-11-08）。"
        )
        summary = compose_execution_summary(
            "写一份本周周报，并调研竞品动态", records, lambda prompt: fabricated
        )

        assert "example1.com" not in summary
        assert "example2.com" not in summary
        assert "抓取时间 2026-11-08" not in summary
        assert "未检索到来源" in summary

    def test_write_permission_failure_is_never_reported_as_done(self):
        records = [
            {
                "name": "写周报文件",
                "capability": "file_write",
                "status": "completed",
                "result": {
                    "success": False,
                    "error": "permission denied：无写权限",
                    "path": "reports/weekly.md",
                },
            }
        ]
        summary = compose_execution_summary(
            "把周报摘要写入文件 reports/weekly.md", records
        )

        assert summary.startswith("未完成")
        assert "已完成 1 项任务但无产出" in summary
        assert "reports/weekly.md" not in summary
        assert "已完成并产出" not in summary
        assert "100%" not in summary

    def test_completed_without_output_is_not_counted_as_complete(self):
        records = [
            {
                "name": "写周报",
                "capability": "execution_pipeline",
                "status": "completed",
                "result": {"result": "没有向工作区写入任何文件"},
            }
        ]
        summary = compose_execution_summary("整理本周进展", records)

        assert "已完成 1 项任务但无产出" in summary
        assert "不计入完成" in summary
        assert "已完成并产出" not in summary

    def test_preflight_notice_is_reported_before_execution(self, monkeypatch):
        from src.ai.agent_runtime import AgentRunState, AgentRuntime

        runtime = AgentRuntime(
            scope="L1", executable_capabilities={"execution_pipeline"}
        )
        order = []

        def _fake_execute(goal, **kwargs):
            order.append("execute")
            raise RuntimeError("stub execution")

        monkeypatch.setattr(runtime.engine, "execute_goal", _fake_execute)
        result = runtime.run_goal(
            "把周报摘要写入文件 reports/weekly.md",
            on_notice=lambda message: order.append(f"notice:{message}"),
        )

        assert order, "派活前必须先把能力边界说清楚"
        assert order[0].startswith("notice:")
        assert "file_write" in order[0]
        assert "execute" in order, "提示之后仍然尝试执行（提示不是阻断）"
        assert result.state == AgentRunState.FAILED

    def test_preflight_is_silent_when_capabilities_are_available(self):
        from src.ai.agent_runtime import AgentRuntime

        runtime = AgentRuntime(
            scope="L1",
            executable_capabilities={"file_write", "network_bus", "execution_pipeline"},
        )

        assert runtime._preflight_capability_notices(
            "把周报摘要写入文件 reports/weekly.md，并联网查最新动态"
        ) == []

    def test_model_summary_crediting_the_wrong_employee_is_rejected(self):
        records = [
            {
                "name": "撰写向量数据库说明",
                "capability": "execution_pipeline",
                "status": "completed",
                "assigned_employee": "小研",
                "assignment_reason": "用户点名指派",
                "result": {"result": "向量数据库按语义而非字面关键词检索。"},
            }
        ]
        fabricated = "这项说明由小文撰写，因为用户点名指派给小文。"
        summary = compose_execution_summary(
            "解释什么是向量数据库", records, lambda prompt: fabricated
        )

        assert "小文" not in summary
        assert "小研" in summary
        assert "用户点名指派" in summary

    def test_pinned_run_ignores_the_decomposers_stale_employee(self):
        from types import SimpleNamespace

        from src.ai.agent_runtime import _record_employee

        task = SimpleNamespace(assigned_agent="agent-x", assigned_employee="小文")
        resolver = lambda agent_id: "小研" if agent_id == "agent-x" else None

        # 点名指派后，拆解器先前写上的名字不可信，以真实解析为准。
        assert _record_employee(task, resolver, "agent-x") == "小研"
        # 解析不到就不写名字，绝不沿用旧名字。
        assert _record_employee(task, None, "agent-x") is None
        # 非点名路径保持原行为。
        assert _record_employee(task, resolver, None) == "小文"

    def test_task_name_crediting_an_outsider_is_scrubbed(self):
        """任务名也是模型写的：不能把活儿记到没参与的人头上。"""
        records = [
            {
                "name": "小文撰写宣传文案",
                "capability": "execution_pipeline",
                "status": "completed",
                "assigned_employee": "小研",
                "result": {"result": "这是一段宣传文案。"},
            }
        ]
        summary = compose_execution_summary("写个宣传文案", records)

        assert "小文" not in summary
        assert "某位员工撰写宣传文案" in summary
        assert "小研" in summary

    def test_failure_summary_never_claims_output(self):
        from src.ai.llm_support import failure_summary

        cases = [
            ("DeepSeek request timed out after 30s", "调用超时"),
            ("402 Insufficient Balance", "额度不足"),
            ("401 invalid api key", "密钥无效"),
        ]
        for error, label in cases:
            summary = failure_summary(error)
            assert summary.startswith("未完成")
            assert label in summary
            assert "已完成" not in summary
            assert "已生成" not in summary
            assert "/" not in summary and "\\" not in summary

    def test_gateway_task_prose_is_scrubbed_before_display(self):
        """任务名/描述/指派理由上屏前走同一份白名单。"""
        from src.gateway.ai_management import AIStateManager

        manager = AIStateManager.__new__(AIStateManager)
        manager._agent_employee_map = lambda: {"agent-1": "小研"}  # type: ignore[method-assign]
        entry = {
            "tasks": [
                {
                    "assigned_agent": "agent-1",
                    "name": "小文撰写宣传文案",
                    "description": "由小文负责，交给小文完成",
                    "assignment_reason": "指派给小明",
                    "status": "completed",
                }
            ]
        }

        manager._attach_employee_attribution(entry)

        task = entry["tasks"][0]
        assert task["assigned_employee"] == "小研"
        assert "小文" not in task["name"]
        assert "某某" not in task["name"]
        assert "小文" not in task["description"]
        assert "小明" not in task["assignment_reason"]

    def test_notification_text_uses_only_user_input_and_state(self):
        """通知文案不含模型生成的人名，因此无需额外校验。"""
        from src.tasks.goal_notify import goal_notification_text

        title, text = goal_notification_text(
            {"state": "failed", "natural_language": "写一份周报摘要", "failed_tasks": 1}
        )
        assert "写一份周报摘要" in text
        assert "没有完成" in text or "失败" in text
        assert "小文" not in text and "小明" not in text

    def test_runtime_exception_still_yields_an_honest_summary(self, monkeypatch):
        from src.ai.agent_runtime import AgentRunState, AgentRuntime

        runtime = AgentRuntime(scope="L1", executable_capabilities={"execution_pipeline"})

        def _boom(goal, **kwargs):
            raise RuntimeError("DeepSeek request timed out")

        monkeypatch.setattr(runtime.engine, "execute_goal", _boom)
        result = runtime.run_goal("写一份周报摘要", persist=False)

        assert result.state == AgentRunState.FAILED
        assert result.evaluation is not None
        summary = result.evaluation.summary
        assert summary.startswith("未完成")
        assert "调用超时" in summary
        assert "已完成" not in summary
        assert "已生成" not in summary
