"""第 3 批 · 只读观察：窗口信息、元素探测、节流、一键关闭与隐私审计。

这些测试全部基于注入的假窗口数据；真实 pywin32 调用只做一次冒烟，且在没有
桌面环境时直接跳过（CI 场景）。
"""

from __future__ import annotations

import hashlib
import json
import sys

import pytest

import src.automation.observe as observe_module
from src.ai.tools.kernel_tools import make_tools
from src.automation.audit import AutomationAudit
from src.automation.contracts import (
    AutomationError,
    ElementInfo,
    Observation,
    TargetScope,
    WindowInfo,
)
from src.automation.observe import (
    ELEMENTS_UNSUPPORTED_REASON,
    OBSERVE_ENV,
    FakeWindowObserver,
    ObservationPolicy,
    ObservationService,
    build_observation_tools,
    observation_enabled,
)
from src.automation.windows_observer import WindowsObserver
from src.kernels.audit import AuditEventType, AuditStore

SENSITIVE_TITLE = "机密项目-客户名单.xlsx"
SENSITIVE_PATH = r"C:\Program Files\SecretApp\secret.exe"


def _windows():
    return [
        {
            "handle": 1,
            "title": SENSITIVE_TITLE,
            "process_name": "secret.exe",
            "executable_path": SENSITIVE_PATH,
            "pid": 1234,
            "foreground": True,
            "fullscreen": True,
            "bounds": [0, 0, 1920, 1080],
        },
        {
            "handle": 2,
            "title": "记事本",
            "process_name": "notepad.exe",
            "executable_path": r"C:\Windows\notepad.exe",
            "pid": 5678,
            "bounds": [100, 100, 800, 600],
        },
    ]


def _elements():
    return {
        1: [
            {
                "handle": 11,
                "class_name": "Edit",
                "control_id": 1001,
                "enabled": True,
                "visible": True,
                "bounds": [10, 20, 100, 40],
            },
            {
                "handle": 12,
                "class_name": "Button",
                "control_id": 1002,
                "enabled": True,
                "visible": True,
                "bounds": [120, 20, 200, 40],
            },
        ]
    }


def _observer(**kwargs) -> FakeWindowObserver:
    return FakeWindowObserver(_windows(), elements=_elements(), **kwargs)


def _audit(tmp_path) -> tuple[AutomationAudit, AuditStore]:
    store = AuditStore(db_path=str(tmp_path / "audit.db"))
    store.initialize()
    return AutomationAudit(store=store), store


# ---------------------------------------------------------------- 契约与结构化字段
def test_observation_carries_structured_window_and_element_fields():
    observation = _observer().observe(TargetScope.SCREEN)

    assert observation.backend == "fake"
    assert observation.foreground is not None
    assert observation.foreground.title == SENSITIVE_TITLE
    assert observation.foreground.fullscreen is True
    assert observation.foreground.process_name == "secret.exe"
    assert observation.foreground.executable_path == SENSITIVE_PATH
    assert observation.elements_supported is True
    assert len(observation.elements) == 2
    assert observation.elements[0].bounds == (10, 20, 100, 40)
    assert observation.focus == "secret.exe"

    payload = observation.to_dict()
    assert payload["foreground"]["title"] == SENSITIVE_TITLE
    assert payload["windows"][1]["title"] == "记事本"
    assert payload["elements"][0]["class_name"] == "Edit"


def test_observation_digest_includes_new_structured_fields():
    base = Observation(focus="a", backend="fake")
    changed_title = Observation(
        focus="a",
        backend="fake",
        foreground=WindowInfo(handle=1, title=SENSITIVE_TITLE),
    )
    changed_elements = Observation(
        focus="a",
        backend="fake",
        elements=(ElementInfo(handle=9, class_name="Button"),),
        elements_supported=True,
    )
    assert base.digest() != changed_title.digest()
    assert base.digest() != changed_elements.digest()


def test_unsupported_elements_are_reported_without_fabrication():
    observer = FakeWindowObserver(_windows())
    observation = observer.observe(TargetScope.WINDOW)

    assert observation.elements == ()
    assert observation.elements_supported is False
    assert observation.elements_reason == ELEMENTS_UNSUPPORTED_REASON


def test_custom_elements_reason_is_preserved():
    observer = FakeWindowObserver(_windows(), elements_reason="仅提供窗口级信息")
    observation = observer.observe(TargetScope.WINDOW)
    assert observation.elements_reason == "仅提供窗口级信息"


def test_fake_observer_does_not_pick_missing_foreground_handle():
    observer = FakeWindowObserver(_windows(), foreground_handle=999)
    observation = observer.observe(TargetScope.WINDOW)
    assert observation.foreground is None
    assert observation.windows == ()
    assert observation.elements_reason == "没有前台窗口，无法读取元素"


# ---------------------------------------------------------------- 默认开 / 硬关闭
def test_observation_defaults_on_and_only_explicit_false_disables():
    assert observation_enabled({}) is True
    assert observation_enabled({OBSERVE_ENV: "yes"}) is True
    assert observation_enabled({OBSERVE_ENV: "1"}) is True
    assert observation_enabled({OBSERVE_ENV: "0"}) is False
    assert observation_enabled({OBSERVE_ENV: "false"}) is False
    assert observation_enabled({OBSERVE_ENV: "off"}) is False

    policy = ObservationPolicy(env={OBSERVE_ENV: "0"})
    assert policy.configured_enabled is False
    with pytest.raises(AutomationError) as excinfo:
        policy.acquire(now=0.0)
    assert excinfo.value.reason == "observation_disabled"


# ---------------------------------------------------------------- 节流
def test_throttle_rejects_excess_captures_and_recovers():
    policy = ObservationPolicy(enabled=True, max_per_window=2, window_seconds=5.0)
    observer = _observer()
    service = ObservationService(observer, policy=policy)

    assert service.observe(principal="liuhao", now=0.0).backend == "fake"
    assert service.observe(principal="liuhao", now=1.0).backend == "fake"
    with pytest.raises(AutomationError) as excinfo:
        service.observe(principal="liuhao", now=2.0)
    assert excinfo.value.reason == "observation_rate_limited"
    # 被节流拒绝时不应真的去采一次。
    assert observer.calls == 2

    # 窗口滑过后恢复。
    assert service.observe(principal="liuhao", now=6.0).backend == "fake"
    assert observer.calls == 3


def test_throttle_snapshot_reports_usage():
    policy = ObservationPolicy(enabled=True, max_per_window=3, window_seconds=10.0)
    policy.acquire(now=0.0)
    policy.acquire(now=1.0)
    snapshot = policy.snapshot(now=2.0)
    assert snapshot["enabled"] is True
    assert snapshot["max_per_window"] == 3
    assert snapshot["used_in_window"] == 2


# ---------------------------------------------------------------- 一键关闭
def test_disable_and_enable_observation():
    policy = ObservationPolicy(enabled=True)
    service = ObservationService(_observer(), policy=policy)

    assert service.observe(principal="liuhao", now=0.0) is not None
    policy.disable("privacy")
    assert policy.enabled is False
    with pytest.raises(AutomationError) as excinfo:
        service.observe(principal="liuhao", now=1.0)
    assert excinfo.value.reason == "observation_disabled"

    policy.enable()
    assert policy.enabled is True
    assert service.observe(principal="liuhao", now=2.0) is not None


# ---------------------------------------------------------------- 隐私审计
def test_audit_stores_only_length_and_hash(tmp_path):
    audit, store = _audit(tmp_path)
    service = ObservationService(
        _observer(),
        policy=ObservationPolicy(enabled=True),
        audit=audit,
    )

    observation = service.observe(
        principal="liuhao", scope=TargetScope.SCREEN, now=0.0
    )
    events = store.query_events(event_type=AuditEventType.AUTOMATION_OBSERVATION)

    assert len(events) == 1
    details = events[0]["details"]
    serialized = json.dumps(details, ensure_ascii=False)
    assert SENSITIVE_TITLE not in serialized
    assert SENSITIVE_PATH not in serialized
    assert "secret.exe" not in serialized
    assert "记事本" not in serialized

    assert details["foreground"]["title"] == {
        "len": len(SENSITIVE_TITLE),
        "sha256": hashlib.sha256(SENSITIVE_TITLE.encode("utf-8")).hexdigest(),
    }
    assert details["window_count"] == 2
    assert details["element_count"] == 2
    assert details["elements_supported"] is True
    assert details["foreground"]["bounds_present"] is True
    assert "bounds" not in details["foreground"]
    assert details["observation_digest"] == observation.digest()


def test_observation_audit_is_a_separate_event_type(tmp_path):
    audit, store = _audit(tmp_path)
    service = ObservationService(
        _observer(), policy=ObservationPolicy(enabled=True), audit=audit
    )
    service.observe(principal="liuhao", now=0.0)

    assert len(store.query_events(event_type=AuditEventType.AUTOMATION_OBSERVATION)) == 1
    assert store.query_events(event_type=AuditEventType.AUTOMATION_ACTION) == []


def test_audit_caps_detail_rows_but_keeps_true_counts(tmp_path):
    windows = [
        {
            "handle": index,
            "title": f"窗口-{index}",
            "process_name": "demo.exe",
            "pid": 900 + index,
            "foreground": index == 1,
        }
        for index in range(1, 26)
    ]
    observer = FakeWindowObserver(windows)
    audit, store = _audit(tmp_path)
    service = ObservationService(
        observer,
        policy=ObservationPolicy(enabled=True),
        audit=audit,
        max_windows=25,
    )
    service.observe(principal="liuhao", scope=TargetScope.SCREEN, now=0.0)

    details = store.query_events(
        event_type=AuditEventType.AUTOMATION_OBSERVATION
    )[0]["details"]
    assert details["window_count"] == 25
    assert len(details["windows"]) == 20
    assert details["windows_truncated_for_audit"] == 5


# ---------------------------------------------------------------- 工具与准入
def _tool(tools, name):
    return next(item for item in tools if item.name == name)


def test_tools_gate_on_human_principal_and_admin_switch(tmp_path):
    audit, _store = _audit(tmp_path)
    policy = ObservationPolicy(enabled=True)

    tools = build_observation_tools(
        "liuhao",
        metadata={"kind": "human"},
        policy=policy,
        observer=_observer(),
        audit=audit,
    )
    names = {item.name for item in tools}
    assert names == {
        "automation.observation.capture",
        "automation.observation.disable",
        "automation.observation.enable",
        "automation.observation.status",
    }
    assert all("execute" not in name and "click" not in name for name in names)

    assert (
        build_observation_tools(
            "agent-1",
            metadata={"kind": "agent"},
            policy=policy,
            observer=_observer(),
            audit=audit,
        )
        == []
    )

    admin_off = ObservationPolicy(env={OBSERVE_ENV: "0"})
    assert (
        build_observation_tools(
            "liuhao",
            metadata={"kind": "human"},
            policy=admin_off,
            observer=_observer(),
            audit=audit,
        )
        == []
    )


def test_tools_capture_disable_enable_flow(tmp_path):
    audit, _store = _audit(tmp_path)
    policy = ObservationPolicy(enabled=True)
    tools = build_observation_tools(
        "liuhao",
        metadata={"kind": "human"},
        policy=policy,
        observer=_observer(),
        audit=audit,
    )

    captured = json.loads(_tool(tools, "automation.observation.capture").fn())
    assert captured["status"] == "ok"
    # 回显给人类时允许标题明文。
    assert captured["observation"]["foreground"]["title"] == SENSITIVE_TITLE
    assert captured["observation"]["elements_supported"] is True

    disabled = json.loads(
        _tool(tools, "automation.observation.disable").fn(reason="privacy")
    )
    assert disabled["observation_policy"]["enabled"] is False

    blocked = json.loads(_tool(tools, "automation.observation.capture").fn())
    assert blocked["status"] == "error"
    assert blocked["error"] == "observation_disabled"

    enabled = json.loads(_tool(tools, "automation.observation.enable").fn())
    assert enabled["observation_policy"]["enabled"] is True
    assert json.loads(_tool(tools, "automation.observation.capture").fn())["status"] == "ok"


def test_capture_rejects_unknown_scope(tmp_path):
    audit, _store = _audit(tmp_path)
    tools = build_observation_tools(
        "liuhao",
        metadata={"kind": "human"},
        policy=ObservationPolicy(enabled=True),
        observer=_observer(),
        audit=audit,
    )
    payload = json.loads(
        _tool(tools, "automation.observation.capture").fn(scope="desktop")
    )
    assert payload["status"] == "error"
    assert payload["error"] == "invalid_scope"


# ---------------------------------------------------------------- Windows 观察器（注入后端）
class FakeWin32Backend:
    available = True
    unavailable_reason = ""

    def __init__(self, *, foreground: int = 1, children_error: bool = False) -> None:
        self.foreground = foreground
        self.children_error = children_error
        self.titles = {1: SENSITIVE_TITLE, 2: "记事本", 3: ""}
        self.rects = {
            1: (0, 0, 1920, 1080),
            2: (100, 100, 800, 600),
            3: (0, 0, 100, 100),
            11: (10, 20, 100, 40),
            12: (120, 20, 200, 40),
        }
        self.pids = {1: 1234, 2: 5678, 3: 3000}
        self.visible = {1: True, 2: True, 3: False, 11: True, 12: True}

    def enum_windows(self):
        return [1, 2, 3]

    def foreground_window(self):
        return self.foreground

    def window_title(self, handle):
        return self.titles.get(handle, "")

    def window_pid(self, handle):
        return self.pids[handle]

    def window_rect(self, handle):
        return self.rects[handle]

    def monitor_rect(self, handle):
        return (0, 0, 1920, 1080)

    def is_visible(self, handle):
        return self.visible.get(handle, False)

    def is_minimized(self, handle):
        return False

    def enum_children(self, handle):
        if self.children_error:
            raise PermissionError("denied")
        return [11, 12] if handle == 1 else []

    def class_name(self, handle):
        return {11: "Edit", 12: "Button"}.get(handle, "")

    def control_id(self, handle):
        return {11: 1001, 12: 1002}.get(handle)

    def is_enabled(self, handle):
        return True

    def process_info(self, pid):
        if pid == 1234:
            return "secret.exe", SENSITIVE_PATH, ""
        return "notepad.exe", r"C:\Windows\notepad.exe", ""


def test_windows_observer_builds_foreground_and_window_list():
    observer = WindowsObserver(backend=FakeWin32Backend())
    observation = observer.observe(TargetScope.SCREEN)

    assert observation.backend == "windows"
    assert observation.foreground is not None
    assert observation.foreground.handle == 1
    assert observation.foreground.title == SENSITIVE_TITLE
    assert observation.foreground.process_name == "secret.exe"
    assert observation.foreground.executable_path == SENSITIVE_PATH
    assert observation.foreground.fullscreen is True
    assert observation.foreground.bounds == (0, 0, 1920, 1080)
    assert observation.foreground.elements_supported is True
    assert observation.foreground.element_count == 2
    # 空标题的隐藏窗口被过滤，只剩两个真实窗口。
    assert [item.handle for item in observation.windows] == [1, 2]
    assert observation.elements[0].bounds == (10, 20, 100, 40)


def test_windows_observer_reports_unsupported_when_child_enumeration_fails():
    observer = WindowsObserver(backend=FakeWin32Backend(children_error=True))
    observation = observer.observe(TargetScope.WINDOW)

    assert observation.elements == ()
    assert observation.elements_supported is False
    assert observation.elements_reason.startswith(ELEMENTS_UNSUPPORTED_REASON)


def test_windows_observer_does_not_invent_foreground_when_unavailable():
    observer = WindowsObserver(backend=FakeWin32Backend(foreground=0))
    observation = observer.observe(TargetScope.WINDOW)

    assert observation.foreground is None
    assert observation.windows == ()
    assert observation.elements_reason == "没有前台窗口，无法读取元素"

    screen = observer.observe(TargetScope.SCREEN)
    assert screen.foreground is None
    assert [item.handle for item in screen.windows] == [1, 2]


def test_windows_observer_reports_missing_backend_dependency():
    class UnavailableBackend:
        available = False
        unavailable_reason = "pywin32_unavailable:ImportError"

    observer = WindowsObserver(backend=UnavailableBackend())
    with pytest.raises(AutomationError) as excinfo:
        observer.observe()
    assert excinfo.value.reason == "observation_backend_unavailable"
    assert "pywin32_unavailable" in str(excinfo.value)


def test_windows_observer_scope_window_only_returns_foreground():
    observer = WindowsObserver(backend=FakeWin32Backend())
    observation = observer.observe(TargetScope.WINDOW)
    assert [item.handle for item in observation.windows] == [1]


# ---------------------------------------------------------------- 真实冒烟（可跳过）
@pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="CI 无桌面环境，跳过真实窗口冒烟"
)
def test_windows_observer_real_smoke():
    observer = WindowsObserver()
    try:
        observation = observer.observe(TargetScope.WINDOW, include_elements=True)
    except AutomationError as exc:
        if exc.reason == "observation_backend_unavailable":
            pytest.skip(f"当前环境无窗口 API：{exc}")
        raise
    assert observation.backend == "windows"
    if observation.foreground is None:
        pytest.skip("当前会话没有前台窗口（无桌面环境）")
    assert observation.foreground.title is not None
    assert isinstance(observation.elements_supported, bool)


# ---------------------------------------------------------------- 内核工具表接线
_KERNEL_PRINCIPAL_BASE = "liuhao-kernel-observation"

_READ_ONLY_OBSERVATION_TOOLS = {
    "automation.observation.capture",
    "automation.observation.disable",
    "automation.observation.enable",
    "automation.observation.status",
}

#: 任何含这些片段的工具名/能力/参数都意味着能产生输入或点击，观察面一律拒绝。
_INPUT_SURFACE_TOKENS = (
    "click",
    "type",
    "input",
    "press",
    "key",
    "mouse",
    "move",
    "scroll",
    "drag",
    "execute",
    "send",
    "write",
)


def _reset_observation_policy(monkeypatch):
    """复位进程级观察策略单例，让本测试的 env/monkeypatch 真正生效。"""
    monkeypatch.setattr(observe_module, "_policy", None)


def _register_identity(principal, kind):
    """在身份内核里登记主体；kind=human 是观察工具的唯一放行类型。"""
    from src.kernels.identity import get_identity_manager

    return get_identity_manager().create_identity(
        principal, metadata={"kind": kind}
    )


def _observation_tool_names(tools):
    return [
        item.name
        for item in tools
        if item.name.startswith("automation.observation.")
    ]


def test_kernel_tools_register_no_observation_when_switch_off(monkeypatch):
    monkeypatch.setenv(OBSERVE_ENV, "0")
    _reset_observation_policy(monkeypatch)
    principal = f"{_KERNEL_PRINCIPAL_BASE}-off"
    _register_identity(principal, "human")

    assert _observation_tool_names(make_tools(principal)) == []


def test_kernel_tools_register_exactly_four_read_only_observation_tools(monkeypatch):
    monkeypatch.delenv(OBSERVE_ENV, raising=False)
    _reset_observation_policy(monkeypatch)
    principal = f"{_KERNEL_PRINCIPAL_BASE}-human"
    _register_identity(principal, "human")

    tools = make_tools(principal)
    observation_tools = [
        item
        for item in tools
        if item.name.startswith("automation.observation.")
    ]

    assert {item.name for item in observation_tools} == _READ_ONLY_OBSERVATION_TOOLS
    assert all(item.risk == "LOW" for item in observation_tools)
    for item in observation_tools:
        # 只看工具名 / 能力 / 参数名；schema 的 ``"type": "object"`` 是 JSON
        # Schema 元字段，不参与"是否有输入入口"的判断。
        parameter_names = " ".join((item.schema.get("properties") or {}).keys())
        surface = f"{item.name} {item.capability} {parameter_names}".lower()
        assert not any(token in surface for token in _INPUT_SURFACE_TOKENS), item.name


def test_kernel_tools_do_not_register_observation_for_non_human(monkeypatch):
    monkeypatch.delenv(OBSERVE_ENV, raising=False)
    _reset_observation_policy(monkeypatch)
    principal = f"{_KERNEL_PRINCIPAL_BASE}-agent"
    _register_identity(principal, "agent")

    assert _observation_tool_names(make_tools(principal)) == []
