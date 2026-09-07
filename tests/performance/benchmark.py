"""
Performance Benchmark Framework for LiuHao AI OS

Provides:
- Benchmark runner for throughput, latency, and concurrency testing
- Load generators (constant, ramp, spike)
- Performance analyzer (bottleneck detection, flame graph data)
- Benchmark report generation
- Regression detection
"""

import asyncio
import time
import statistics
import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum, auto
from concurrent.futures import ThreadPoolExecutor
import threading
from collections import defaultdict
import sys
from datetime import datetime


class BenchmarkType(Enum):
    """Benchmark type enumeration."""
    THROUGHPUT = auto()      # 吞吐量测试
    LATENCY = auto()         # 延迟测试
    CONCURRENCY = auto()     # 并发测试
    STRESS = auto()          # 压力测试
    SPIKE = auto()           # 脉冲负载测试
    SOAK = auto()            # 稳定性/浸泡测试
    REGRESSION = auto()      # 回归检测


class LoadPattern(Enum):
    """Load pattern for benchmarks."""
    CONSTANT = "constant"        # 恒定负载
    RAMP = "ramp"                # 阶梯/渐增负载
    SPIKE = "spike"              # 脉冲负载
    STEP = "step"                # 步进负载


@dataclass
class BenchmarkConfig:
    """Benchmark configuration."""
    benchmark_type: BenchmarkType
    name: str
    description: str = ""
    
    # Load configuration
    load_pattern: LoadPattern = LoadPattern.CONSTANT
    target_rps: float = 100.0          # 目标 RPS (requests per second)
    duration: float = 60.0             # 测试持续时间 (秒)
    concurrency: int = 10              # 并发数
    
    # Ramp/step configuration
    start_rps: float = 10.0            # 起始 RPS (for ramp/step)
    ramp_duration: float = 30.0        # 渐增时间 (秒)
    step_size: float = 20.0            # 步进大小
    step_duration: float = 15.0        # 每步持续时间
    
    # Spike configuration
    spike_rps: float = 500.0           # 脉冲峰值 RPS
    spike_duration: float = 5.0        # 脉冲持续时间
    spike_interval: float = 30.0       # 脉冲间隔
    
    # Common settings
    warmup_duration: float = 5.0       # 预热时间
    cooldown_duration: float = 5.0     # 冷却时间
    timeout: float = 30.0              # 单个请求超时
    
    # Output
    output_dir: str = "tests/output/benchmarks"
    generate_report: bool = True
    report_formats: List[str] = field(default_factory=lambda: ["json", "html"])


@dataclass
class BenchmarkResult:
    """Benchmark result data."""
    config: BenchmarkConfig
    start_time: float
    end_time: float
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    latencies: List[float] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    throughput_history: List[Tuple[float, float]] = field(default_factory=list)  # (timestamp, rps)
    
    @property
    def duration(self) -> float:
        return self.end_time - self.start_time
    
    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests
    
    @property
    def avg_rps(self) -> float:
        if self.duration == 0:
            return 0.0
        return self.total_requests / self.duration
    
    @property
    def avg_latency(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.mean(self.latencies)
    
    @property
    def p50_latency(self) -> float:
        if not self.latencies:
            return 0.0
        return statistics.median(self.latencies)
    
    @property
    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        index = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[index]
    
    @property
    def p99_latency(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        index = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[index]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "config": asdict(self.config),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.success_rate,
            "avg_rps": self.avg_rps,
            "avg_latency": self.avg_latency,
            "p50_latency": self.p50_latency,
            "p95_latency": self.p95_latency,
            "p99_latency": self.p99_latency,
            "error_count": len(self.errors),
            "errors": self.errors[:100],  # Limit errors in output
        }


class BenchmarkTarget:
    """Abstract base class for benchmark targets."""
    
    async def setup(self) -> None:
        """Setup before benchmark."""
        pass
    
    async def teardown(self) -> None:
        """Teardown after benchmark."""
        pass
    
    async def execute(self, *args, **kwargs) -> Any:
        """Execute a single operation."""
        raise NotImplementedError


class LoadGenerator:
    """Generates load according to configured pattern."""
    
    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.current_rps = config.target_rps
        self._running = False
    
    def get_current_rps(self, elapsed: float) -> float:
        """Get current RPS based on load pattern and elapsed time."""
        warmup = self.config.warmup_duration
        
        # Skip warmup period
        if elapsed < warmup:
            return 0.0
        
        test_elapsed = elapsed - warmup
        
        if self.config.load_pattern == LoadPattern.CONSTANT:
            return self.config.target_rps
        
        elif self.config.load_pattern == LoadPattern.RAMP:
            # Linear ramp from start_rps to target_rps
            if test_elapsed <= self.config.ramp_duration:
                progress = test_elapsed / self.config.ramp_duration
                return self.config.start_rps + (self.config.target_rps - self.config.start_rps) * progress
            return self.config.target_rps
        
        elif self.config.load_pattern == LoadPattern.STEP:
            # Step function
            steps_completed = int(test_elapsed / self.config.step_duration)
            current_step_rps = self.config.start_rps + steps_completed * self.config.step_size
            return min(current_step_rps, self.config.target_rps)
        
        elif self.config.load_pattern == LoadPattern.SPIKE:
            # Periodic spikes
            cycle_time = test_elapsed % self.config.spike_interval
            if cycle_time < self.config.spike_duration:
                return self.config.spike_rps
            return self.config.target_rps
        
        return self.config.target_rps
    
    async def generate_load(
        self,
        target: BenchmarkTarget,
        result: BenchmarkResult,
    ) -> None:
        """Generate load according to pattern."""
        self._running = True
        start = time.time()
        
        # Warmup phase
        await asyncio.sleep(self.config.warmup_duration)
        
        # Main test phase
        test_start = time.time()
        last_rps_report = test_start
        request_count = 0
        
        while self._running:
            elapsed = time.time() - test_start
            
            if elapsed >= self.config.duration:
                break
            
            # Get current target RPS
            current_rps = self.get_current_rps(time.time() - start)
            
            if current_rps <= 0:
                await asyncio.sleep(0.1)
                continue
            
            # Calculate interval between requests
            interval = 1.0 / current_rps
            
            # Execute request
            task_start = time.time()
            try:
                await asyncio.wait_for(target.execute(), timeout=self.config.timeout)
                latency = time.time() - task_start
                result.latencies.append(latency)
                result.successful_requests += 1
            except asyncio.TimeoutError:
                result.failed_requests += 1
                result.errors.append("Timeout")
            except Exception as e:
                result.failed_requests += 1
                result.errors.append(str(e))
            
            result.total_requests += 1
            request_count += 1
            
            # Record throughput history
            now = time.time()
            if now - last_rps_report >= 1.0:
                result.throughput_history.append((now, request_count))
                request_count = 0
                last_rps_report = now
            
            # Wait for next interval
            elapsed_since_task = time.time() - task_start
            wait_time = max(0, interval - elapsed_since_task)
            await asyncio.sleep(wait_time)
        
        # Cooldown
        await asyncio.sleep(self.config.cooldown_duration)
        
        self._running = False
    
    def stop(self):
        """Stop load generation."""
        self._running = False


class BenchmarkRunner:
    """Main benchmark runner."""
    
    def __init__(self, config: BenchmarkConfig):
        self.config = config
        self.results: List[BenchmarkResult] = []
    
    async def run(self, target: BenchmarkTarget) -> BenchmarkResult:
        """Run a benchmark against the target."""
        await target.setup()
        
        result = BenchmarkResult(
            config=self.config,
            start_time=time.time(),
            end_time=0,
        )
        
        generator = LoadGenerator(self.config)
        
        try:
            await generator.generate_load(target, result)
        finally:
            await target.teardown()
        
        result.end_time = time.time()
        self.results.append(result)
        
        if self.config.generate_report:
            self._generate_report(result)
        
        return result
    
    def _generate_report(self, result: BenchmarkResult) -> None:
        """Generate benchmark report."""
        output_dir = Path(self.config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.config.name}_{timestamp}"
        
        if "json" in self.config.report_formats:
            json_path = output_dir / f"{base_name}.json"
            with open(json_path, 'w') as f:
                json.dump(result.to_dict(), f, indent=2)
        
        if "html" in self.config.report_formats:
            html_path = output_dir / f"{base_name}.html"
            self._generate_html_report(result, html_path)
        
        print(f"Report generated: {output_dir / base_name}")
    
    def _generate_html_report(self, result: BenchmarkResult, path: Path) -> None:
        """Generate HTML benchmark report."""
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Benchmark Report: {self.config.name}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        .metric {{ display: inline-block; margin: 10px; padding: 15px; background: #f5f5f5; border-radius: 5px; }}
        .metric h3 {{ margin: 0 0 5px 0; font-size: 14px; color: #666; }}
        .metric .value {{ font-size: 24px; font-weight: bold; color: #333; }}
        .success {{ color: #28a745; }}
        .warning {{ color: #ffc107; }}
        .danger {{ color: #dc3545; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <h1>Benchmark Report: {self.config.name}</h1>
    <p><strong>Description:</strong> {self.config.description}</p>
    <p><strong>Type:</strong> {self.config.benchmark_type.name}</p>
    <p><strong>Load Pattern:</strong> {self.config.load_pattern.value}</p>
    <p><strong>Duration:</strong> {result.duration:.2f}s</p>
    
    <h2>Summary Metrics</h2>
    <div class="metric">
        <h3>Total Requests</h3>
        <div class="value">{result.total_requests}</div>
    </div>
    <div class="metric">
        <h3>Successful</h3>
        <div class="value success">{result.successful_requests}</div>
    </div>
    <div class="metric">
        <h3>Failed</h3>
        <div class="value danger">{result.failed_requests}</div>
    </div>
    <div class="metric">
        <h3>Success Rate</h3>
        <div class="value">{result.success_rate:.2%}</div>
    </div>
    <div class="metric">
        <h3>Avg RPS</h3>
        <div class="value">{result.avg_rps:.2f}</div>
    </div>
    <div class="metric">
        <h3>Avg Latency</h3>
        <div class="value">{result.avg_latency*1000:.2f} ms</div>
    </div>
    <div class="metric">
        <h3>P50 Latency</h3>
        <div class="value">{result.p50_latency*1000:.2f} ms</div>
    </div>
    <div class="metric">
        <h3>P95 Latency</h3>
        <div class="value">{result.p95_latency*1000:.2f} ms</div>
    </div>
    <div class="metric">
        <h3>P99 Latency</h3>
        <div class="value">{result.p99_latency*1000:.2f} ms</div>
    </div>
    
    <h2>Configuration</h2>
    <table>
        <tr><th>Parameter</th><th>Value</th></tr>
        <tr><td>Load Pattern</td><td>{self.config.load_pattern.value}</td></tr>
        <tr><td>Target RPS</td><td>{self.config.target_rps}</td></tr>
        <tr><td>Duration</td><td>{self.config.duration}s</td></tr>
        <tr><td>Concurrency</td><td>{self.config.concurrency}</td></tr>
        <tr><td>Timeout</td><td>{self.config.timeout}s</td></tr>
    </table>
    
    <h2>Throughput History</h2>
    <table>
        <tr><th>Timestamp</th><th>RPS</th></tr>
        {''.join(f'<tr><td>{ts:.1f}</td><td>{rps:.1f}</td></tr>' for ts, rps in result.throughput_history)}
    </table>
    
    <p><em>Generated at {datetime.now().isoformat()}</em></p>
</body>
</html>
"""
        with open(path, 'w') as f:
            f.write(html)


class PerformanceAnalyzer:
    """Analyzes benchmark results for bottlenecks and regressions."""
    
    @staticmethod
    def analyze_latencies(latencies: List[float]) -> Dict[str, Any]:
        """Analyze latency distribution."""
        if not latencies:
            return {}
        
        sorted_latencies = sorted(latencies)
        return {
            "count": len(latencies),
            "mean": statistics.mean(latencies),
            "median": statistics.median(latencies),
            "stdev": statistics.stdev(latencies) if len(latencies) > 1 else 0,
            "min": min(latencies),
            "max": max(latencies),
            "p50": sorted_latencies[int(len(sorted_latencies) * 0.5)],
            "p75": sorted_latencies[int(len(sorted_latencies) * 0.75)],
            "p90": sorted_latencies[int(len(sorted_latencies) * 0.90)],
            "p95": sorted_latencies[int(len(sorted_latencies) * 0.95)],
            "p99": sorted_latencies[int(len(sorted_latencies) * 0.99)],
            "p999": sorted_latencies[int(len(sorted_latencies) * 0.999)] if len(sorted_latencies) > 1000 else sorted_latencies[-1],
        }
    
    @staticmethod
    def detect_bottlenecks(result: BenchmarkResult) -> List[Dict[str, Any]]:
        """Detect performance bottlenecks from benchmark results."""
        bottlenecks = []
        
        # High error rate
        if result.success_rate < 0.99:
            bottlenecks.append({
                "type": "high_error_rate",
                "severity": "critical" if result.success_rate < 0.95 else "warning",
                "message": f"Error rate is {(1-result.success_rate)*100:.2f}%",
                "recommendation": "Check error logs and increase timeout or resources",
            })
        
        # High latency
        if result.p99_latency > 1.0:
            bottlenecks.append({
                "type": "high_latency",
                "severity": "critical" if result.p99_latency > 5.0 else "warning",
                "message": f"P99 latency is {result.p99_latency:.3f}s",
                "recommendation": "Optimize slow paths, add caching, or scale horizontally",
            })
        
        # Low throughput
        if result.avg_rps < result.config.target_rps * 0.8:
            bottlenecks.append({
                "type": "low_throughput",
                "severity": "warning",
                "message": f"Achieved {result.avg_rps:.1f} RPS vs target {result.config.target_rps}",
                "recommendation": "Increase concurrency, optimize bottlenecks, or add resources",
            })
        
        # Latency variance
        if len(result.latencies) > 1:
            cv = statistics.stdev(result.latencies) / statistics.mean(result.latencies)
            if cv > 1.0:
                bottlenecks.append({
                    "type": "high_latency_variance",
                    "severity": "warning",
                    "message": f"High latency variance (CV={cv:.2f})",
                    "recommendation": "Investigate GC pauses, lock contention, or noisy neighbors",
                })
        
        return bottlenecks
    
    @staticmethod
    def compare_results(baseline: BenchmarkResult, current: BenchmarkResult) -> Dict[str, Any]:
        """Compare current results with baseline for regression detection."""
        regression_detected = False
        changes = {}
        
        # Compare key metrics
        metrics = {
            "avg_rps": (current.avg_rps - baseline.avg_rps) / baseline.avg_rps * 100,
            "avg_latency": (current.avg_latency - baseline.avg_latency) / baseline.avg_latency * 100,
            "p95_latency": (current.p95_latency - baseline.p95_latency) / baseline.p95_latency * 100,
            "p99_latency": (current.p99_latency - baseline.p99_latency) / baseline.p99_latency * 100,
            "success_rate": (current.success_rate - baseline.success_rate) * 100,
        }
        
        for metric, pct_change in metrics.items():
            if abs(pct_change) > 10:  # 10% threshold
                regression_detected = True
            changes[metric] = {
                "baseline": getattr(baseline, metric.replace("p95_", "p95_").replace("p99_", "p99_")),
                "current": getattr(current, metric.replace("p95_", "p95_").replace("p99_", "p99_")),
                "change_pct": pct_change,
                "regression": pct_change < -10,  # Performance degradation
            }
        
        return {
            "regression_detected": regression_detected,
            "changes": changes,
            "baseline_config": asdict(baseline.config),
            "current_config": asdict(current.config),
        }


# ==================== Convenience Functions ====================

async def run_benchmark(
    target: BenchmarkTarget,
    config: BenchmarkConfig,
) -> BenchmarkResult:
    """Run a single benchmark."""
    runner = BenchmarkRunner(config)
    return await runner.run(target)


def create_constant_load_benchmark(
    name: str,
    target_rps: float,
    duration: float = 60.0,
    **kwargs,
) -> BenchmarkConfig:
    """Create a constant load benchmark config."""
    return BenchmarkConfig(
        benchmark_type=BenchmarkType.THROUGHPUT,
        name=name,
        load_pattern=LoadPattern.CONSTANT,
        target_rps=target_rps,
        duration=duration,
        **kwargs,
    )


def create_ramp_benchmark(
    name: str,
    start_rps: float,
    target_rps: float,
    ramp_duration: float = 60.0,
    **kwargs,
) -> BenchmarkConfig:
    """Create a ramp load benchmark config."""
    return BenchmarkConfig(
        benchmark_type=BenchmarkType.THROUGHPUT,
        name=name,
        load_pattern=LoadPattern.RAMP,
        start_rps=start_rps,
        target_rps=target_rps,
        duration=ramp_duration + 30,  # ramp + steady state
        ramp_duration=ramp_duration,
        **kwargs,
    )


def create_spike_benchmark(
    name: str,
    base_rps: float,
    spike_rps: float,
    spike_duration: float = 5.0,
    spike_interval: float = 30.0,
    duration: float = 120.0,
    **kwargs,
) -> BenchmarkConfig:
    """Create a spike load benchmark config."""
    return BenchmarkConfig(
        benchmark_type=BenchmarkType.SPIKE,
        name=name,
        load_pattern=LoadPattern.SPIKE,
        target_rps=base_rps,
        spike_rps=spike_rps,
        spike_duration=spike_duration,
        spike_interval=spike_interval,
        duration=duration,
        **kwargs,
    )


def create_soak_benchmark(
    name: str,
    target_rps: float,
    duration: float = 3600.0,  # 1 hour default
    **kwargs,
) -> BenchmarkConfig:
    """Create a soak test benchmark config."""
    return BenchmarkConfig(
        benchmark_type=BenchmarkType.SOAK,
        name=name,
        load_pattern=LoadPattern.CONSTANT,
        target_rps=target_rps,
        duration=duration,
        **kwargs,
    )


# ==================== Export ====================

__all__ = [
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
]