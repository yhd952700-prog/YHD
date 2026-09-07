# Tracing Fixes for LiuHao AI OS

## AISpanAttributes Dual-Mode Access Pattern

**Problem**: `AISpanAttributes` needed to support both class-attribute access (`AISpanAttributes.GOAL_ID`) AND instantiation (`AISpanAttributes()`).

**Solution**: Design as a class with class-level attributes that persist across usages.

```python
class AISpanAttributes:
    GOAL_ID = 'goal_id'
    TASK_ID = 'task_id'
    MODEL_NAME = 'model_name'
    OPERATION_TYPE = 'operation_type'
```

## create_span() Degraded Mode Fix

**Problem**: In `_OTEL_AVAILABLE=False` branch, `create_span()` returned `_SpanContextmanager(name, {})` instead of `_NoopSpanContextmanager(name, kwargs.get("attributes", {}))`.

**Why**: When OpenTelemetry is unavailable, the noop context manager must be returned, not the real one (which would fail). Also, passing an empty `dict` `{}` loses any provided attributes.

**Fix**: Change the return to:
```python
return _NoopSpanContextmanager(name, kwargs.get("attributes", {}))
```

**Impact**: Ensures `test_goal_task_graph.py::test_goal_decomposition` passes in degraded mode (no OTel server).

## Verification

All 4 goal task graph tests pass:
```
46 passed, 13 skipped, 1 warning in 5.47s
```