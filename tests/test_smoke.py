"""最小冒烟：网关应用能真正启动、健康端点可用、受保护路由受闸门保护。

历史遗留的 ``assert True`` 零断言。这里换成真实请求断言，避免"通过但没验"。
"""


def test_smoke() -> None:
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200, health.text

        # 无令牌访问受保护路由必须是 401（而不是 404，说明路由真的挂上了）。
        guarded = client.get("/v1/kernels")
        assert guarded.status_code == 401, guarded.text
