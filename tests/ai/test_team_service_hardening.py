"""团队模块专属硬化测试（补缺口轮，2026-11-17）。

覆盖此前零专属测试的 ``src/ai/team_store.py``（TeamStore）与
``src/ai/team_service.py``（TeamService）：

* 空白开局：新库无队员，派活才按需建队员；
* 任务状态机：held → running、reject / cancel / fail / block / interrupted
  的终态语义与幂等、连续失败升级、进度钳制；
* 服务层真实用户路径：待批派活 → 拒绝、急停、重启恢复、汇总分桶；
* 队员工作区路径穿越防护与产物落盘/回读；
* 队员换模型：只存配置 id、不存 Key，队长不可配、未知配置不静默顶替。

全部用 tmp_path 独立 SQLite + MockProvider，不触碰真实团队库。
"""

from __future__ import annotations

import pytest

from src.ai.providers import MockProvider
from src.ai.team_service import TeamService
from src.ai.team_store import TeamStore


@pytest.fixture()
def store(tmp_path):
    s = TeamStore(str(tmp_path / "team.db"))
    yield s
    s.close()


@pytest.fixture()
def service(store, tmp_path, monkeypatch):
    # 队员工作区产物落到临时目录，不写真实 data/agents。
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    return TeamService(
        provider=MockProvider(name="test-mock", model="test-model"), store=store
    )


# --------------------------------------------------------------------------- #
# TeamStore：空白开局与按需建队员
# --------------------------------------------------------------------------- #


def test_blank_start_has_no_members_until_first_task(store):
    assert store.list_members() == []
    assert store.team_summary()["total_members"] == 0

    task = store.create_task(role="researcher", title="查一下竞品价格")
    assert task["status"] == "running"
    assert task["correlation_id"] == task["task_id"]  # 没给时默认用 task_id

    members = store.list_members()
    assert [m["role"] for m in members] == ["researcher"]
    assert members[0]["status"] == "running"
    assert members[0]["current_task"]["task_id"] == task["task_id"]
    assert members[0]["latest_result"] is None


def test_store_rejects_unknown_role_and_empty_title(store):
    with pytest.raises(ValueError):
        store.create_task(role="wizard", title="施法")
    with pytest.raises(ValueError):
        store.create_task(role="coder", title="   ")
    with pytest.raises(ValueError):
        store.create_held_task(role="coder", title="")
    with pytest.raises(ValueError):
        store.ensure_member("wizard")
    with pytest.raises(KeyError):
        store.get_task("team_task_nope")
    with pytest.raises(KeyError):
        store.get_member("coder")  # 没派过活就不存在，不预摆


def test_member_order_is_fixed_formation(store):
    for role in ("coder", "manager", "analyst"):
        store.ensure_member(role)
    assert [m["role"] for m in store.list_members()] == [
        "manager",
        "analyst",
        "coder",
    ]
    # ensure_member 幂等：重复调用不覆盖已有 agent_id 之外的状态，
    # 但显式传新 agent_id 时会改绑。
    store.ensure_member("coder", agent_id="coder-custom")
    assert store.get_member("coder")["agent_id"] == "coder-custom"


# --------------------------------------------------------------------------- #
# TeamStore：状态机
# --------------------------------------------------------------------------- #


def test_held_task_lifecycle_and_reject_idempotence(store):
    held = store.create_held_task(
        role="coder", title="删旧文件", meta={"danger_kind": "delete_file"}
    )
    assert held["status"] == "awaiting_approval"
    assert held["meta"] == {"danger_kind": "delete_file"}
    # 待批时队员不标忙
    assert store.get_member("coder")["status"] == "idle"

    running = store.mark_running(held["task_id"])
    assert running["status"] == "running"
    assert running["started_at"] is not None
    assert store.get_member("coder")["status"] == "running"
    with pytest.raises(ValueError):
        store.mark_running(held["task_id"])  # 已经在跑，不能重复开始

    rejected_src = store.create_held_task(role="analyst", title="发外部邮件")
    rejected = store.reject_task(rejected_src["task_id"], "风险太大")
    assert rejected["status"] == "rejected"
    assert rejected["error"] == "风险太大"
    # 终态再 reject / cancel / complete 都是原样返回，不改状态
    assert store.reject_task(rejected_src["task_id"])["status"] == "rejected"
    assert store.cancel_task(rejected_src["task_id"])["status"] == "rejected"
    assert store.complete_task(rejected_src["task_id"], "x")["status"] == "rejected"


def test_failure_escalation_and_complete_resets_counter(store):
    first = store.create_task(role="researcher", title="任务一")
    failed = store.fail_task(first["task_id"], "超时")
    assert failed["status"] == "failed"
    member = store.get_member("researcher")
    assert member["consecutive_failures"] == 1
    assert member["escalated"] is False
    assert member["status"] == "failed"

    second = store.create_task(role="researcher", title="任务二")
    store.fail_task(second["task_id"], "又超时")
    member = store.get_member("researcher")
    assert member["consecutive_failures"] == 2
    assert member["escalated"] is True  # 连续失败 2 次升级问 Joseph

    third = store.create_task(role="researcher", title="任务三")
    done = store.complete_task(
        third["task_id"], result_text="结果", sources=["https://example.com"],
        tokens_used=42,
    )
    assert done["status"] == "done"
    assert done["sources"] == ["https://example.com"]
    assert done["tokens_used"] == 42
    assert done["step_index"] == done["step_total"]  # 完成自动推满进度
    member = store.get_member("researcher")
    assert member["consecutive_failures"] == 0
    assert member["escalated"] is False
    assert member["status"] == "idle"
    assert member["latest_result"]["task_id"] == third["task_id"]


def test_block_and_interrupted_do_not_count_as_member_failures(store):
    blocked_src = store.create_task(role="analyst", title="整理报表")
    blocked = store.block_task(blocked_src["task_id"], "没有配置模型")
    assert blocked["status"] == "needs_model"
    member = store.get_member("analyst")
    assert member["consecutive_failures"] == 0
    assert member["status"] == "idle"

    cut_src = store.create_task(role="analyst", title="整理报表二")
    cut = store.mark_interrupted(cut_src["task_id"], "网关重启前中断")
    assert cut["status"] == "failed"
    assert cut["error"] == "网关重启前中断"
    member = store.get_member("analyst")
    assert member["consecutive_failures"] == 0  # 中断不是队员的错
    assert member["escalated"] is False


def test_progress_is_clamped_and_frozen_on_terminal(store):
    task = store.create_task(role="reminder", title="提醒开会", step_total=3)
    assert store.update_progress(task["task_id"], 99)["step_index"] == 3
    assert store.update_progress(task["task_id"], -5)["step_index"] == 0
    assert store.update_progress(task["task_id"], 2)["step_index"] == 2
    store.cancel_task(task["task_id"], "不用了")
    # 终态任务进度不再动
    assert store.update_progress(task["task_id"], 1)["step_index"] == 2
    cancelled = store.get_task(task["task_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "不用了"


def test_list_tasks_filters_limit_and_messages(store):
    a = store.create_task(role="coder", title="写脚本", correlation_id="corr-1")
    b = store.create_task(role="coder", title="改脚本", correlation_id="corr-1")
    store.create_task(role="analyst", title="做表格")
    store.complete_task(a["task_id"], "ok")

    assert {t["task_id"] for t in store.list_tasks(role="coder")} == {
        a["task_id"],
        b["task_id"],
    }
    assert [t["task_id"] for t in store.list_tasks(status="done")] == [a["task_id"]]
    assert len(store.list_tasks(limit=1)) == 1
    assert len(store.list_tasks(limit=0)) == 1  # limit 下限钳到 1
    assert len(store.list_tasks(limit=9999)) == 3  # 上限钳到 200，不报错

    store.record_message(
        message_id="m1", sender="jarvis", receiver="team-coder",
        kind="delegate", summary="写脚本", correlation_id="corr-1",
    )
    store.record_message(
        message_id="m2", sender="team-coder", receiver="jarvis",
        kind="result", summary="写好了", correlation_id="corr-1",
    )
    messages = store.list_messages("corr-1")
    assert [m["message_id"] for m in messages] == ["m1", "m2"]  # 按时间升序
    assert store.list_messages("corr-other") == []

    summary = store.team_summary()
    assert summary["total_members"] == 2
    assert summary["running_count"] == 2  # b 与 analyst 的任务还在跑


def test_tasks_and_members_survive_reopen(tmp_path):
    path = str(tmp_path / "team.db")
    first = TeamStore(path)
    task = first.create_task(role="coder", title="持久化任务")
    first.complete_task(task["task_id"], "落盘结果")
    first.close()

    second = TeamStore(path)
    try:
        reloaded = second.get_task(task["task_id"])
        assert reloaded["status"] == "done"
        assert reloaded["result_text"] == "落盘结果"
        assert second.get_member("coder")["latest_result"]["task_id"] == task["task_id"]
    finally:
        second.close()


# --------------------------------------------------------------------------- #
# TeamStore：队员模型配置（P6，只存 id 不存 Key）
# --------------------------------------------------------------------------- #


def test_store_set_member_model_rules(store):
    with pytest.raises(ValueError):
        store.set_member_model("manager", model_config_id="cfg-1")  # 队长不单独配
    with pytest.raises(KeyError):
        store.set_member_model("coder", model_config_id="cfg-1")  # 队员不存在

    store.ensure_member("coder")
    updated = store.set_member_model(
        "coder", model_config_id="cfg-1", model_provider="openai",
        model_name="gpt-x", model_base_url="https://api.example.com",
    )
    assert updated["model_config_id"] == "cfg-1"
    assert updated["model_name"] == "gpt-x"
    assert "api_key" not in updated and "key" not in updated  # 队员表不存 Key

    cleared = store.set_member_model("coder")  # 空 id = 清掉，回共用默认
    assert cleared["model_config_id"] == ""
    assert cleared["model_name"] == ""


# --------------------------------------------------------------------------- #
# TeamService：派活校验与待批路径（真实用户路径）
# --------------------------------------------------------------------------- #


def test_dispatch_validation_errors(service):
    with pytest.raises(ValueError):
        service.dispatch("boss", "manager", "队长亲自干")  # 队长不接派活
    with pytest.raises(ValueError):
        service.dispatch("boss", "wizard", "施法")
    with pytest.raises(ValueError):
        service.dispatch("boss", "coder", "   ")
    with pytest.raises(ValueError):
        service.dispatch("boss", "coder", "干活", danger_kind="launch_missiles")
    with pytest.raises(ValueError):
        service.dispatch("boss", "", "干活")


def test_dispatch_held_then_reject_executes_nothing(service, store):
    result = service.dispatch(
        "boss", "researcher", "给客户发报价",
        approval_required=True, danger_kind="send_message",
    )
    assert result["status"] == "awaiting_approval"
    task_id = result["task_id"]
    assert store.get_task(task_id)["status"] == "awaiting_approval"
    # 只建了被派活的队员记录，没凭空多出其他队员（空白开局）
    assert [m["role"] for m in store.list_members()] == ["researcher"]

    rejected = service.reject_task(task_id, "boss", "先别发")
    assert rejected["status"] == "rejected"
    assert store.get_task(task_id)["status"] == "rejected"
    # 拒绝未知任务：如实报 failed，不抛错炸调用方
    missing = service.reject_task("team_task_nope", "boss")
    assert missing["status"] == "failed"
    assert "unknown" in missing["error"]


def test_stop_all_cancels_running_and_awaiting(service, store):
    running = store.create_task(role="coder", title="正在写")
    held = service.dispatch(
        "boss", "analyst", "外发文件",
        approval_required=True, danger_kind="send_message",
    )
    done = store.create_task(role="reminder", title="已完成的事")
    store.complete_task(done["task_id"], "好")

    result = service.stop_all("boss")
    assert result["cancelled_count"] == 2
    assert set(result["cancelled"]) == {running["task_id"], held["task_id"]}
    assert store.get_task(running["task_id"])["status"] == "cancelled"
    assert store.get_task(held["task_id"])["status"] == "cancelled"
    assert store.get_task(done["task_id"])["status"] == "done"  # 终态不动
    assert service.stop_all("boss")["cancelled_count"] == 0  # 再急停无事可停


def test_recover_unfinished_only_stale_protects_current_process(service, store):
    running = store.create_task(role="coder", title="本进程刚派的活")
    held = store.create_held_task(role="analyst", title="等批的活")

    # 默认 only_stale=True：本进程新建的在跑任务不被误伤，待批原样保留
    report = service.recover_unfinished()
    assert report["interrupted"] == []
    assert report["awaiting_approval"] == [held["task_id"]]
    assert store.get_task(running["task_id"])["status"] == "running"

    # 显式全量恢复：标失败并写清中断原因，不计队员失败
    report = service.recover_unfinished(only_stale=False)
    assert report["interrupted"] == [running["task_id"]]
    recovered = store.get_task(running["task_id"])
    assert recovered["status"] == "failed"
    assert "中断" in recovered["error"]
    assert store.get_member("coder")["consecutive_failures"] == 0
    assert store.get_task(held["task_id"])["status"] == "awaiting_approval"


def test_summarize_buckets_and_skips_unknown_ids(service, store):
    done = store.create_task(role="coder", title="写完的", plan_id="plan-1")
    store.complete_task(done["task_id"], "成果", sources=["src-a"])
    failed = store.create_task(role="coder", title="写崩的", plan_id="plan-1")
    store.fail_task(failed["task_id"], "语法错误")
    blocked = store.create_task(role="analyst", title="卡住的", plan_id="plan-2")
    store.block_task(blocked["task_id"], "没有配置模型")
    pending = store.create_task(role="reminder", title="进行中", plan_id="plan-1")

    summary = service.summarize(
        task_ids=[done["task_id"], failed["task_id"], blocked["task_id"],
                  pending["task_id"], "team_task_nope"]
    )
    assert [t["task_id"] for t in summary["completed"]] == [done["task_id"]]
    assert summary["completed"][0]["sources"] == ["src-a"]
    assert [t["task_id"] for t in summary["failed"]] == [failed["task_id"]]
    assert [t["task_id"] for t in summary["blocked"]] == [blocked["task_id"]]
    assert [t["task_id"] for t in summary["pending"]] == [pending["task_id"]]
    assert "已完成 1 项，未完成 3 项" in summary["summary_text"]
    assert "语法错误" in summary["summary_text"]

    by_plan = service.summarize(plan_id="plan-1")
    assert {t["task_id"] for t in by_plan["completed"] + by_plan["failed"]
            + by_plan["pending"]} == {
        done["task_id"], failed["task_id"], pending["task_id"],
    }


# --------------------------------------------------------------------------- #
# TeamService：工作区与产物（P5）
# --------------------------------------------------------------------------- #


def test_workspace_dir_rejects_path_traversal(service):
    for bad in ("../evil", "a/b", "a\\b", "", ".hidden", "x" * 65):
        with pytest.raises(ValueError):
            service.member_workspace_dir(bad)
    ok = service.member_workspace_dir("team-coder_1")
    assert ok.is_dir()
    assert ok.name == "workspace"


def test_artifact_write_and_read_roundtrip(service, store):
    task = store.create_task(role="coder", title="写周报")
    artifact = service.write_artifact("team-coder", task["task_id"], "正文内容")
    assert artifact["kind"] == "file"
    assert artifact["name"] == f"{task['task_id']}.md"

    store.complete_task(task["task_id"], "正文内容", artifacts=[artifact])
    read_back = service.read_artifact(task["task_id"], artifact["name"])
    assert "正文内容" in read_back["content"]
    assert "写周报" in read_back["content"]  # 文件头带任务标题

    with pytest.raises(ValueError):
        service.read_artifact(task["task_id"], "../secret.md")
    with pytest.raises(KeyError):
        service.read_artifact(task["task_id"], "no-such.md")
    with pytest.raises(KeyError):
        service.read_artifact("team_task_nope", artifact["name"])


# --------------------------------------------------------------------------- #
# TeamService：队员换模型（P6）
# --------------------------------------------------------------------------- #


def test_service_set_member_model_rules(service, store):
    with pytest.raises(ValueError):
        service.set_member_model("manager", model_config_id="cfg-1")
    with pytest.raises(ValueError):
        service.set_member_model("wizard", model_config_id="cfg-1")
    # 配置 id 在模型配置中心找不到：如实报错，不静默换默认顶上
    store.ensure_member("coder")
    with pytest.raises(ValueError):
        service.set_member_model("coder", model_config_id="cfg-does-not-exist-xyz")
    # 清掉未配过的模型是合法操作，回到共用默认
    cleared = service.set_member_model("coder", model_config_id="")
    assert cleared["model_config_id"] == ""
    with pytest.raises(ValueError):
        service.get_member_model("wizard")
    assert service.get_member_model("coder")["role"] == "coder"


def test_provider_for_member_uses_factory_when_injected(store):
    sentinel = object()
    svc = TeamService(
        provider=MockProvider(name="m", model="x"), store=store,
        provider_factory=lambda role: sentinel,
    )
    assert svc.provider_for_member("coder") is sentinel
