#!/usr/bin/env python3
"""CI Observability Check Script.

Validates that the observability framework is properly configured
and all components are functional after code changes.

Exit codes: 0 = every check ran and every required backend is available;
1 = a check really failed; 3 = NOT VERIFIED (a required export backend
(langfuse / arize-phoenix) is unavailable, so the export path was not
exercised). The CI observability job installs both backends, so 3 there means
"the evidence we planned to collect is missing" -- never a green pass.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.adapters.observability.observability_adapter import (
    ObservabilityConfig,
    setup_observability,
    get_config,
    set_config,
    increment_counter,
    observe_latency,
    export_to_langfuse,
    export_to_phoenix,
    LANGFUSE_AVAILABLE,
    PHOENIX_AVAILABLE,
)
from src.adapters.observability.metrics_helper import get_metrics


def main():
    print("=" * 60)
    print("LiuHao AI OS - CI Observability Validation")
    print("=" * 60)

    # 1. Test ObservabilityConfig
    print("\n1. Testing ObservabilityConfig...")
    try:
        config = ObservabilityConfig(
            enable_metrics=True,
            enable_tracing=True,
            enable_structured_logging=True
        )
        print("   OK ObservabilityConfig created successfully")
    except Exception as e:
        print(f"   FAIL ObservabilityConfig failed: {e}")
        return 1

    # 2. Test setup_observability
    print("\n2. Testing setup_observability()...")
    try:
        setup_observability(config)
        print("   OK setup_observability() executed successfully")
    except Exception as e:
        print(f"   FAIL setup_observability() failed: {e}")
        return 1

    # 3. Test get_config/set_config round-trip
    print("\n3. Testing get_config/set_config round-trip...")
    try:
        # Set config first
        config = ObservabilityConfig(
            enable_metrics=True,
            enable_tracing=True,
            enable_structured_logging=True
        )
        set_config(config)
        # Now retrieve
        retrieved = get_config()
        if retrieved and retrieved.enable_metrics and retrieved.enable_tracing:
            print("   OK get_config/set_config round-trip successful")
        else:
            print("   FAIL Config values not preserved")
            return 1
    except Exception as e:
        print(f"   FAIL get_config/set_config failed: {e}")
        return 1

    # 4. Test metrics functions
    print("\n4. Testing metrics functions...")
    try:
        increment_counter("ci.observability.validation")
        observe_latency("ci.validation.latency", 0.123)
        print("   OK increment_counter and observe_latency work")
    except Exception as e:
        print(f"   FAIL Metrics functions failed: {e}")
        return 1

    # 5. Test get_metrics
    print("\n5. Testing get_metrics()...")
    try:
        metrics_output = get_metrics()
        if len(metrics_output) > 100:
            print(f"   OK get_metrics() returned {len(metrics_output)} chars")
        else:
            print(f"   WARN get_metrics() returned only {len(metrics_output)} chars (may be incomplete)")
    except Exception as e:
        print(f"   FAIL get_metrics() failed: {e}")
        return 1

    # 6. Test export functions (graceful fallback)
    print("\n6. Testing export functions...")
    try:
        export_to_langfuse({"name": "test_trace", "trace_id": "test123"})
        print("   OK export_to_langfuse() executed (graceful fallback if deps missing)")
    except Exception as e:
        print(f"   FAIL export_to_langfuse() failed: {e}")
        return 1

    try:
        export_to_phoenix({"name": "test_trace", "trace_id": "test123"})
        print("   OK export_to_phoenix() executed (graceful fallback if deps missing)")
    except Exception as e:
        print(f"   FAIL export_to_phoenix() failed: {e}")
        return 1

    # 7. Summary -- a graceful fallback that silently skips the export is NOT
    # a pass: the backend being unavailable means the export path was never
    # exercised here.
    missing_backends = []
    if not LANGFUSE_AVAILABLE:
        missing_backends.append("langfuse")
    if not PHOENIX_AVAILABLE:
        missing_backends.append("arize-phoenix")
    print("\n" + "=" * 60)
    if missing_backends:
        print(
            "CI Observability Validation: NOT VERIFIED -- required backend(s) "
            "unavailable: %s" % ", ".join(missing_backends)
        )
        print("The export path was NOT exercised; a fallback is not evidence.")
        print("=" * 60)
        return 3
    print("CI Observability Validation: ALL CHECKS PASSED OK")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
