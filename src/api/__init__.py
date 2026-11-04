"""API package for provider and runtime metrics.

Only ``src.api.routes`` is mounted by the running gateway (``src.gateway.main``
includes ``src.api.routes.knowledge``). The top-level ``admin_api`` /
``agents_api`` / ``tasks_api`` / ``memory_api`` / ``plugins_api`` / ``cost_api``
modules are LEGACY: they belong to the ``src/api/app.py`` host, are not reachable
from the gateway, and are kept only for ``tests/test_api_extended.py``. Do not
wire them into the gateway; use the ``src.gateway`` routers instead.
"""
