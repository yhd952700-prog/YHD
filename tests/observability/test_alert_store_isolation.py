"""告警 store 单例隔离的**双向证明**（F3 回归护栏）。

这个测试存在的理由
------------------
``tests/gateway/test_alerts_surface.py::test_alerts_honest_empty_when_no_failures``
断言 ``/v1/alerts`` 的 ``count == 0``。修复 F3 之前，这个断言**永远不可能失败**：
``src/observability/alerts/store.py`` 的 ``get_alert_store()`` 是模块级单例
``_default_store``，只在第一次调用时解析 ``LIUHAO_ALERTS_STORE_PATH``。只要会话里
任何一个先跑的测试构造过它，后续测试即便把路径指向 ``tmp_path``，读面与写面拿到的
仍是那份绑定到默认路径的 store —— 于是 ``count == 0`` 反映的是"我被隔离了"，
而不是"真的没有告警"。

一个无法失败的断言不是护栏，是装饰。所以这里显式证明它**已经有牙**：
写入一条真实告警后，``count`` 必须从 0 变成 1。

三段
----
1. ``test_redirection_is_honoured``  —— conftest 的隔离 fixture 真的把 store 绑到本用例
2. ``test_honest_empty_assertion_has_teeth`` —— 关键：``/v1/alerts`` 的 count 会随真实
   告警而变，证明该断言具备失败能力（修复前此处恒为 0）
3. ``test_reader_and_writer_share_one_store`` —— 写面写入的 id 能在读面看到，
   排除"读面另有一份"的分裂

以上全部经**产品真实代码路径**（``emit_alert`` -> ``AlertStore`` -> ``/v1/alerts``），
不使用 mock、不 stub 任何一环。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.observability.alerts.models import Alert, AlertSeverity, AlertType
from src.observability.alerts.store import get_alert_store
from src.security import get_jwt_handler


def _auth_headers() -> dict:
    token, _payload = get_jwt_handler().create_token(subject="f3-regression-human")
    return {"Authorization": f"Bearer {token}"}


def test_redirection_is_honoured():
    """本用例的告警 store 必须落在自己的 ``tmp_path``，且初始为空。

    反向对照：修复前这里拿到的会是绑定到默认路径的陈旧单例。
    """
    store = get_alert_store()
    resolved = Path(str(store.storage_path)).resolve()
    assert resolved.name == "alerts.json", resolved
    # tmp_path 一定带 pytest 的编号目录；默认路径是 data/observability/alerts.json
    assert resolved != Path("data/observability/alerts.json").resolve(), (
        "the alert store is still bound to the DEFAULT path -- the conftest "
        "isolation fixture (_isolate_alert_store) did not take effect"
    )
    assert store.list_alerts() == [], "a fresh isolated store must start empty"


def test_honest_empty_assertion_has_teeth():
    """关键护栏：``/v1/alerts`` 的 ``count`` 必须随真实告警变化。

    修复前：无论写入多少条，``count`` 恒为 0 → 诚实空态断言是假通过。
    修复后：写入一条 → ``count == 1``，该断言重新具备失败能力。
    """
    app = get_app()
    with TestClient(app) as client:
        headers = _auth_headers()
        before = client.get("/v1/alerts", headers=headers)
        assert before.status_code == 200, before.text
        assert before.json()["count"] == 0, before.json()

        get_alert_store().emit_alert(
            Alert(
                id="f3-regression-teeth",
                name="F3 regression teeth",
                severity=AlertSeverity.HIGH,
                alert_type=AlertType.CUSTOM,
                source="f3_regression",
                message="proving the honest-empty assertion can fail",
            ),
            dispatch=False,
        )

        after = client.get("/v1/alerts", headers=headers)
        assert after.status_code == 200, after.text
        assert after.json()["count"] == 1, (
            "the read surface still cannot see what the write surface produced -- "
            "the reader and the writer are looking at two different stores"
        )


def test_reader_and_writer_share_one_store():
    """写面落库的 id 必须在读面出现——排除读面另有一份的分裂。"""
    get_alert_store().emit_alert(
        Alert(
            id="f3-regression-shared",
            name="F3 regression shared",
            severity=AlertSeverity.HIGH,
            alert_type=AlertType.CUSTOM,
            source="f3_regression",
            message="the reader must see exactly what the writer persisted",
        ),
        dispatch=False,
    )
    app = get_app()
    with TestClient(app) as client:
        body = client.get("/v1/alerts", headers=_auth_headers()).json()
    ids = {a.get("id") for a in body["alerts"]}
    assert "f3-regression-shared" in ids, sorted(ids)


if __name__ == "__main__":  # pragma: no cover - manual proof entry point
    raise SystemExit(pytest.main([__file__, "-v"]))
