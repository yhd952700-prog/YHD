"""
管道编排 (Pipeline Orchestration) - LiuHao AI OS 高级示例

本示例演示如何构建和编排数据处理管道：
1. 构建多阶段数据管道
2. 分支与合并
3. 错误处理与重试
4. 监控与进度追踪

运行方式：
    python examples/advanced/pipeline_orchestration.py
"""

import sys
import os
import time
import uuid
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.pipelines.etl import (
    ETLConfig,
    ETLPipelineStatus,
    ETLStrategy,
)


# ======================================================================
# 管道阶段类型
# ======================================================================

class StageStatus(str, Enum):
    """管道阶段状态。"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class StageType(str, Enum):
    """管道阶段类型。"""
    SOURCE = "source"           # 数据源
    TRANSFORM = "transform"     # 转换
    FILTER = "filter"           # 过滤
    AGGREGATE = "aggregate"     # 聚合
    JOIN = "join"               # 合并
    BRANCH = "branch"           # 分支
    SINK = "sink"               # 输出
    CUSTOM = "custom"           # 自定义


@dataclass
class PipelineStage:
    """管道中的一个处理阶段。"""
    stage_id: str
    name: str
    stage_type: StageType
    status: StageStatus = StageStatus.PENDING
    input_data: Optional[Any] = None
    output_data: Optional[Any] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    retries: int = 0
    max_retries: int = 3
    depends_on: List[str] = field(default_factory=list)  # 依赖的阶段 ID
    processor: Optional[Callable] = None  # 处理函数
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """管道执行结果。"""
    pipeline_name: str
    success: bool
    total_stages: int
    completed_stages: int
    failed_stages: int
    total_duration_seconds: float
    stages: List[PipelineStage]
    final_output: Optional[Any] = None
    error_message: Optional[str] = None


# ======================================================================
# 管道编排器
# ======================================================================

class PipelineOrchestrator:
    """
    管道编排器 - 管理和执行多阶段数据处理管道。

    功能：
    - 有向无环图 (DAG) 执行
    - 依赖管理
    - 分支与合并
    - 错误处理与重试
    - 进度监控
    """

    def __init__(self, name: str = "pipeline"):
        """
        初始化管道编排器。

        Args:
            name: 管道名称
        """
        self.name = name
        self.stages: Dict[str, PipelineStage] = {}
        self.execution_order: List[str] = []
        self.on_stage_start: Optional[Callable] = None
        self.on_stage_complete: Optional[Callable] = None
        self.on_stage_fail: Optional[Callable] = None
        self.on_pipeline_complete: Optional[Callable] = None

    def add_stage(self, name: str, stage_type: StageType,
                  processor: Callable,
                  depends_on: List[str] = None,
                  max_retries: int = 3) -> str:
        """
        添加一个处理阶段。

        Args:
            name: 阶段名称
            stage_type: 阶段类型
            processor: 处理函数 (接收数据，返回处理后的数据)
            depends_on: 依赖的阶段名称列表
            max_retries: 最大重试次数

        Returns:
            阶段 ID
        """
        stage_id = f"stage-{uuid.uuid4().hex[:8]}"
        # 将名称转换为 ID（用于依赖引用）
        stage = PipelineStage(
            stage_id=stage_id,
            name=name,
            stage_type=stage_type,
            processor=processor,
            depends_on=depends_on or [],
            max_retries=max_retries,
        )
        self.stages[stage_id] = stage
        self.stages[name] = stage  # 也用名称作为键，方便引用
        return stage_id

    def _topological_sort(self) -> List[str]:
        """
        对阶段进行拓扑排序，确定执行顺序。

        使用 Kahn 算法进行拓扑排序。
        """
        # 构建入度表
        in_degree = {}
        for stage in self.stages.values():
            if stage.stage_id not in in_degree:
                in_degree[stage.stage_id] = 0

        for stage in self.stages.values():
            for dep_name in stage.depends_on:
                dep_stage = self.stages.get(dep_name)
                if dep_stage:
                    in_degree[stage.stage_id] = in_degree.get(stage.stage_id, 0) + 1

        # 找到所有入度为 0 的节点
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            current = queue.pop(0)
            result.append(current)

            # 找到所有依赖当前阶段的阶段
            for stage in self.stages.values():
                deps_ids = [
                    self.stages[d].stage_id for d in stage.depends_on
                    if d in self.stages
                ]
                if current in deps_ids:
                    in_degree[stage.stage_id] -= 1
                    if in_degree[stage.stage_id] == 0:
                        queue.append(stage.stage_id)

        # 检查是否有环
        if len(result) != len(set(in_degree.keys())):
            raise ValueError("管道中存在循环依赖，无法执行")

        return result

    def _get_stage_input(self, stage: PipelineStage) -> Any:
        """获取阶段的输入数据（合并所有依赖的输出）。"""
        if not stage.depends_on:
            return stage.input_data

        inputs = {}
        for dep_name in stage.depends_on:
            dep_stage = self.stages.get(dep_name)
            if dep_stage and dep_stage.output_data is not None:
                inputs[dep_name] = dep_stage.output_data

        # 如果只有一个依赖，直接返回其输出
        if len(inputs) == 1:
            return list(inputs.values())[0]

        # 多个依赖，返回字典
        return inputs

    def execute(self, initial_data: Any = None) -> PipelineResult:
        """
        执行整个管道。

        Args:
            initial_data: 初始输入数据

        Returns:
            管道执行结果
        """
        start_time = time.time()

        # 确定执行顺序
        self.execution_order = self._topological_sort()

        print(f"\n🚀 开始执行管道: {self.name}")
        print(f"   阶段数量: {len(self.execution_order)}")
        print(f"   执行顺序: {' → '.join(self._get_stage_names())}")
        print()

        # 设置初始数据到源阶段
        for stage_id in self.execution_order:
            stage = self._get_stage_by_id(stage_id)
            if stage and not stage.depends_on:
                stage.input_data = initial_data
                break

        # 逐阶段执行
        completed = 0
        failed = 0

        for stage_id in self.execution_order:
            stage = self._get_stage_by_id(stage_id)
            if not stage:
                continue

            # 检查依赖是否都成功
            deps_ok = all(
                self.stages[d].status == StageStatus.COMPLETED
                for d in stage.depends_on if d in self.stages
            )

            if not deps_ok and stage.depends_on:
                stage.status = StageStatus.SKIPPED
                print(f"  ⏭️  跳过 [{stage.name}] - 依赖阶段未成功")
                continue

            # 执行阶段（含重试逻辑）
            success = self._execute_stage_with_retry(stage)

            if success:
                completed += 1
            else:
                failed += 1
                # 对于关键阶段失败，终止管道
                if stage.stage_type in [StageType.SOURCE, StageType.TRANSFORM]:
                    print(f"\n❌ 关键阶段 [{stage.name}] 失败，管道终止")
                    break

        total_duration = time.time() - start_time
        success = failed == 0

        # 找到最终输出（最后一个完成的阶段）
        final_output = None
        for stage_id in reversed(self.execution_order):
            stage = self._get_stage_by_id(stage_id)
            if stage and stage.status == StageStatus.COMPLETED:
                final_output = stage.output_data
                break

        result = PipelineResult(
            pipeline_name=self.name,
            success=success,
            total_stages=len(self.execution_order),
            completed_stages=completed,
            failed_stages=failed,
            total_duration_seconds=total_duration,
            stages=[self._get_stage_by_id(sid) for sid in self.execution_order
                    if self._get_stage_by_id(sid)],
            final_output=final_output,
            error_message=None if success else f"{failed} 个阶段失败",
        )

        if self.on_pipeline_complete:
            self.on_pipeline_complete(result)

        return result

    def _get_stage_by_id(self, stage_id: str) -> Optional[PipelineStage]:
        """通过 ID 获取阶段。"""
        return self.stages.get(stage_id)

    def _get_stage_names(self) -> List[str]:
        """获取按执行顺序排列的阶段名称。"""
        names = []
        for sid in self.execution_order:
            stage = self._get_stage_by_id(sid)
            if stage:
                names.append(stage.name)
        return names

    def _execute_stage_with_retry(self, stage: PipelineStage) -> bool:
        """
        执行阶段，包含重试逻辑。

        Args:
            stage: 要执行的阶段

        Returns:
            是否成功
        """
        stage.status = StageStatus.RUNNING
        stage_start = time.time()

        if self.on_stage_start:
            self.on_stage_start(stage)

        print(f"  ▶️  执行 [{stage.name}] ({stage.stage_type.value})...")

        for attempt in range(stage.max_retries + 1):
            try:
                # 获取输入
                input_data = self._get_stage_input(stage)

                # 执行处理
                if stage.processor:
                    output = stage.processor(input_data, stage.metadata)
                else:
                    output = input_data

                stage.output_data = output
                stage.status = StageStatus.COMPLETED
                stage.duration_seconds = time.time() - stage_start

                # 统计信息
                input_size = self._estimate_size(input_data)
                output_size = self._estimate_size(output)
                print(f"     ✅ 完成 (耗时 {stage.duration_seconds:.2f}s, "
                      f"{input_size} → {output_size})")

                if self.on_stage_complete:
                    self.on_stage_complete(stage)

                return True

            except Exception as e:
                stage.retries = attempt + 1
                stage.error = str(e)

                if attempt < stage.max_retries:
                    stage.status = StageStatus.RETRYING
                    wait_time = 2 ** attempt  # 指数退避
                    print(f"     ⚠️  第 {attempt + 1} 次失败: {e}")
                    print(f"        {wait_time}s 后重试 ({stage.retries}/{stage.max_retries})...")
                    time.sleep(wait_time * 0.1)  # 演示时缩短等待
                else:
                    stage.status = StageStatus.FAILED
                    stage.duration_seconds = time.time() - stage_start
                    print(f"     ❌ 失败: {e}")
                    print(f"        已重试 {stage.max_retries} 次，全部失败")

                    if self.on_stage_fail:
                        self.on_stage_fail(stage, e)

                    return False

        return False

    def _estimate_size(self, data: Any) -> str:
        """估算数据大小（用于显示）。"""
        if data is None:
            return "0"
        if isinstance(data, list):
            return f"{len(data)} 条"
        if isinstance(data, dict):
            return f"{len(data)} 项"
        if isinstance(data, str):
            return f"{len(data)} 字符"
        return "?"

    def get_progress(self) -> Dict[str, Any]:
        """获取管道执行进度。"""
        total = len(self.stages)
        completed = sum(
            1 for s in self.stages.values()
            if s.status == StageStatus.COMPLETED
        )
        return {
            "total": total,
            "completed": completed,
            "progress": completed / total if total > 0 else 0,
        }


# ======================================================================
# 示例 1: 线性管道
# ======================================================================

def demo_linear_pipeline():
    """
    演示简单的线性数据处理管道。

    流程：数据提取 → 清洗 → 转换 → 聚合 → 输出
    """
    print("=" * 60)
    print("  示例 1：线性数据处理管道")
    print("=" * 60)
    print()

    pipeline = PipelineOrchestrator(name="数据处理管道")

    # ---- 定义各阶段处理函数 ----

    def extract_data(data, metadata):
        """阶段 1: 数据提取"""
        # 模拟从数据源提取数据
        raw_data = [
            {"id": 1, "name": "Alice", "age": "25", "score": "95.5"},
            {"id": 2, "name": "Bob", "age": "30", "score": "87.0"},
            {"id": 3, "name": "Charlie", "age": "28", "score": "92.3"},
            {"id": 4, "name": "Diana", "age": "35", "score": "78.9"},
            {"id": 5, "name": "Eve", "age": "22", "score": "88.1"},
        ]
        metadata["records"] = len(raw_data)
        return raw_data

    def clean_data(data, metadata):
        """阶段 2: 数据清洗"""
        cleaned = []
        removed = 0
        for record in data:
            # 类型转换和验证
            try:
                record["age"] = int(record["age"])
                record["score"] = float(record["score"])
                # 验证范围
                if 0 <= record["age"] <= 120 and 0 <= record["score"] <= 100:
                    cleaned.append(record)
                else:
                    removed += 1
            except (ValueError, TypeError):
                removed += 1

        metadata["cleaned_records"] = len(cleaned)
        metadata["removed_records"] = removed
        return cleaned

    def transform_data(data, metadata):
        """阶段 3: 数据转换"""
        transformed = []
        for record in data:
            new_record = {
                **record,
                "age_group": "young" if record["age"] < 30 else "adult",
                "grade": (
                    "A" if record["score"] >= 90
                    else "B" if record["score"] >= 80
                    else "C" if record["score"] >= 70
                    else "D"
                ),
                "passed": record["score"] >= 60,
            }
            transformed.append(new_record)
        metadata["transformed_records"] = len(transformed)
        return transformed

    def aggregate_data(data, metadata):
        """阶段 4: 数据聚合"""
        total_score = sum(r["score"] for r in data)
        avg_score = total_score / len(data) if data else 0

        grade_counts = {}
        age_group_counts = {}
        for record in data:
            grade_counts[record["grade"]] = grade_counts.get(record["grade"], 0) + 1
            age_group_counts[record["age_group"]] = age_group_counts.get(
                record["age_group"], 0) + 1

        aggregation = {
            "total_records": len(data),
            "average_score": round(avg_score, 2),
            "max_score": max(r["score"] for r in data),
            "min_score": min(r["score"] for r in data),
            "grade_distribution": grade_counts,
            "age_group_distribution": age_group_counts,
            "pass_rate": sum(1 for r in data if r["passed"]) / len(data),
        }
        metadata["aggregation"] = aggregation
        return aggregation

    def format_report(data, metadata):
        """阶段 5: 生成报告"""
        report = f"""
📊 数据分析报告
{'=' * 40}
总记录数: {data['total_records']}
平均分: {data['average_score']}
最高分: {data['max_score']}
最低分: {data['min_score']}
通过率: {data['pass_rate']:.1%}

成绩分布:
"""
        for grade, count in sorted(data['grade_distribution'].items()):
            bar = "█" * count
            report += f"  {grade}: {bar} ({count})\n"

        report += f"\n年龄组分布:\n"
        for group, count in data['age_group_distribution'].items():
            bar = "█" * count
            report += f"  {group}: {bar} ({count})\n"

        metadata["report_length"] = len(report)
        return report

    # ---- 构建管道 ----
    pipeline.add_stage("数据提取", StageType.SOURCE, extract_data)
    pipeline.add_stage("数据清洗", StageType.TRANSFORM, clean_data,
                       depends_on=["数据提取"])
    pipeline.add_stage("数据转换", StageType.TRANSFORM, transform_data,
                       depends_on=["数据清洗"])
    pipeline.add_stage("数据聚合", StageType.AGGREGATE, aggregate_data,
                       depends_on=["数据转换"])
    pipeline.add_stage("生成报告", StageType.SINK, format_report,
                       depends_on=["数据聚合"])

    # ---- 执行管道 ----
    result = pipeline.execute()

    # ---- 输出结果 ----
    print()
    print("=" * 50)
    print("  📄 管道执行报告")
    print("=" * 50)
    print(f"\n管道名称: {result.pipeline_name}")
    print(f"执行状态: {'✅ 成功' if result.success else '❌ 失败'}")
    print(f"完成阶段: {result.completed_stages}/{result.total_stages}")
    print(f"失败阶段: {result.failed_stages}")
    print(f"总耗时: {result.total_duration_seconds:.2f}s")

    if result.final_output:
        print(f"\n最终输出:")
        print(result.final_output)

    print()
    return result


# ======================================================================
# 示例 2: 分支与合并管道
# ======================================================================

def demo_branch_merge_pipeline():
    """
    演示带有分支和合并的复杂管道。

    流程：
           ┌→ 分支A处理 ─┐
    数据源 ─┤              ├→ 合并 → 输出
           └→ 分支B处理 ─┘
    """
    print("=" * 60)
    print("  示例 2：分支与合并管道")
    print("=" * 60)
    print()

    pipeline = PipelineOrchestrator(name="分支合并管道")

    # 处理函数
    def source_data(data, metadata):
        """数据源：生成原始数据"""
        return {
            "numbers": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "labels": ["a", "b", "c", "d", "e"],
        }

    def branch_a(data, metadata):
        """分支 A：数值统计"""
        numbers = data["numbers"]
        result = {
            "sum": sum(numbers),
            "average": sum(numbers) / len(numbers),
            "max": max(numbers),
            "min": min(numbers),
            "count": len(numbers),
        }
        metadata["branch_a_result"] = result
        return result

    def branch_b(data, metadata):
        """分支 B：文本统计"""
        labels = data["labels"]
        result = {
            "total_chars": sum(len(l) for l in labels),
            "unique_chars": len(set("".join(labels))),
            "total_items": len(labels),
            "sorted_labels": sorted(labels),
        }
        metadata["branch_b_result"] = result
        return result

    def merge_results(data, metadata):
        """合并两个分支的结果"""
        # data 是一个字典，包含所有依赖的输出
        branch_a_result = data.get("分支A_数值统计", {})
        branch_b_result = data.get("分支B_文本统计", {})

        merged = {
            "numeric_summary": branch_a_result,
            "text_summary": branch_b_result,
            "combined": {
                "total_data_points": (
                    branch_a_result.get("count", 0)
                    + branch_b_result.get("total_items", 0)
                ),
                "branches_processed": 2,
            }
        }
        metadata["merged"] = True
        return merged

    def final_output(data, metadata):
        """格式化最终输出"""
        output = f"""
🔀 分支合并处理结果
{'=' * 40}

【数值分支结果】
  求和: {data['numeric_summary'].get('sum', 0)}
  平均: {data['numeric_summary'].get('average', 0)}
  最大: {data['numeric_summary'].get('max', 0)}
  最小: {data['numeric_summary'].get('min', 0)}
  数量: {data['numeric_summary'].get('count', 0)}

【文本分支结果】
  字符总数: {data['text_summary'].get('total_chars', 0)}
  唯一字符: {data['text_summary'].get('unique_chars', 0)}
  项目数量: {data['text_summary'].get('total_items', 0)}

【合并统计】
  总数据点: {data['combined']['total_data_points']}
  处理分支: {data['combined']['branches_processed']}
"""
        return output

    # 构建管道
    pipeline.add_stage("数据源", StageType.SOURCE, source_data)
    pipeline.add_stage("分支A_数值统计", StageType.BRANCH, branch_a,
                       depends_on=["数据源"])
    pipeline.add_stage("分支B_文本统计", StageType.BRANCH, branch_b,
                       depends_on=["数据源"])
    pipeline.add_stage("结果合并", StageType.JOIN, merge_results,
                       depends_on=["分支A_数值统计", "分支B_文本统计"])
    pipeline.add_stage("最终输出", StageType.SINK, final_output,
                       depends_on=["结果合并"])

    # 执行
    result = pipeline.execute()

    print()
    print("=" * 50)
    print("  📄 执行结果")
    print("=" * 50)
    print(f"\n状态: {'✅ 成功' if result.success else '❌ 失败'}")
    print(f"耗时: {result.total_duration_seconds:.2f}s")

    if result.final_output:
        print(result.final_output)

    print()
    return result


# ======================================================================
# 示例 3: 错误处理与重试
# ======================================================================

def demo_error_handling():
    """
    演示管道的错误处理和重试机制。

    包括：
    - 阶段失败重试
    - 指数退避策略
    - 失败后的跳过机制
    """
    print("=" * 60)
    print("  示例 3：错误处理与重试机制")
    print("=" * 60)
    print()

    pipeline = PipelineOrchestrator(name="容错测试管道")

    call_count = {"flaky": 0, "always_fail": 0}

    def stable_stage(data, metadata):
        """稳定的阶段，总是成功"""
        return {"status": "ok", "data": data}

    def flaky_stage(data, metadata):
        """不稳定的阶段，前两次失败，第三次成功"""
        call_count["flaky"] += 1
        if call_count["flaky"] < 3:
            raise RuntimeError(f"临时故障 (第{call_count['flaky']}次尝试)")
        return {"status": "recovered", "attempts": call_count["flaky"]}

    def always_fail_stage(data, metadata):
        """总是失败的阶段"""
        call_count["always_fail"] += 1
        raise ValueError("致命错误：无法处理数据")

    def fallback_stage(data, metadata):
        """降级处理阶段"""
        return {"status": "degraded", "note": "使用降级方案"}

    # 构建管道
    pipeline.add_stage("稳定阶段", StageType.TRANSFORM, stable_stage)
    pipeline.add_stage("易故障阶段", StageType.TRANSFORM, flaky_stage,
                       depends_on=["稳定阶段"], max_retries=3)
    pipeline.add_stage("降级阶段", StageType.TRANSFORM, fallback_stage,
                       depends_on=["稳定阶段"])  # 并行的降级路径

    # 执行
    result = pipeline.execute({"test": "data"})

    print()
    print("=" * 50)
    print("  错误处理说明")
    print("=" * 50)
    print("""
  重试策略:
    • 最大重试次数: 3 次
    • 退避策略: 指数退避 (2^n 秒)
    • 失败判定: 异常抛出即失败

  容错模式:
    • 自动重试 - 暂时性故障自动恢复
    • 降级路径 - 主路径失败时使用备用方案
    • 优雅跳过 - 非关键阶段失败不影响整体
""")

    print(f"  实际调用次数:")
    print(f"    易故障阶段: {call_count['flaky']} 次 (前2次失败，第3次成功)")

    print()
    return result


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行所有管道编排示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 管道编排 (Pipeline Orchestration)")
    print("█" * 60)

    try:
        # 示例 1: 线性管道
        demo_linear_pipeline()

        # 示例 2: 分支合并
        demo_branch_merge_pipeline()

        # 示例 3: 错误处理
        demo_error_handling()

        print("=" * 60)
        print("  管道编排功能总结")
        print("=" * 60)
        print("""
  ✓ 多阶段管道构建
  ✓ DAG 依赖管理
  ✓ 拓扑排序执行
  ✓ 分支与合并
  ✓ 自动重试（指数退避）
  ✓ 错误传播与降级
  ✓ 进度实时监控
  ✓ 性能统计与报告
""")

    except Exception as e:
        print(f"\n❌ 运行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
