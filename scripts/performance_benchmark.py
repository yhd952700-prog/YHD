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
    """基准测试: MCP服务器响应时间"""
    print("\n" + "=" * 60)
    print("性能基准测试: MCP服务器响应时间")
    print("=" * 60)
    
    import json
    from src.mcp_adapter import MCPAdapter
    
    try:
        adapter = MCPAdapter()
        
        # 测试每个服务器的基本可达性
        servers = adapter.list_servers()
        print(f"可用MCP服务器: {len(servers)}")
        
        for server_name in servers[:3]:  # 只测试前3个
            start = time.time()
            try:
                # 尝试列出工具
                tools = adapter.list_tools(server_name)
                elapsed = time.time() - start
                print(f"  {server_name}: {elapsed*1000:.2f} ms, 工具数: {len(tools)}")
            except Exception as e:
                elapsed = time.time() - start
                print(f"  {server_name}: {elapsed*1000:.2f} ms, 错误: {str(e)[:30]}")
    except Exception as e:
        # 此前该 try 块没有 except/finally，文件无法通过语法解析（SyntaxError），
        # 因而整个脚本从未可执行。补上兜底，使 `compileall` 能真实反映仓库状态。
        print(f"MCP适配器不可用: {str(e)[:60]}")
        servers = []

    print("MCP基准测试完成")
    
    return {"servers_tested": len(servers) if 'servers' in dir() else 0}
    
    # 注意：由于Docker不可用，这里仅作演示
    return {"servers_tested": 0, "note": "Docker not available for MCP testing"}

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
