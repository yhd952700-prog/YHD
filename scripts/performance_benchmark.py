"""性能基准测试脚本 for LiuHao AI OS.

测试项目:
1. Provider生成延迟
2. Token处理吞吐量
3. 内存使用情况
4. MCP服务器响应时间
"""

import os
import sys
import time
import json
import asyncio
import argparse
from datetime import datetime

project_dir = r'D:\LiuHao-AI-OS'
sys.path.insert(0, project_dir)

def benchmark_provider_generation():
    """基准测试: Provider生成延迟"""
    print("=" * 60)
    print("性能基准测试: Provider生成延迟")
    print("=" * 60)
    
    from src.ai.providers import get_provider
    import resource
    
    # Warmup
    p = get_provider()
    p.generate("Hello, how are you?")
    
    # 正式测试
    start_time = time.time()
    iterations = 10
    
    for i in range(iterations):
        result = p.generate(f"Test message {i}", model="gpt-4o-mini")
    
    elapsed = time.time() - start_time
    avg_time = elapsed / iterations * 1000  # 毫秒
    
    print(f"迭代次数: {iterations}")
    print(f"总耗时: {elapsed:.3f} 秒")
    print(f"平均延迟: {avg_time:.2f} ms")
    print(f"每token估算时间: N/A (需计算实际token数)")
    
    # 收集内存信息
    mem_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = p.generate("Memory test message")
    mem_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mem_used = mem_after - mem_before
    
    print(f"内存使用峰值: {mem_used} KB")
    
    return {
        'avg_latency_ms': avg_time,
        'memory_peak_kb': mem_used,
        'iterations': iterations
    }

def benchmark_mcp_servers():
    """基准测试: MCP服务器响应时间。

    ⚠️ 2026-09-13 重写。原实现有三处运行期错误，导致本函数**永远**走兜底分支
    （打印「MCP适配器不可用」并返回 0），从未真正测过任何东西：
      1. import 路径 `src.mcp_adapter` 不存在 —— 真实路径是
         `src/adapters/mcp/mcp_adapter.py`；
      2. 调用了 MCPAdapter 上并不存在的 `list_servers()`（真实 API 是 `servers` 字典）；
      3. `list_tools()` 是 async，未 await 就对协程取 len()。
    另有 `return` 之后的不可达死代码（含一句重复 return）。

    同时：MCP 走 STDIO 传输会**直接执行**配置里的 command 字符串（即便连接失败命令
    也已执行），且该适配器尚未接入运行时（MCP_WIRING_STATUS）。因此在「白名单命令 /
    强制沙箱 / 走 Policy 执法链」三项前置条件满足前，本函数不发起任何真实连接。
    """
    print("\n" + "=" * 60)
    print("性能基准测试: MCP服务器响应时间")
    print("=" * 60)

    from src.adapters.mcp.mcp_adapter import MCPAdapter, MCP_WIRING_STATUS

    adapter = MCPAdapter()
    adapter.load_config()
    enabled = [name for name, cfg in adapter.servers.items() if cfg.enabled]

    print(f"接线状态: MCP_WIRING_STATUS={MCP_WIRING_STATUS!r}")
    print(f"配置内服务器: {len(adapter.servers)} 个（启用 {len(enabled)}）")
    for name in enabled[:3]:
        print(f"  - {name}")

    if MCP_WIRING_STATUS != "wired":
        print("未接线：跳过真实连接（三项安全前置条件未满足）")
        return {
            "servers_tested": 0,
            "servers_configured": len(adapter.servers),
            "wiring_status": MCP_WIRING_STATUS,
            "note": "未接线，跳过真实探测",
        }

    async def _probe(names):
        rows = []
        for server_name in names[:3]:  # 只测试前3个
            start = time.time()
            if not await adapter.connect_server(server_name):
                rows.append((server_name, (time.time() - start) * 1000, None))
                continue
            tools = await adapter.list_tools(server_name)
            rows.append((server_name, (time.time() - start) * 1000, len(tools or [])))
            await adapter.disconnect_server(server_name)
        return rows

    results = asyncio.run(_probe(enabled))
    for server_name, elapsed_ms, tool_count in results:
        tail = f"工具数: {tool_count}" if tool_count is not None else "连接失败"
        print(f"  {server_name}: {elapsed_ms:.2f} ms, {tail}")

    print("MCP基准测试完成")
    return {"servers_tested": len(results), "wiring_status": MCP_WIRING_STATUS}

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="LiuHao AI OS 性能基准测试")
    parser.add_argument('--provider', action='store_true', 
                        help='测试Provider生成延迟')
    parser.add_argument('--mcp', action='store_true',
                        help='测试MCP服务器响应时间')
    parser.add_argument('--all', action='store_true',
                        help='运行所有测试')
    
    args = parser.parse_args()
    
    results = {}
    
    if args.all or args.provider:
        results['provider'] = benchmark_provider_generation()
    
    if args.all or args.mcp:
        results['mcp'] = benchmark_mcp_servers()
    
    # 汇总报告
    print("\n" + "=" * 60)
    print("性能基准测试汇总报告")
    print("=" * 60)
    for name, result in results.items():
        print(f"\n{name}: {result}")
    
    # 生成报告文件
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(project_dir, f"performance_report_{timestamp}.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n报告已保存至: {report_path}")

if __name__ == "__main__":
    main()
