"""
Python SDK 基础使用 - LiuHao AI OS SDK 示例

本示例演示 Python SDK 的基础使用：
1. 客户端初始化
2. Agent 操作（创建、执行、查询状态）
3. 记忆操作（添加、查询、搜索）
4. 知识库操作（添加文档、检索）

运行方式：
    python examples/sdk/python_basic_usage.py

注意：这是 SDK 使用示例，展示 API 调用方式。
实际使用需要运行 LiuHao AI OS 服务端。
"""

import sys
import os
import json
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ======================================================================
# LiuHao 客户端（SDK 风格）
# ======================================================================

class LiuHaoClient:
    """
    LiuHao AI OS Python SDK 客户端。

    提供对 LiuHao AI OS API 的封装，支持：
    - Agent 管理
    - 记忆系统操作
    - 知识库操作
    - 任务管理
    """

    def __init__(self, api_key: str = None, base_url: str = None,
                 timeout: int = 30):
        """
        初始化客户端。

        Args:
            api_key: API 密钥
            base_url: API 基础 URL
            timeout: 请求超时时间（秒）
        """
        self.api_key = api_key or os.environ.get("LIUHAO_API_KEY", "")
        self.base_url = base_url or os.environ.get("LIUHAO_BASE_URL",
                                                   "http://localhost:8000/api/v1")
        self.timeout = timeout

        # 模拟模式（没有真实 API 时使用）
        self._mock_mode = not self.api_key
        self._mock_agents: Dict[str, Dict[str, Any]] = {}
        self._mock_memories: List[Dict[str, Any]] = []
        self._mock_knowledge: List[Dict[str, Any]] = []
        self._agent_counter = 0

        if self._mock_mode:
            print("  ⚠️  API Key 未配置，使用模拟模式")

    # ==================================================================
    # Agent 操作
    # ==================================================================

    class AgentsAPI:
        """Agent 相关 API。"""

        def __init__(self, client: "LiuHaoClient"):
            self.client = client

        def create(self, name: str, role: str = "general",
                   system_prompt: str = "",
                   temperature: float = 0.7) -> Dict[str, Any]:
            """
            创建一个新的 Agent。

            Args:
                name: Agent 名称
                role: 角色 (general, researcher, analyst, coder 等)
                system_prompt: 系统提示词
                temperature: 温度参数

            Returns:
                创建的 Agent 信息
            """
            if self.client._mock_mode:
                self.client._agent_counter += 1
                agent_id = f"agent-{self.client._agent_counter:04d}"
                agent = {
                    "id": agent_id,
                    "name": name,
                    "role": role,
                    "status": "idle",
                    "system_prompt": system_prompt or f"你是一个{role}角色的 AI 助手。",
                    "temperature": temperature,
                    "created_at": "2024-01-15T10:00:00Z",
                }
                self.client._mock_agents[agent_id] = agent
                return agent

            # 实际实现: POST /agents
            return self._request("POST", "/agents", {
                "name": name,
                "role": role,
                "system_prompt": system_prompt,
                "temperature": temperature,
            })

        def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
            """获取 Agent 详情。"""
            if self.client._mock_mode:
                return self.client._mock_agents.get(agent_id)
            return self._request("GET", f"/agents/{agent_id}")

        def list(self, limit: int = 20, offset: int = 0,
                 role: str = None) -> Dict[str, Any]:
            """
            列出 Agent。

            Args:
                limit: 每页数量
                offset: 偏移量
                role: 按角色筛选

            Returns:
                包含 items 和 total 的字典
            """
            if self.client._mock_mode:
                items = list(self.client._mock_agents.values())
                if role:
                    items = [a for a in items if a["role"] == role]
                total = len(items)
                paginated = items[offset:offset + limit]
                return {"items": paginated, "total": total,
                        "limit": limit, "offset": offset}
            params = {"limit": limit, "offset": offset}
            if role:
                params["role"] = role
            return self._request("GET", "/agents", params=params)

        def execute(self, agent_id: str, task: str,
                    stream: bool = False) -> Dict[str, Any]:
            """
            让 Agent 执行任务。

            Args:
                agent_id: Agent ID
                task: 任务描述
                stream: 是否流式输出

            Returns:
                执行结果
            """
            if self.client._mock_mode:
                agent = self.client._mock_agents.get(agent_id)
                if not agent:
                    raise ValueError(f"Agent {agent_id} 不存在")

                agent["status"] = "busy"

                # 模拟执行
                result = {
                    "output": f"这是对任务 '{task[:30]}...' 的模拟回答。\n\n"
                              f"Agent: {agent['name']}\n"
                              f"角色: {agent['role']}\n\n"
                              f"在真实环境中，这里会返回 AI 生成的实际回答。",
                    "status": "completed",
                    "latency_ms": 1234,
                    "tokens_used": 150,
                    "task_id": f"task-{int(time.time())}",
                }

                agent["status"] = "idle"
                return result

            return self._request("POST", f"/agents/{agent_id}/execute",
                                 {"task": task, "stream": stream})

        def get_status(self, agent_id: str) -> Dict[str, Any]:
            """获取 Agent 状态。"""
            if self.client._mock_mode:
                agent = self.client._mock_agents.get(agent_id, {})
                return {
                    "id": agent_id,
                    "status": agent.get("status", "unknown"),
                    "current_task": agent.get("current_task"),
                    "completed_tasks": agent.get("completed_tasks", 0),
                }
            return self._request("GET", f"/agents/{agent_id}/status")

        def delete(self, agent_id: str) -> bool:
            """删除 Agent。"""
            if self.client._mock_mode:
                if agent_id in self.client._mock_agents:
                    del self.client._mock_agents[agent_id]
                    return True
                return False
            self._request("DELETE", f"/agents/{agent_id}")
            return True

        def _request(self, method: str, path: str, data: Dict = None,
                     params: Dict = None) -> Dict[str, Any]:
            """发送 HTTP 请求（内部方法）。"""
            # 实际实现使用 requests 或 httpx
            # 这里是模拟
            raise NotImplementedError("真实 API 调用需要配置 API Key")

    # ==================================================================
    # 记忆操作
    # ==================================================================

    class MemoryAPI:
        """记忆系统相关 API。"""

        def __init__(self, client: "LiuHaoClient"):
            self.client = client

        def add(self, content: str, memory_type: str = "semantic",
                tags: List[str] = None, metadata: Dict[str, Any] = None,
                agent_id: str = None) -> Dict[str, Any]:
            """
            添加记忆。

            Args:
                content: 记忆内容
                memory_type: 记忆类型 (semantic, episodic, working)
                tags: 标签列表
                metadata: 元数据
                agent_id: 关联的 Agent ID

            Returns:
                创建的记忆条目
            """
            if self.client._mock_mode:
                memory_id = f"mem-{len(self.client._mock_memories) + 1:04d}"
                memory = {
                    "id": memory_id,
                    "content": content,
                    "type": memory_type,
                    "tags": tags or [],
                    "metadata": metadata or {},
                    "agent_id": agent_id,
                    "created_at": "2024-01-15T10:00:00Z",
                }
                self.client._mock_memories.append(memory)
                return memory

            return self._request("POST", "/memory", {
                "content": content,
                "type": memory_type,
                "tags": tags,
                "metadata": metadata,
                "agent_id": agent_id,
            })

        def search(self, query: str, limit: int = 10,
                   memory_type: str = None) -> Dict[str, Any]:
            """
            搜索记忆。

            Args:
                query: 搜索查询
                limit: 返回数量
                memory_type: 按类型筛选

            Returns:
                搜索结果
            """
            if self.client._mock_mode:
                query_lower = query.lower()
                results = []

                for mem in self.client._mock_memories:
                    # 简单的关键词匹配
                    score = 0.0
                    content_lower = mem["content"].lower()
                    if query_lower in content_lower:
                        score = 1.0
                    else:
                        query_words = set(query_lower.split())
                        content_words = set(content_lower.split())
                        if query_words & content_words:
                            score = len(query_words & content_words) / len(query_words)

                    if memory_type and mem["type"] != memory_type:
                        continue

                    if score > 0:
                        results.append({**mem, "score": score})

                results.sort(key=lambda x: x["score"], reverse=True)
                return {
                    "results": results[:limit],
                    "total": len(results),
                    "query": query,
                }

            params = {"q": query, "limit": limit}
            if memory_type:
                params["type"] = memory_type
            return self._request("GET", "/memory/search", params=params)

        def get(self, memory_id: str) -> Optional[Dict[str, Any]]:
            """获取记忆详情。"""
            if self.client._mock_mode:
                for mem in self.client._mock_memories:
                    if mem["id"] == memory_id:
                        return mem
                return None
            return self._request("GET", f"/memory/{memory_id}")

        def list(self, agent_id: str = None, limit: int = 20,
                 offset: int = 0) -> Dict[str, Any]:
            """列出记忆条目。"""
            if self.client._mock_mode:
                items = self.client._mock_memories
                if agent_id:
                    items = [m for m in items if m.get("agent_id") == agent_id]
                total = len(items)
                return {"items": items[offset:offset + limit], "total": total}
            params = {"limit": limit, "offset": offset}
            if agent_id:
                params["agent_id"] = agent_id
            return self._request("GET", "/memory", params=params)

        def delete(self, memory_id: str) -> bool:
            """删除记忆。"""
            if self.client._mock_mode:
                for i, mem in enumerate(self.client._mock_memories):
                    if mem["id"] == memory_id:
                        self.client._mock_memories.pop(i)
                        return True
                return False
            self._request("DELETE", f"/memory/{memory_id}")
            return True

        def _request(self, method, path, data=None, params=None):
            raise NotImplementedError("真实 API 调用需要配置 API Key")

    # ==================================================================
    # 知识库操作
    # ==================================================================

    class KnowledgeAPI:
        """知识库相关 API。"""

        def __init__(self, client: "LiuHaoClient"):
            self.client = client

        def add_document(self, title: str, content: str,
                         source: str = "", tags: List[str] = None,
                         metadata: Dict[str, Any] = None) -> Dict[str, Any]:
            """
            添加文档到知识库。

            Args:
                title: 文档标题
                content: 文档内容
                source: 来源
                tags: 标签
                metadata: 元数据

            Returns:
                添加的文档信息
            """
            if self.client._mock_mode:
                doc_id = f"doc-{len(self.client._mock_knowledge) + 1:04d}"
                doc = {
                    "id": doc_id,
                    "title": title,
                    "content": content,
                    "source": source,
                    "tags": tags or [],
                    "metadata": metadata or {},
                    "chunk_count": len(content) // 500 + 1,  # 模拟分块数
                    "created_at": "2024-01-15T10:00:00Z",
                }
                self.client._mock_knowledge.append(doc)
                return doc

            return self._request("POST", "/knowledge/documents", {
                "title": title,
                "content": content,
                "source": source,
                "tags": tags,
                "metadata": metadata,
            })

        def search(self, query: str, limit: int = 5,
                   min_score: float = 0.5) -> Dict[str, Any]:
            """
            语义搜索知识库。

            Args:
                query: 查询文本
                limit: 返回数量
                min_score: 最低相似度分数

            Returns:
                搜索结果
            """
            if self.client._mock_mode:
                # 简单模拟
                results = []
                for doc in self.client._mock_knowledge:
                    # 模拟相似度
                    import random
                    random.seed(hash(query + doc["id"]))
                    score = random.uniform(0.5, 0.95)
                    if score >= min_score:
                        results.append({
                            "document": doc,
                            "score": score,
                            "snippet": doc["content"][:200] + "...",
                        })

                results.sort(key=lambda x: x["score"], reverse=True)
                return {
                    "results": results[:limit],
                    "total": len(results),
                    "query": query,
                }

            return self._request("GET", "/knowledge/search", params={
                "q": query, "limit": limit, "min_score": min_score,
            })

        def list_documents(self, limit: int = 20,
                           offset: int = 0) -> Dict[str, Any]:
            """列出所有文档。"""
            if self.client._mock_mode:
                items = self.client._mock_knowledge
                return {
                    "items": items[offset:offset + limit],
                    "total": len(items),
                }
            return self._request("GET", "/knowledge/documents",
                                 params={"limit": limit, "offset": offset})

        def delete_document(self, doc_id: str) -> bool:
            """删除文档。"""
            if self.client._mock_mode:
                for i, doc in enumerate(self.client._mock_knowledge):
                    if doc["id"] == doc_id:
                        self.client._mock_knowledge.pop(i)
                        return True
                return False
            self._request("DELETE", f"/knowledge/documents/{doc_id}")
            return True

        def _request(self, method, path, data=None, params=None):
            raise NotImplementedError("真实 API 调用需要配置 API Key")

    # ==================================================================
    # 属性访问
    # ==================================================================

    @property
    def agents(self) -> AgentsAPI:
        """Agent API。"""
        return self.AgentsAPI(self)

    @property
    def memory(self) -> MemoryAPI:
        """记忆 API。"""
        return self.MemoryAPI(self)

    @property
    def knowledge(self) -> KnowledgeAPI:
        """知识库 API。"""
        return self.KnowledgeAPI(self)


# ======================================================================
# 示例 1: 客户端初始化
# ======================================================================

def demo_client_init():
    """
    演示客户端初始化。
    """
    print("=" * 60)
    print("  示例 1：客户端初始化")
    print("=" * 60)
    print()

    # 方式 1: 直接传参
    print("方式 1：直接传入参数")
    client1 = LiuHaoClient(
        api_key="your-api-key-here",
        base_url="https://api.liuhao.example.com/v1",
        timeout=30,
    )
    print(f"  Base URL: {client1.base_url}")
    print(f"  超时: {client1.timeout}s")

    print()

    # 方式 2: 使用环境变量
    print("方式 2：使用环境变量")
    print("  设置环境变量:")
    print("    LIUHAO_API_KEY=your-api-key")
    print("    LIUHAO_BASE_URL=https://api.liuhao.example.com/v1")
    print()
    client2 = LiuHaoClient()
    print(f"  模式: {'模拟' if client2._mock_mode else '真实API'}")

    print()
    return client2


# ======================================================================
# 示例 2: Agent 操作
# ======================================================================

def demo_agent_operations(client: LiuHaoClient):
    """
    演示 Agent 相关操作。
    """
    print("=" * 60)
    print("  示例 2：Agent 操作")
    print("=" * 60)
    print()

    # 创建 Agent
    print("[1/5] 创建 Agent...")
    agent = client.agents.create(
        name="我的助手",
        role="general",
        system_prompt="你是一个友好的 AI 助手，善于回答各种问题。",
        temperature=0.7,
    )
    print(f"  已创建 Agent: {agent['name']} (ID: {agent['id']})")
    print(f"  角色: {agent['role']}")
    print(f"  状态: {agent['status']}")

    print()

    # 再创建几个不同角色的 Agent
    print("[2/5] 创建不同角色的 Agent...")
    researcher = client.agents.create("研究助手", "researcher")
    coder = client.agents.create("代码助手", "coder")
    analyst = client.agents.create("数据分析员", "analyst")
    print(f"  已创建 3 个不同角色的 Agent")

    print()

    # 列出 Agent
    print("[3/5] 列出所有 Agent...")
    result = client.agents.list(limit=10)
    print(f"  总数: {result['total']}")
    for agent in result["items"]:
        print(f"    • {agent['name']} ({agent['role']}) - {agent['status']}")

    print()

    # 执行任务
    print("[4/5] 执行任务...")
    task = "介绍一下 Python 编程语言的主要特点"
    print(f"  任务: {task}")
    print()
    print("  执行中...")

    exec_result = client.agents.execute(agent["id"], task)
    print(f"  状态: {exec_result['status']}")
    print(f"  耗时: {exec_result['latency_ms']}ms")
    print(f"  Token 使用: {exec_result['tokens_used']}")
    print()
    print("  回答:")
    for line in exec_result["output"].split("\n")[:5]:
        print(f"    {line}")

    print()

    # 查询状态
    print("[5/5] 查询 Agent 状态...")
    status = client.agents.get_status(agent["id"])
    print(f"  状态: {status['status']}")
    print(f"  已完成任务: {status['completed_tasks']}")

    print()


# ======================================================================
# 示例 3: 记忆操作
# ======================================================================

def demo_memory_operations(client: LiuHaoClient):
    """
    演示记忆系统相关操作。
    """
    print("=" * 60)
    print("  示例 3：记忆操作")
    print("=" * 60)
    print()

    # 添加记忆
    print("[1/4] 添加记忆...")
    memories_to_add = [
        ("用户喜欢深色主题", "semantic", ["偏好", "UI"]),
        ("用户的项目截止日期是本周五", "semantic", ["项目", "截止日期"]),
        ("上次会议讨论了性能优化方案", "episodic", ["会议", "性能"]),
        ("Python 是一种解释型编程语言", "semantic", ["技术", "Python"]),
        ("React 是一个前端框架", "semantic", ["技术", "前端"]),
    ]

    for content, mtype, tags in memories_to_add:
        mem = client.memory.add(
            content=content,
            memory_type=mtype,
            tags=tags,
        )
        print(f"  + {content[:40]}... (ID: {mem['id']})")

    print()

    # 列出记忆
    print("[2/4] 列出记忆...")
    mem_list = client.memory.list(limit=10)
    print(f"  总数: {mem_list['total']}")
    for mem in mem_list["items"][:3]:
        print(f"    • [{mem['type']}] {mem['content'][:40]}...")

    print()

    # 搜索记忆
    print("[3/4] 搜索记忆...")
    query = "Python 编程"
    print(f"  查询: '{query}'")
    search_result = client.memory.search(query)
    print(f"  找到 {search_result['total']} 条相关记忆")
    for item in search_result["results"]:
        score_bar = "█" * int(item.get("score", 0) * 20)
        print(f"    [{score_bar}] {item['content'][:50]}...")

    print()

    # 获取详情
    print("[4/4] 获取记忆详情...")
    if mem_list["items"]:
        first_id = mem_list["items"][0]["id"]
        detail = client.memory.get(first_id)
        if detail:
            print(f"  ID: {detail['id']}")
            print(f"  内容: {detail['content']}")
            print(f"  类型: {detail['type']}")
            print(f"  标签: {', '.join(detail['tags'])}")

    print()


# ======================================================================
# 示例 4: 知识库操作
# ======================================================================

def demo_knowledge_operations(client: LiuHaoClient):
    """
    演示知识库相关操作。
    """
    print("=" * 60)
    print("  示例 4：知识库操作")
    print("=" * 60)
    print()

    # 添加文档
    print("[1/3] 添加文档到知识库...")
    docs = [
        ("产品使用手册", "本产品是一个 AI 助手平台，支持多智能体协作...",
         "产品文档", ["使用指南", "入门"]),
        ("API 参考", "LiuHao AI OS 提供 RESTful API 接口...",
         "开发者文档", ["API", "开发"]),
        ("常见问题 FAQ", "Q: 如何重置密码？A: 在登录页点击忘记密码...",
         "帮助中心", ["FAQ", "常见问题"]),
    ]

    for title, content, source, tags in docs:
        doc = client.knowledge.add_document(
            title=title,
            content=content,
            source=source,
            tags=tags,
        )
        print(f"  + {title} ({doc['chunk_count']} 个分块)")

    print()

    # 列出文档
    print("[2/3] 列出知识库文档...")
    doc_list = client.knowledge.list_documents()
    print(f"  总文档数: {doc_list['total']}")
    for doc in doc_list["items"]:
        print(f"    • {doc['title']} ({doc['source']})")

    print()

    # 语义搜索
    print("[3/3] 语义搜索知识库...")
    query = "怎么使用这个产品？"
    print(f"  查询: '{query}'")

    search_result = client.knowledge.search(query, limit=3)
    print(f"  找到 {search_result['total']} 个相关文档")
    print()

    for i, item in enumerate(search_result["results"], 1):
        score = item.get("score", 0)
        bar = "█" * int(score * 20)
        print(f"  {i}. [{bar}] {score:.2f} - {item['document']['title']}")
        print(f"     {item.get('snippet', '')[:60]}...")

    print()


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有 SDK 基础示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - Python SDK 基础使用")
    print("█" * 60)

    try:
        # 示例 1: 初始化
        client = demo_client_init()

        # 示例 2: Agent 操作
        demo_agent_operations(client)

        # 示例 3: 记忆操作
        demo_memory_operations(client)

        # 示例 4: 知识库操作
        demo_knowledge_operations(client)

        print("=" * 60)
        print("  Python SDK 基础功能总结")
        print("=" * 60)
        print("""
  ✓ 客户端初始化（API Key / 环境变量）
  ✓ Agent: 创建、列表、执行、状态、删除
  ✓ 记忆: 添加、搜索、列表、详情、删除
  ✓ 知识库: 添加文档、语义搜索、列表
  ✓ 分页和筛选
  ✓ 统一的错误处理
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    import time
    main()
