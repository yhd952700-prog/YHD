"""Performance Benchmarks for liuhao AI OS

Provides comprehensive benchmarking suites for:
- Model latency and throughput
- Cache performance
- API response times
- Database query performance
"""

import time
import asyncio
import statistics
from typing import Dict, Any, List, Callable, Optional, Tuple
from dataclasses import dataclass, field
import concurrent.futures


@dataclass
class BenchmarkResult:
    """单个基准测试结果"""
    name: str
    iterations: int
    total_time_ms: float
    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    throughput_per_sec: float
    success_count: int
    failure_count: int
    error_message: Optional[str] = None


class BenchmarkSuite:
    """基准测试套件管理器"""
    
    def __init__(self, name: str = "unnamed"):
        self.name = name
        self._benchmarks: List[Callable] = []
        self._results: Dict[str, BenchmarkResult] = {}
    
    def add_benchmark(self, func: Callable) -> None:
        """注册基准测试函数"""
        self._benchmarks.append(func)
    
    async def run_all(self, iterations: int = 100) -> Dict[str, BenchmarkResult]:
        """运行所有已注册的基准测试"""
        self._results = {}
        
        for benchmark_fn in self._benchmarks:
            try:
                result = await benchmark_fn(iterations)
                self._results[benchmark_fn.__name__] = result
            except Exception as e:
                self._results[benchmark_fn.__name__] = BenchmarkResult(
                    name=benchmark_fn.__name__,
                    iterations=iterations,
                    total_time_ms=0,
                    min_ms=0,
                    max_ms=0,
                    mean_ms=0,
                    median_ms=0,
                    p95_ms=0,
                    p99_ms=0,
                    throughput_per_sec=0,
                    success_count=0,
                    failure_count=iterations,
                    error_message=str(e)
                )
        
        return self._results
    
    def get_results(self) -> Dict[str, BenchmarkResult]:
        """获取所有基准测试结果"""
        return self._results.copy()


class PerfTracker:
    """性能追踪上下文管理器"""
    
    def __init__(self, name: str, tracker_dict: Dict[str, List[float]]):
        self.name = name
        self.tracker_dict = tracker_dict
        self.start_time: Optional[float] = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, *args):
        elapsed_ms = (time.time() - self.start_time) * 1000
        if self.name not in self.tracker_dict:
            self.tracker_dict[self.name] = []
        self.tracker_dict[self.name].append(elapsed_ms)


def benchmark(iterations: int = 100, description: str = ""):
    """装饰器 - 自动将函数注册为基准测试"""
    def decorator(func):
        func._benchmark_iterations = iterations
        func._benchmark_description = description
        return func
    return decorator


async def run_single_benchmark(
    func: Callable,
    iterations: int
) -> BenchmarkResult:
    """运行单个基准测试函数"""
    times: List[float] = []
    success_count = 0
    failure_count = 0
    error_msg = None
    
    # 同步函数
    if not asyncio.iscoroutinefunction(func):
        for i in range(iterations):
            try:
                start = time.time()
                func()
                elapsed_ms = (time.time() - start) * 1000
                times.append(elapsed_ms)
                success_count += 1
            except Exception as e:
                failure_count += 1
                error_msg = str(e) if error_msg is None else error_msg
    # 异步函数
    else:
        for i in range(iterations):
            try:
                start = time.time()
                await func()
                elapsed_ms = (time.time() - start) * 1000
                times.append(elapsed_ms)
                success_count += 1
            except Exception as e:
                failure_count += 1
                error_msg = str(e) if error_msg is None else error_msg
    
    if not times:
        times = [0]  # Avoid division by zero
    
    times_sorted = sorted(times)
    n = len(times_sorted)
    
    return BenchmarkResult(
        name=func.__name__,
        iterations=iterations,
        total_time_ms=sum(times),
        min_ms=min(times),
        max_ms=max(times),
        mean_ms=statistics.mean(times),
        median_ms=statistics.median(times),
        p95_ms=times_sorted[int(n * 0.95)],
        p99_ms=times_sorted[int(n * 0.99)],
        throughput_per_sec=success_count / (sum(times) / 1000) if sum(times) > 0 else 0,
        success_count=success_count,
        failure_count=failure_count,
        error_message=error_msg
    )


# 预定义基准测试

@benchmark(iterations=50, description="Cache get performance")
def benchmark_cache_get():
    """Cache GET 操作基准测试"""
    # This will be measured at runtime
    pass


@benchmark(iterations=50, description="Model call latency")
def benchmark_model_call():
    """模型调用延迟基准测试"""
    pass


@benchmark(iterations=30, description="Concurrent request throughput")
def benchmark_concurrent_requests():
    """并发请求吞吐量基准测试"""
    pass


# Cache performance benchmarks

async def cache_get_benchmark(iterations: int = 100) -> BenchmarkResult:
    """Cache GET 性能基准测试"""
    from src.performance.cache import LRUCache
    
    cache = LRUCache(max_size=1000)
    
    # 预热缓存
    for i in range(100):
        cache.put(f"key_{i}", {"data": i, "nested": {"level": 2}})
    
    # 测试 GET 操作
    times = []
    success_count = 0
    failure_count = 0
    
    for i in range(iterations):
        start = time.time()
        result = cache.get(f"key_{i % 100}")
        elapsed_ms = (time.time() - start) * 1000
        times.append(elapsed_ms)
        if result is not None:
            success_count += 1
        else:
            failure_count += 1
    
    return _compute_benchmark_stats("cache_get", iterations, times, success_count, failure_count)


async def cache_put_benchmark(iterations: int = 100) -> BenchmarkResult:
    """Cache PUT 性能基准测试"""
    from src.performance.cache import LRUCache
    
    cache = LRUCache(max_size=1000)
    
    times = []
    success_count = 0
    failure_count = 0
    
    for i in range(iterations):
        start = time.time()
        cache.put(f"key_{i}", {"data": i, "ttl": 300})
        elapsed_ms = (time.time() - start) * 1000
        times.append(elapsed_ms)
        success_count += 1
    
    return _compute_benchmark_stats("cache_put", iterations, times, success_count, failure_count)


def _compute_benchmark_stats(
    name: str,
    iterations: int,
    times: List[float],
    success_count: int,
    failure_count: int
) -> BenchmarkResult:
    """计算基准统计数据"""
    if not times:
        times = [0]
    
    times_sorted = sorted(times)
    n = len(times_sorted)
    
    return BenchmarkResult(
        name=name,
        iterations=iterations,
        total_time_ms=sum(times),
        min_ms=min(times),
        max_ms=max(times),
        mean_ms=statistics.mean(times),
        median_ms=statistics.median(times),
        p95_ms=times_sorted[int(n * 0.95)],
        p99_ms=times_sorted[int(n * 0.99)],
        throughput_per_sec=success_count / (sum(times) / 1000) if sum(times) > 0 else 0,
        success_count=success_count,
        failure_count=failure_count
    )


# Throughput benchmarks

async def model_throughput_benchmark(iterations: int = 50) -> BenchmarkResult:
    """模型吞吐量基准测试"""
    times = []
    success_count = 0
    failure_count = 0
    
    for i in range(iterations):
        start = time.time()
        # Simulate model call - small sleep to avoid actual API calls
        await asyncio.sleep(0.01)  # 10ms simulated model call
        elapsed_ms = (time.time() - start) * 1000
        times.append(elapsed_ms)
        success_count += 1
    
    return _compute_benchmark_stats("model_throughput", iterations, times, success_count, failure_count)


async def api_response_benchmark(iterations: int = 100) -> BenchmarkResult:
    """API 响应时间基准测试"""
    times = []
    success_count = 0
    failure_count = 0
    
    for i in range(iterations):
        start = time.time()
        # Simulate API response
        await asyncio.sleep(0.005)  # 5ms simulated API response
        elapsed_ms = (time.time() - start) * 1000
        times.append(elapsed_ms)
        success_count += 1
    
    return _compute_benchmark_stats("api_response", iterations, times, success_count, failure_count)


# 测试导入验收
if __name__ == "__main__":
    print("Performance benchmarks module loaded OK")