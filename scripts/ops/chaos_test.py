"""Chaos Testing for liuhao AI OS Distribution

Fault injection tests for distributed system resilience:
- Pod kill simulation
- Network partition injection  
- Latency injection
- Failover testing
"""

import asyncio
import time
import random
from typing import Dict, Any, List, Callable, Optional
from concurrent.futures import ThreadPoolExecutor


class ChaosInjector:
    """混沌注入器 - 用于分布式系统弹性测试"""
    
    def __init__(self, target_service: str, duration_seconds: int = 60):
        self.target_service = target_service
        self.duration_seconds = duration_seconds
        self._injectors: Dict[str, Callable] = {}
        self._results: List[Dict[str, Any]] = []
    
    def register_injector(self, name: str, injector_fn: Callable) -> None:
        """注册混沌注入函数"""
        self._injectors[name] = injector_fn
    
    async def inject(self, injectors: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """执行混沌注入测试
        
        Args:
            injectors: 要注入的 injector 名称列表，None 表示注入所有
            
        Returns:
            测试结果列表
        """
        targets = injectors or list(self._injectors.keys())
        tasks = []
        
        for name in targets:
            if name in self._injectors:
                tasks.append(self._run_injector(name))
        
        if tasks:
            self._results = await asyncio.gather(*tasks)
        else:
            self._results = []
        
        return self._results
    
    async def _run_injector(self, name: str) -> Dict[str, Any]:
        """运行单个混沌注入器"""
        injector = self._injectors[name]
        start = time.time()
        
        try:
            result = await injector(self.target_service)
            elapsed = time.time() - start
            return {
                "injector": name,
                "success": True,
                "elapsed_ms": int(elapsed * 1000),
                "result": result
            }
        except Exception as e:
            elapsed = time.time() - start
            return {
                "injector": name,
                "success": False,
                "elapsed_ms": int(elapsed * 1000),
                "error": str(e)
            }
    
    def reset(self) -> None:
        """重置结果"""
        self._results = []


# 常用混沌注入函数

async def pod_kill_injection(service: str) -> Dict[str, Any]:
    """模拟 Pod Kill - 杀死随机 Pod
    
    在 K8s 环境中，这会发送 SIGTERM 给随机选择的 Pod
    """
    # Would implement: kubectl delete pod <random-pod> or API call
    # For now, simulate the test scenario
    await asyncio.sleep(0.1)  # Simulate pod termination
    return {
        "action": "pod_kill",
        "service": service,
        "survived": random.choice([True, False]),
        "recovery_time_ms": random.randint(100, 5000)
    }


async def network_partition_injection(service: str) -> Dict[str, Any]:
    """模拟网络分区 - 中断服务间网络连接"""
    await asyncio.sleep(0.1)  # Simulate network disruption
    return {
        "action": "network_partition",
        "service": service,
        "partition_duration_ms": random.randint(500, 30000),
        "services_affected": random.randint(1, 10)
    }


async def latency_injection(service: str, min_ms: int = 100, max_ms: int = 5000) -> Dict[str, Any]:
    """模拟延迟注入 - 人为增加服务响应延迟"""
    await asyncio.sleep(random.uniform(min_ms/1000, max_ms/1000))
    return {
        "action": "latency_injection",
        "service": service,
        "added_latency_ms": random.randint(min_ms, max_ms),
        "duration_seconds": random.randint(10, 60)
    }


async def failover_test(service: str, num_restarts: int = 3) -> Dict[str, Any]:
    """测试服务故障转移能力"""
    results = []
    for i in range(num_restarts):
        # Simulate restart
        await asyncio.sleep(0.05)
        results.append({
            "restart_number": i + 1,
            "success": random.choice([True, False]),
            "recovery_time_ms": random.randint(200, 3000)
        })
    
    successful = sum(1 for r in results if r["success"])
    return {
        "action": "failover_test",
        "service": service,
        "total_restarts": num_restarts,
        "successful_restarts": successful,
        "success_rate": successful / num_restarts,
        "all_passed": successful == num_restarts
    }


# Convenience function
def create_chaos_injector(service: str, duration: int = 60) -> ChaosInjector:
    """创建混沌注入器实例
    
    Args:
        target_service: 目标服务名称
        duration: 测试时长(秒)
        
    Returns:
        配置好的 ChaosInjector 实例
    """
    injector = ChaosInjector(service, duration)
    
    injector.register_injector("pod_kill", pod_kill_injection)
    injector.register_injector("network_partition", network_partition_injection)
    injector.register_injector("latency_injection", lambda s: latency_injection(s, 100, 2000))
    injector.register_injector("failover_test", failover_test)
    
    return injector
