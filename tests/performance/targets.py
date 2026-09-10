"""
Example Benchmark Targets for LiuHao AI OS

Provides concrete implementations of BenchmarkTarget for common testing scenarios.
"""

import asyncio
import time
import random
from typing import Any, Dict, Optional
from .benchmark import BenchmarkRunner, BenchmarkTarget, BenchmarkConfig, create_constant_load_benchmark


class MockAPITarget(BenchmarkTarget):
    """
    Mock API target for benchmarking.
    
    Simulates an API endpoint with configurable latency and error rate.
    """
    
    def __init__(
        self,
        base_latency: float = 0.05,      # 基础延迟 (秒)
        latency_variance: float = 0.02,  # 延迟方差
        error_rate: float = 0.0,         # 错误率 (0.0-1.0)
        timeout: float = 30.0,           # 超时时间
    ):
        self.base_latency = base_latency
        self.latency_variance = latency_variance
        self.error_rate = error_rate
        self.timeout = timeout
        self.request_count = 0
        self.error_count = 0
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute a mock API request."""
        self.request_count += 1
        
        # Simulate latency
        latency = self.base_latency + random.uniform(-self.latency_variance, self.latency_variance)
        latency = max(0.001, latency)  # Minimum 1ms
        await asyncio.sleep(latency)
        
        # Simulate errors
        if random.random() < self.error_rate:
            self.error_count += 1
            raise Exception("Simulated API error")
        
        return {
            "status": "ok",
            "request_id": self.request_count,
            "latency_ms": latency * 1000,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get execution statistics."""
        return {
            "total_requests": self.request_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / max(1, self.request_count),
        }


class CacheTarget(BenchmarkTarget):
    """
    Cache benchmark target.
    
    Tests cache performance (get/put operations).
    """
    
    def __init__(
        self,
        cache_backend,
        read_ratio: float = 0.8,  # 读操作比例
    ):
        self.cache = cache_backend
        self.read_ratio = read_ratio
        self.operations = 0
        self.hits = 0
        self.misses = 0
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute a cache operation (get or put)."""
        self.operations += 1
        key = f"key_{random.randint(1, 1000)}"
        
        if random.random() < self.read_ratio:
            # Read operation
            result = self.cache.get(key)
            if result is not None:
                self.hits += 1
                return {"operation": "get", "hit": True, "key": key}
            else:
                self.misses += 1
                return {"operation": "get", "hit": False, "key": key}
        else:
            # Write operation
            self.cache.put(key, f"value_{self.operations}")
            return {"operation": "put", "key": key}
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self.hits + self.misses
        return {
            "total_operations": self.operations,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / max(1, total),
        }


class DatabaseTarget(BenchmarkTarget):
    """
    Database benchmark target.
    
    Simulates database operations with configurable latency.
    """
    
    def __init__(
        self,
        query_latency: float = 0.01,      # 查询延迟
        write_latency: float = 0.02,      # 写入延迟
        read_ratio: float = 0.7,          # 读操作比例
    ):
        self.query_latency = query_latency
        self.write_latency = write_latency
        self.read_ratio = read_ratio
        self.query_count = 0
        self.write_count = 0
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute a database operation."""
        if random.random() < self.read_ratio:
            # Read query
            await asyncio.sleep(self.query_latency + random.uniform(-0.002, 0.002))
            self.query_count += 1
            return {"operation": "select", "rows": random.randint(1, 100)}
        else:
            # Write query
            await asyncio.sleep(self.write_latency + random.uniform(-0.005, 0.005))
            self.write_count += 1
            return {"operation": "insert", "rows_affected": 1}
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "queries": self.query_count,
            "writes": self.write_count,
            "total": self.query_count + self.write_count,
        }


# ==================== Predefined Benchmark Configurations ====================


def get_standard_benchmarks() -> Dict[str, BenchmarkConfig]:
    """Get standard benchmark configurations for common scenarios."""
    
    return {
        "api_smoke": create_constant_load_benchmark(
            name="api_smoke_test",
            target_rps=10.0,
            duration=30.0,
            description="Quick API smoke test",
        ),
        
        "api_baseline": create_constant_load_benchmark(
            name="api_baseline",
            target_rps=100.0,
            duration=60.0,
            description="Baseline API performance test",
        ),
        
        "api_load": create_constant_load_benchmark(
            name="api_load_test",
            target_rps=500.0,
            duration=120.0,
            description="High load API test",
        ),
        
        "api_stress": create_constant_load_benchmark(
            name="api_stress_test",
            target_rps=1000.0,
            duration=60.0,
            description="Stress test to find breaking point",
        ),
        
        "api_ramp": BenchmarkConfig(
            benchmark_type=BenchmarkConfig.BenchmarkType.THROUGHPUT,
            name="api_ramp_test",
            load_pattern="ramp",
            start_rps=10.0,
            target_rps=500.0,
            ramp_duration=60.0,
            duration=120.0,
            description="Ramp up load to find capacity",
        ),
        
        "api_spike": BenchmarkConfig(
            benchmark_type=BenchmarkConfig.BenchmarkType.SPIKE,
            name="api_spike_test",
            load_pattern="spike",
            target_rps=100.0,
            spike_rps=1000.0,
            spike_duration=10.0,
            spike_interval=30.0,
            duration=180.0,
            description="Spike load test for resilience",
        ),
        
        "api_soak": BenchmarkConfig(
            benchmark_type=BenchmarkConfig.BenchmarkType.SOAK,
            name="api_soak_test",
            load_pattern="constant",
            target_rps=100.0,
            duration=3600.0,  # 1 hour
            description="Long-running stability test",
        ),
        
        "cache_perf": create_constant_load_benchmark(
            name="cache_performance",
            target_rps=5000.0,
            duration=60.0,
            description="Cache performance test",
        ),
        
        "db_baseline": create_constant_load_benchmark(
            name="database_baseline",
            target_rps=200.0,
            duration=60.0,
            description="Database baseline performance",
        ),
    }


# ==================== Convenience Runner Functions ====================


async def run_api_benchmark(
    target_rps: float = 100.0,
    duration: float = 60.0,
    base_latency: float = 0.05,
    error_rate: float = 0.0,
) -> Any:
    """Run a standard API benchmark."""
    from .benchmark import run_benchmark, BenchmarkRunner, create_constant_load_benchmark
    
    target = MockAPITarget(
        base_latency=base_latency,
        error_rate=error_rate,
    )
    
    config = create_constant_load_benchmark(
        name="api_benchmark",
        target_rps=target_rps,
        duration=duration,
    )
    
    runner = BenchmarkRunner(config)
    return await runner.run(target)


async def run_cache_benchmark(
    cache_backend,
    target_rps: float = 5000.0,
    duration: float = 60.0,
    read_ratio: float = 0.8,
) -> Any:
    """Run a cache benchmark."""
    from .benchmark import BenchmarkRunner, create_constant_load_benchmark
    
    target = CacheTarget(cache_backend, read_ratio)
    
    config = create_constant_load_benchmark(
        name="cache_benchmark",
        target_rps=target_rps,
        duration=duration,
    )
    
    runner = BenchmarkRunner(config)
    return await runner.run(target)


async def run_database_benchmark(
    target_rps: float = 200.0,
    duration: float = 60.0,
    query_latency: float = 0.01,
) -> Any:
    """Run a database benchmark."""
    from .benchmark import BenchmarkRunner, create_constant_load_benchmark
    
    target = DatabaseTarget(query_latency=query_latency)
    
    config = create_constant_load_benchmark(
        name="database_benchmark",
        target_rps=target_rps,
        duration=duration,
    )
    
    runner = BenchmarkRunner(config)
    return await runner.run(target)


# ==================== CLI Helper ====================


def main():
    """Command line interface for running benchmarks."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Run performance benchmarks")
    parser.add_argument(
        "benchmark",
        choices=list(get_standard_benchmarks().keys()) + ["custom"],
        help="Benchmark to run",
    )
    parser.add_argument("--target-rps", type=float, default=100.0)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--latency", type=float, default=0.05)
    parser.add_argument("--error-rate", type=float, default=0.0)
    parser.add_argument("--output", type=str, default="tests/output/benchmarks")
    
    args = parser.parse_args()
    
    async def run():
        benchmarks = get_standard_benchmarks()
        
        if args.benchmark == "custom":
            config = BenchmarkConfig(
                name="custom_benchmark",
                load_pattern="constant",
                target_rps=args.target_rps,
                duration=args.duration,
                output_dir=args.output,
            )
        else:
            config = benchmarks[args.benchmark]
            config.output_dir = args.output
        
        target = MockAPITarget(
            base_latency=args.latency,
            error_rate=args.error_rate,
        )
        
        runner = BenchmarkRunner(config)
        result = await runner.run(target)
        
        print(f"\n=== Benchmark Results ===")
        print(f"Total Requests: {result.total_requests}")
        print(f"Successful: {result.successful_requests}")
        print(f"Failed: {result.failed_requests}")
        print(f"Success Rate: {result.success_rate:.2%}")
        print(f"Avg RPS: {result.avg_rps:.2f}")
        print(f"Avg Latency: {result.avg_latency*1000:.2f} ms")
        print(f"P50 Latency: {result.p50_latency*1000:.2f} ms")
        print(f"P95 Latency: {result.p95_latency*1000:.2f} ms")
        print(f"P99 Latency: {result.p99_latency*1000:.2f} ms")
        
        # Analyze bottlenecks
        from .benchmark import PerformanceAnalyzer
        bottlenecks = PerformanceAnalyzer.detect_bottlenecks(result)
        if bottlenecks:
            print(f"\n=== Bottlenecks Detected ===")
            for b in bottlenecks:
                print(f"  [{b['severity'].upper()}] {b['type']}: {b['message']}")
                print(f"    Recommendation: {b['recommendation']}")
    
    asyncio.run(run())


if __name__ == "__main__":
    main()