"""
Performance Testing Package for LiuHao AI OS

Provides:
- Benchmark framework for throughput, latency, and concurrency testing
- Load generators (constant, ramp, spike, step, soak)
- Performance analyzer (bottleneck detection, regression detection)
- Example benchmark targets
"""

from .benchmark import (
    BenchmarkType,
    LoadPattern,
    BenchmarkConfig,
    BenchmarkResult,
    BenchmarkTarget,
    LoadGenerator,
    BenchmarkRunner,
    PerformanceAnalyzer,
    create_constant_load_benchmark,
    create_ramp_benchmark,
    create_spike_benchmark,
    create_soak_benchmark,
    run_benchmark,
)

from .targets import (
    MockAPITarget,
    CacheTarget,
    DatabaseTarget,
    get_standard_benchmarks,
    run_api_benchmark,
    run_cache_benchmark,
    run_database_benchmark,
)

__all__ = [
    # Benchmark framework
    "BenchmarkType",
    "LoadPattern",
    "BenchmarkConfig",
    "BenchmarkResult",
    "BenchmarkTarget",
    "LoadGenerator",
    "BenchmarkRunner",
    "PerformanceAnalyzer",
    "create_constant_load_benchmark",
    "create_ramp_benchmark",
    "create_spike_benchmark",
    "create_soak_benchmark",
    "run_benchmark",
    
    # Example targets
    "MockAPITarget",
    "CacheTarget",
    "DatabaseTarget",
    "get_standard_benchmarks",
    "run_api_benchmark",
    "run_cache_benchmark",
    "run_database_benchmark",
]