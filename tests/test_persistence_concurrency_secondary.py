"""次优先级持久化点的并发回归（同一手法：锁 + 重读合并 + 原子替换）。

覆盖：RBAC 角色库、两套模型注册表、审计高水位（HWM）、插件状态、
可观测性存储。全部落在 ``tmp_path``。
"""

import json
import os
import threading

from src.model_gateway.model_registry import ModelRegistry as GatewayModelRegistry
from src.model_gateway.model_registry import RegisteredModel
from src.models.registry import ModelRegistry
from src.observability.models import Span
from src.observability.store import ObservabilityStore
from src.plugins.base import PluginMetadata
from src.plugins.manager import PluginManager
from src.security.audit_logger import CryptoAuditLogger
from src.security.audit_policy import AuditKernel
from src.security.rbac import RBACManager


def _run_threads(targets):
    barrier = threading.Barrier(len(targets))
    errors = []
    errors_lock = threading.Lock()

    def wrap(fn, label):
        def runner():
            try:
                barrier.wait(timeout=30)
                fn()
            except Exception as exc:  # noqa: BLE001
                with errors_lock:
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return runner

    threads = [threading.Thread(target=wrap(fn, label)) for fn, label in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return errors


# ---------------------------------------------------------------------------
# RBAC：多实例并发创建角色
# ---------------------------------------------------------------------------


def test_rbac_concurrent_role_create_keeps_all(tmp_path):
    path = str(tmp_path / "rbac.json")

    def create(index):
        def run():
            RBACManager(store_path=path).create_role(name=f"role-{index}")
        return run

    errors = _run_threads([(create(i), f"role-{i}") for i in range(6)])
    assert errors == [], f"并发创建角色出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data["roles"]) == 6
    assert {r["name"] for r in data["roles"]} == {f"role-{i}" for i in range(6)}


# ---------------------------------------------------------------------------
# 模型注册表（src/models 与 src/model_gateway 两套）
# ---------------------------------------------------------------------------


def test_models_registry_concurrent_register_keeps_all(tmp_path):
    path = str(tmp_path / "models.json")

    def register(index):
        def run():
            ModelRegistry(storage_path=path).register_model(
                name=f"model-{index}", model_type="llm", version=f"v{index}"
            )
        return run

    errors = _run_threads([(register(i), f"model-{i}") for i in range(6)])
    assert errors == [], f"并发注册模型出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    names = {item["name"] for item in data["models"]}
    # 注册表会额外注入 model_pricing.json 里的定价条目，这里只断言并发注册
    # 的 6 个模型一个不少（且没有被后写者覆盖）。
    assert {f"model-{i}" for i in range(6)} <= names


def test_gateway_model_registry_concurrent_persist_keeps_all(tmp_path):
    path = str(tmp_path / "gateway_models.json")

    def register(index):
        def run():
            registry = GatewayModelRegistry(storage_path=path)
            registry.register(
                RegisteredModel(
                    model_id=f"gw-{index}",
                    provider="openai",
                    model_name=f"gw-model-{index}",
                )
            )
            registry.persist()
        return run

    errors = _run_threads([(register(i), f"gw-{i}") for i in range(6)])
    assert errors == [], f"并发持久化模型出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    models = data["models"]
    if isinstance(models, dict):
        models = list(models.values())
    assert {f"gw-{i}" for i in range(6)} <= {
        item["model_id"] for item in models
    }


# ---------------------------------------------------------------------------
# 审计高水位：单调递增，不能写小
# ---------------------------------------------------------------------------


def test_audit_hwm_is_monotonic_under_concurrency(tmp_path):
    logger_path = str(tmp_path / "audit_hwm.json")
    policy_path = str(tmp_path / "policy_hwm.json")

    def bump(index):
        def run():
            audit = CryptoAuditLogger(hwm_path=logger_path)
            audit._entries_ever_written = index * 10
            audit._save_hwm()

            policy = AuditKernel(hwm_path=policy_path)
            policy._entries_ever_written = index * 10
            policy._save_hwm()
        return run

    errors = _run_threads([(bump(i), f"hwm-{i}") for i in range(6)])
    assert errors == [], f"并发写 HWM 出现异常：{errors[:5]}"

    for path in (logger_path, policy_path):
        with open(path, "r", encoding="utf-8") as fh:
            assert json.load(fh)["entries_ever_written"] == 50


# ---------------------------------------------------------------------------
# 插件状态
# ---------------------------------------------------------------------------


def test_plugin_state_concurrent_save_keeps_all(tmp_path):
    plugins_dir = str(tmp_path / "plugins")

    def save(index):
        def run():
            manager = PluginManager(plugins_dir=plugins_dir)
            name = f"plugin-{index}"
            manager._metadata[name] = PluginMetadata(
                name=name, version="1.0.0", description=""
            )
            manager._save_state()
        return run

    errors = _run_threads([(save(i), f"plugin-{i}") for i in range(6)])
    assert errors == [], f"并发保存插件状态出现异常：{errors[:5]}"

    with open(os.path.join(plugins_dir, "plugin_state.json"), "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert set(data["metadata"]) == {f"plugin-{i}" for i in range(6)}


# ---------------------------------------------------------------------------
# 可观测性存储
# ---------------------------------------------------------------------------


def test_observability_concurrent_emit_keeps_all_spans(tmp_path):
    path = str(tmp_path / "spans.json")

    def emit(index):
        def run():
            store = ObservabilityStore(storage_path=path)
            store.emit_span(
                Span(
                    span_id=f"span-{index}",
                    trace_id=f"trace-{index}",
                    name=f"span-{index}",
                )
            )
        return run

    errors = _run_threads([(emit(i), f"span-{i}") for i in range(5)])
    assert errors == [], f"并发写 span 出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data["spans"]) == 5
