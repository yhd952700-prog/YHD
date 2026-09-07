---
name: systematic-code-development
description: "Systematic code development workflow for LiuHao AI OS phases. Follow 4-phase review + D:\\ drive placement + Chinese responses."
category: development
---

# Systematic Code Development for LiuHao AI OS

## Always-On Rules

1. **D:\\ drive constraint**: All files must be placed on D:\\ drive. Construct paths using POSIX-style forward slashes for native tools. Primary delivery goes to `D:\LiuHao-AI-OS`.

2. **4-phase review cycle**: Every code change follows: (1) Understand existing code, (2) Review against blueprints, (3) Propose modifications, (4) Refine with verification.

3. **Chinese responses**: All assistant responses to user must be in Chinese. User explicitly stated '以后用中文回答我'.

4. **Test-first approach**: Write/tests before or alongside code changes. Use `pytest` with `--ignore` for phase-segregated testing. Target 100% pass on relevant test tree.

5. **Skill-before-code**: Load `hermes-agent` skill via `skill_view(name='hermes-agent')` before configuring, modifying, or troubleshooting Hermes. Do not guess or invent workarounds.

6. **No fabricated output**: Report real execution results. If a tool fails, say so directly. Never substitute plausible-looking fabricated output for results you couldn't actually produce.

7. **Match length to ask**: One-line question gets one-line answer; finished work gets short report of what changed, what's verified, what's left. Never a replay of the process. No filler ("Great question," "I'd be happy to"), no restating the request, no re-summarizing what you already said, no narrating tool calls the user can see.

## Pitfalls & Fixes

### Pitfall: Widening helper signatures without checking test tree
**Rule**: Grep the test tree for the SYMBOL before widening a helper signature — hand-rolled mocks reimplement the old shape and fail on a shard you did not run.

**Why**: In this session, widening `add_audit_event` signature to accept `str` without checking existing test calls caused `AttributeError: 'str' object has no attribute 'value'`. The test was using `AuditSeverity.LOW` enum, and the code path expected `.value` access.

**Fix**: Always check existing test calls for the symbol before modifying helper signatures. Accept both `str` and `AuditSeverity` enum using `isinstance(severity, str)` guard.

### Pitfall: Import path mismatches between test and source
**Rule**: When tests fail with import errors, verify the exact Python path structure. Use `sys.path.insert(0, 'src')` pattern consistently. Module structures like `src/sre/scaling/` require `PYTHONPATH=/d/LiuHao-AI-OS/src` for imports to resolve.

**Why**: Test collection failures occurred because `from src.xxx.yyy` paths didn't resolve until `PYTHONPATH` was set correctly or `sys.path` was manually managed in test files.

**Fix**: Always add `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))` at test file top, or ensure `PYTHONPATH=/d/LiuHao-AI-OS/src` is set before running pytest.

### Pitfall: Enum vs string mismatch in function parameters
**Rule**: Function parameters accepting enums should also accept strings via `isinstance(severity, str)` guard, OR tests should always pass the enum type. Mixed usage causes `AttributeError: 'str' object has no attribute 'value'`.

**Why**: test_security_audit_console used `severity="low"` (string) while the function signature expected `AuditSeverity` enum with `.value` access.

**Fix**: Design functions to accept both types, or standardize on one type across code and tests.

### Pitfall: Provider Adapter model registry mismatch

**Rule**: Model Registry entries must stay synchronized with Provider Adapter registered models — every model registered in the Model Registry must have a corresponding Provider Adapter entry, and vice versa. Mismatched entries cause routing failures and silent model fallback to incorrect providers.

**Why**: In Phase 4, the Provider Adapter and Model Registry are separate components. When a model was registered in one but not the other, the Model Router would fail to find it, falling back to a default or erroring. The registration pipeline must update both atomically.

**Fix**: The registration function must call both `model_registry.register()` and `provider_adapter.register_model()` in a single transaction. If either fails, roll back both. After any provider change, sync the registry via `registry.sync_from_adapter()`.

### Pitfall: Model Router stale cache

**Rule**: Model Router caches model lookups but must invalidate cache on registry updates. A stale cache entry can route requests to a disabled or deprecated model.

**Why**: Model status changes (activate/deactivate/deprecate) update the registry, but if the Router's in-memory cache is not cleared or refreshed, subsequent route lookups will return the old model reference.

**Fix**: Model Router maintains a `cache_ttl` and refreshes from registry on every N lookups. On any `registry.update_status()` or `registry.register()`, call `router.invalidate_cache()`.

### Pitfall: AISpanAttributes dual-mode access pattern (preserved)

**Rule**: When `AISpanAttributes` needs to support both `AISpanAttributes.GOAL_ID` class-attribute access AND `AISpanAttributes()` instantiation, design it as a class with instance attributes that can also be accessed as class attributes. In degraded mode, ensure `create_span()` returns `_NoopSpanContextmanager` (not `_SpanContextmanager`) when OpenTelemetry is unavailable.

**Why**: In this session, `AISpanAttributes` was initially designed as a plain function, then patched to a class, then patched again to dual-mode (class-accessible attributes + callable instance) to satisfy `goal_task_graph.py` test expectations. The `create_span()` return in the `_OTEL_AVAILABLE=False` branch was returning `_SpanContextmanager(name, {})` instead of `_NoopSpanContextmanager(name, kwargs.get("attributes", {}))`, causing test failures when tracing was unavailable.

**Fix**: Design `AISpanAttributes` as a class with class-level attributes. In `create_span()`, return `_NoopSpanContextmanager` in degraded mode, passing the attributes dict. Always verify both class-attribute access and function-call patterns work before marking a tracing module complete.

## Support Files (references/)

- `references/phase-workflows.md` — Phase-by-phase implementation checklist
- `references/error-patterns.md` — Common error patterns and fixes from this session
- `references/api-endpoints.md` — Any external API endpoint specifications

## Templates (templates/)

- `templates/test_template.py` — Boilerplate test file structure for new phases
- `templates/module_template.py` — Boilerplate module skeleton for new feature areas

## Scripts (scripts/)

- `scripts/verify-imports.py` — Script to verify all module imports resolve correctly
- `scripts/check-phase-completion.py` — Script to check which phases have 100% test pass

## Workflow Example

```bash
# 1. Understand existing code
cd /d/LiuHao-AI-OS
ls src/sre/  # Check current state

# 2. Create new module directory
mkdir -p src/sre/scaling src/sre/disaster src/cost

# 3. Write module file
write_file content=new_module.py

# 4. Write test file
write_file path=tests/xxx.py content=test_new_module.py

# 5. Run tests with correct path
PYTHONPATH=/d/LiuHao-AI-OS/src python -m pytest tests/xxx.py -v

# 6. Fix any import/syntax errors iteratively
# 7. Verify 100% pass before marking complete
```