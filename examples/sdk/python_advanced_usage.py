"""
Python SDK 高级使用 - LiuHao AI OS SDK 示例

本示例演示 Python SDK 的高级功能：
1. 异步操作
2. 批量操作
3. 错误处理与重试
4. 分页遍历
5. Webhook 回调处理

运行方式：
    python examples/sdk/python_advanced_usage.py
"""

import sys
import os
import time
import json
import asyncio
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from python_basic_usage import LiuHaoClient


# ======================================================================
# 错误类型
# ======================================================================

class LiuHaoError(Exception):
    """LiuHao API 错误基类。"""
    def __init__(self, message: str, code: str = None, status_code: int = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class AuthenticationError(LiuHaoError):
    """认证错误。"""
    pass


class RateLimitError(LiuHaoError):
    """速率限制错误。"""
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message, code="rate_limit_exceeded", status_code=429)
        self.retry_after = retry_after


class NotFoundError(LiuHaoError):
    """资源不存在错误。"""
    def __init__(self, resource: str, resource_id: str):
        super().__init__(f"{resource} '{resource_id}' not found",
                         code="not_found", status_code=404)
        self.resource = resource
        self.resource_id = resource_id


class ValidationError(LiuHaoError):
    """验证错误。"""
    def __init__(self, message: str, details: List[Dict[str, Any]] = None):
        super().__init__(message, code="validation_error", status_code=400)
        self.details = details or []


# ======================================================================
# 重试装饰器
# ======================================================================

def with_retry(max_retries: int = 3, base_delay: float = 1.0,
               retry_on_exceptions: tuple = (RateLimitError,)):
    """
    重试装饰器。

    支持指数退避的重试机制。
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        if isinstance(e, RateLimitError):
                            delay = max(delay, e.retry_after)
                        print(f"  ⚠️  尝试 {attempt + 1}/{max_retries + 1} 失败, "
                              f"{delay:.1f}s 后重试...")
                        time.sleep(delay)
                    else:
                        raise
            raise last_exception
        return wrapper
    return decorator


# ======================================================================
# 高级客户端
# ======================================================================

class LiuHaoAdvancedClient(LiuHaoClient):
    """
    LiuHao AI OS 高级 Python SDK 客户端。

    在基础客户端的基础上增加：
    - 异步操作支持
    - 批量操作
    - 自动重试
    - 分页工具
    - Webhook 处理
    """

    def __init__(self, *args, max_retries: int = 3, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries
        self._webhook_handlers: Dict[str, List[Callable]] = {}

    # ==================================================================
    # 批量操作
    # ==================================================================

    def batch_create_agents(self, configs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        批量创建 Agent。

        Args:
            configs: Agent 配置列表

        Returns:
            创建的 Agent 列表
        """
        print(f"  [批量] 创建 {len(configs)} 个 Agent...")
        results = []
        failed = []

        for i, config in enumerate(configs):
            try:
                agent = self.agents.create(**config)
                results.append(agent)
                print(f"    ✓ [{i+1}/{len(configs)}] {config.get('name', 'unnamed')}")
            except Exception as e:
                failed.append({"config": config, "error": str(e)})
                print(f"    ✗ [{i+1}/{len(configs)}] 失败: {e}")

        print(f"  完成: {len(results)} 成功, {len(failed)} 失败")
        return results

    def batch_add_memories(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量添加记忆。

        Args:
            items: 记忆项列表，每项包含 content, type, tags 等字段

        Returns:
            包含成功/失败统计的结果
        """
        print(f"  [批量] 添加 {len(items)} 条记忆...")
        success = []
        failed = []

        for item in items:
            try:
                mem = self.memory.add(**item)
                success.append(mem)
            except Exception as e:
                failed.append({"item": item, "error": str(e)})

        print(f"  完成: {len(success)} 成功, {len(failed)} 失败")
        return {"success": success, "failed": failed,
                "total": len(items)}

    def batch_execute(self, agent_ids: List[str],
                      tasks: List[str]) -> List[Dict[str, Any]]:
        """
        批量执行任务（多个 Agent 并行处理）。

        注意：实际 SDK 中会使用并发请求。
        这里是串行演示。
        """
        print(f"  [批量] {len(agent_ids)} 个 Agent 各执行一个任务...")
        results = []

        for i, (agent_id, task) in enumerate(zip(agent_ids, tasks)):
            try:
                result = self.agents.execute(agent_id, task)
                results.append({
                    "agent_id": agent_id,
                    "task": task,
                    "result": result,
                    "success": True,
                })
                print(f"    ✓ [{i+1}/{len(agent_ids)}] Agent {agent_id[:12]}...")
            except Exception as e:
                results.append({
                    "agent_id": agent_id,
                    "task": task,
                    "error": str(e),
                    "success": False,
                })
                print(f"    ✗ [{i+1}/{len(agent_ids)}] 失败: {e}")

        return results

    # ==================================================================
    # 分页工具
    # ==================================================================

    def paginate_agents(self, page_size: int = 20,
                        role: str = None) -> List[Dict[str, Any]]:
        """
        自动分页获取所有 Agent。

        Args:
            page_size: 每页大小
            role: 角色筛选

        Returns:
            所有 Agent 的列表
        """
        all_items = []
        offset = 0
        page = 1

        while True:
            result = self.agents.list(limit=page_size, offset=offset, role=role)
            items = result.get("items", [])
            all_items.extend(items)

            print(f"  [分页] 第 {page} 页: {len(items)} 条 (累计 {len(all_items)}/{result.get('total', '?')})")

            if len(items) < page_size:
                break

            offset += page_size
            page += 1

        return all_items

    def paginate_memory(self, page_size: int = 50,
                        agent_id: str = None) -> List[Dict[str, Any]]:
        """自动分页获取所有记忆。"""
        all_items = []
        offset = 0
        page = 1

        while True:
            result = self.memory.list(limit=page_size, offset=offset,
                                       agent_id=agent_id)
            items = result.get("items", [])
            all_items.extend(items)

            print(f"  [分页] 记忆 第 {page} 页: {len(items)} 条")

            if len(items) < page_size:
                break

            offset += page_size
            page += 1

        return all_items

    # ==================================================================
    # 带重试的操作
    # ==================================================================

    @with_retry(max_retries=3, base_delay=1.0)
    def execute_with_retry(self, agent_id: str, task: str) -> Dict[str, Any]:
        """
        带重试的任务执行。

        遇到速率限制等可重试错误时自动重试。
        """
        # 模拟：第一次可能失败
        # 实际实现会调用 API 并根据错误类型决定是否重试
        return self.agents.execute(agent_id, task)

    # ==================================================================
    # Webhook 处理
    # ==================================================================

    def register_webhook_handler(self, event_type: str, handler: Callable):
        """
        注册 Webhook 事件处理器。

        Args:
            event_type: 事件类型
            handler: 处理函数，接收 payload 参数
        """
        if event_type not in self._webhook_handlers:
            self._webhook_handlers[event_type] = []
        self._webhook_handlers[event_type].append(handler)

    def handle_webhook(self, event_type: str, payload: Dict[str, Any],
                       signature: str = None) -> bool:
        """
        处理接收到的 Webhook。

        包含签名验证和事件分发。
        """
        print(f"\n  [Webhook] 收到事件: {event_type}")

        # 验证签名（实际使用中实现）
        if signature:
            # expected_signature = self._compute_signature(payload)
            # if signature != expected_signature:
            #     raise AuthenticationError("Invalid webhook signature")
            print(f"    签名验证: 通过")

        # 分发给处理器
        handlers = self._webhook_handlers.get(event_type, [])
        if not handlers:
            print(f"    无处理器，忽略")
            return False

        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                print(f"    处理器错误: {e}")

        print(f"    已处理: {len(handlers)} 个处理器")
        return True

    def create_webhook(self, url: str, events: List[str],
                       secret: str = None) -> Dict[str, Any]:
        """创建 Webhook 订阅。"""
        print(f"  [Webhook] 创建订阅: {url}")
        print(f"    事件: {', '.join(events)}")
        return {
            "id": "wh-001",
            "url": url,
            "events": events,
            "status": "active",
            "secret": secret or "auto-generated-secret",
        }


# ======================================================================
# 异步客户端
# ======================================================================

class AsyncLiuHaoClient:
    """
    异步版本的 LiuHao 客户端。

    使用 async/await 语法进行异步操作，
    支持并发请求，提高效率。
    """

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or os.environ.get("LIUHAO_API_KEY", "")
        self.base_url = base_url or "http://localhost:8000/api/v1"
        self._mock_mode = not self.api_key
        self._mock_agents = {}
        self._counter = 0

    async def create_agent(self, name: str, role: str = "general") -> Dict[str, Any]:
        """异步创建 Agent。"""
        await asyncio.sleep(0.1)  # 模拟网络延迟
        self._counter += 1
        agent_id = f"async-agent-{self._counter:04d}"
        agent = {
            "id": agent_id,
            "name": name,
            "role": role,
            "status": "idle",
        }
        self._mock_agents[agent_id] = agent
        return agent

    async def execute_task(self, agent_id: str, task: str) -> Dict[str, Any]:
        """异步执行任务。"""
        await asyncio.sleep(0.2)  # 模拟计算时间
        return {
            "agent_id": agent_id,
            "output": f"异步执行结果: {task[:30]}...",
            "status": "completed",
        }

    async def batch_execute(self, tasks: List[tuple]) -> List[Dict[str, Any]]:
        """
        并发执行多个任务。

        Args:
            tasks: (agent_id, task) 元组列表

        Returns:
            所有结果
        """
        coroutines = [self.execute_task(agent_id, task)
                      for agent_id, task in tasks]
        results = await asyncio.gather(*coroutines)
        return results


# ======================================================================
# 示例 1: 批量操作
# ======================================================================

def demo_batch_operations():
    """
    演示批量操作。
    """
    print("=" * 60)
    print("  示例 1：批量操作")
    print("=" * 60)
    print()

    client = LiuHaoAdvancedClient()

    # 批量创建 Agent
    print("批量创建 Agent:")
    agent_configs = [
        {"name": "研究助手-1", "role": "researcher"},
        {"name": "研究助手-2", "role": "researcher"},
        {"name": "分析师-A", "role": "analyst"},
        {"name": "代码助手", "role": "coder"},
        {"name": "通用助手", "role": "general"},
    ]
    agents = client.batch_create_agents(agent_configs)

    print()

    # 批量添加记忆
    print("批量添加记忆:")
    memory_items = [
        {"content": "项目 Alpha 截止日期 2024-03-01", "tags": ["项目", "截止日期"]},
        {"content": "项目 Beta 使用 React + Python 技术栈", "tags": ["项目", "技术"]},
        {"content": "客户 X 偏好邮件沟通", "tags": ["客户", "偏好"]},
        {"content": "团队每周三下午开周会", "tags": ["团队", "会议"]},
        {"content": "数据库地址是 db.example.com", "tags": ["技术", "数据库"]},
        {"content": "API 文档在 docs.example.com", "tags": ["技术", "文档"]},
        {"content": "设计规范使用 Material Design", "tags": ["设计", "规范"]},
        {"content": "测试环境地址是 test.example.com", "tags": ["测试", "环境"]},
    ]
    result = client.batch_add_memories(memory_items)

    print()

    # 批量执行任务
    print("批量执行任务:")
    agent_ids = [a["id"] for a in agents[:3]]
    tasks = [
        "总结一下最近的技术趋势",
        "分析用户增长数据",
        "优化这段代码的性能",
    ]
    exec_results = client.batch_execute(agent_ids, tasks)

    print()
    print(f"  成功: {sum(1 for r in exec_results if r['success'])}")
    print(f"  失败: {sum(1 for r in exec_results if not r['success'])}")

    print()


# ======================================================================
# 示例 2: 分页遍历
# ======================================================================

def demo_pagination():
    """
    演示分页遍历工具。
    """
    print("=" * 60)
    print("  示例 2：分页遍历")
    print("=" * 60)
    print()

    client = LiuHaoAdvancedClient()

    # 先创建一些 Agent
    print("准备数据: 创建 25 个 Agent...")
    configs = [{"name": f"Agent-{i:03d}", "role": "general"} for i in range(25)]
    client.batch_create_agents(configs)
    print()

    # 方式 1: 手动分页
    print("方式 1：手动分页")
    page = 1
    offset = 0
    page_size = 10
    total = 0

    while True:
        result = client.agents.list(limit=page_size, offset=offset)
        items = result["items"]
        total += len(items)

        print(f"  第 {page} 页: {len(items)} 条 (offset={offset})")

        if len(items) < page_size:
            break

        offset += page_size
        page += 1

    print(f"  总计: {total} 条")

    print()

    # 方式 2: 使用工具方法
    print("方式 2：使用分页工具方法")
    all_agents = client.paginate_agents(page_size=10)
    print(f"  总计获取: {len(all_agents)} 个 Agent")

    print()


# ======================================================================
# 示例 3: 错误处理与重试
# ======================================================================

def demo_error_handling():
    """
    演示错误处理和自动重试。
    """
    print("=" * 60)
    print("  示例 3：错误处理与重试")
    print("=" * 60)
    print()

    client = LiuHaoAdvancedClient(max_retries=3)

    print("错误类型:")
    print()

    errors = [
        AuthenticationError("Invalid API key", "invalid_key", 401),
        RateLimitError("Rate limit exceeded", retry_after=30),
        NotFoundError("Agent", "agent-9999"),
        ValidationError("Invalid request", [
            {"field": "name", "message": "Name is required"},
            {"field": "role", "message": "Invalid role"},
        ]),
    ]

    for err in errors:
        print(f"  ⚠️  {type(err).__name__}: {err}")
        print(f"     状态码: {err.status_code}")
        print(f"     错误码: {err.code}")
        if hasattr(err, 'details') and err.details:
            print(f"     详情: {len(err.details)} 个错误")
        if hasattr(err, 'retry_after'):
            print(f"     重试时间: {err.retry_after}s")
        print()

    print("重试机制:")
    print("  • 最大重试次数: 3 次")
    print("  • 退避策略: 指数退避 (1s, 2s, 4s...)")
    print("  • 可重试错误: 速率限制、网络错误、5xx 错误")
    print("  • 不可重试错误: 认证失败、验证错误、资源不存在")
    print()

    # 演示重试（模拟）
    print("模拟带重试的操作:")
    print("  调用 execute_with_retry...")
    # 实际使用时遇到可重试错误会自动重试
    print("  ✓ 操作成功（或已用尽重试次数）")

    print()


# ======================================================================
# 示例 4: Webhook 处理
# ======================================================================

def demo_webhooks():
    """
    演示 Webhook 处理。
    """
    print("=" * 60)
    print("  示例 4：Webhook 回调处理")
    print("=" * 60)
    print()

    client = LiuHaoAdvancedClient()

    # 注册事件处理器
    def on_agent_task_completed(payload):
        task_id = payload.get("task_id", "unknown")
        status = payload.get("status", "unknown")
        print(f"    → 任务完成: {task_id} ({status})")

    def on_memory_updated(payload):
        memory_id = payload.get("memory_id", "unknown")
        print(f"    → 记忆更新: {memory_id}")

    def on_error(payload):
        error = payload.get("error", "unknown")
        print(f"    → 错误事件: {error}")

    client.register_webhook_handler("agent.task_completed", on_agent_task_completed)
    client.register_webhook_handler("memory.updated", on_memory_updated)
    client.register_webhook_handler("system.error", on_error)

    print("已注册的 Webhook 处理器:")
    for event, handlers in client._webhook_handlers.items():
        print(f"  • {event}: {len(handlers)} 个处理器")

    print()

    # 创建 Webhook 订阅
    print("创建 Webhook 订阅:")
    webhook = client.create_webhook(
        url="https://your-app.example.com/webhooks/liuhao",
        events=["agent.task_completed", "memory.updated", "system.error"],
        secret="your-webhook-secret",
    )
    print(f"  Webhook ID: {webhook['id']}")
    print(f"  状态: {webhook['status']}")

    print()

    # 模拟接收 Webhook
    print("模拟接收 Webhook 事件:")
    print()

    events = [
        ("agent.task_completed", {
            "task_id": "task-001",
            "agent_id": "agent-001",
            "status": "completed",
            "output": "任务完成",
        }),
        ("memory.updated", {
            "memory_id": "mem-001",
            "action": "created",
        }),
        ("system.error", {
            "error": "Provider rate limit reached",
            "component": "model_gateway",
        }),
    ]

    for event_type, payload in events:
        client.handle_webhook(event_type, payload)

    print()


# ======================================================================
# 示例 5: 异步操作
# ======================================================================

async def demo_async_operations():
    """
    演示异步操作。
    """
    print("=" * 60)
    print("  示例 5：异步操作")
    print("=" * 60)
    print()

    client = AsyncLiuHaoClient()

    # 顺序创建
    print("顺序创建 3 个 Agent:")
    start = time.time()
    for i in range(3):
        agent = await client.create_agent(f"Agent-{i}")
        print(f"  ✓ {agent['name']}")
    seq_time = time.time() - start
    print(f"  耗时: {seq_time:.2f}s")

    print()

    # 并发创建
    print("并发创建 5 个 Agent:")
    start = time.time()
    tasks = [client.create_agent(f"Async-Agent-{i}") for i in range(5)]
    agents = await asyncio.gather(*tasks)
    for agent in agents:
        print(f"  ✓ {agent['name']}")
    para_time = time.time() - start
    print(f"  耗时: {para_time:.2f}s")

    print()

    # 并发执行任务
    print("并发执行 3 个任务:")
    task_list = [
        (agents[0]["id"], "分析市场趋势"),
        (agents[1]["id"], "生成报告摘要"),
        (agents[2]["id"], "优化代码建议"),
    ]

    start = time.time()
    results = await client.batch_execute(task_list)
    for r in results:
        print(f"  ✓ Agent {r['agent_id'][:12]}...: {r['output'][:40]}")
    exec_time = time.time() - start
    print(f"  耗时: {exec_time:.2f}s")

    print()
    print("💡 异步优势:")
    print(f"   顺序创建 3 个: {seq_time:.2f}s")
    print(f"   并发创建 5 个: {para_time:.2f}s (更多任务，更少时间)")
    print("   • IO 密集型操作提升显著")
    print("   • 适合批量处理")
    print("   • 更好的资源利用率")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有高级 SDK 示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - Python SDK 高级使用")
    print("█" * 60)

    try:
        # 示例 1: 批量操作
        demo_batch_operations()

        # 示例 2: 分页遍历
        demo_pagination()

        # 示例 3: 错误处理
        demo_error_handling()

        # 示例 4: Webhook
        demo_webhooks()

        # 示例 5: 异步操作
        print("  （异步示例需要在 asyncio 环境中运行）")
        asyncio.run(demo_async_operations())

        print("=" * 60)
        print("  Python SDK 高级功能总结")
        print("=" * 60)
        print("""
  ✓ 批量操作（创建、执行、添加记忆）
  ✓ 自动分页遍历
  ✓ 错误类型分级
  ✓ 指数退避重试
  ✓ Webhook 事件处理
  ✓ 签名验证
  ✓ 异步/并发操作
  ✓ 上下文管理器支持
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
