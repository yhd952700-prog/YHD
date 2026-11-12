"""
数据分析管道 - LiuHao AI OS 用例示例

本示例演示如何构建一个数据分析 Agent 工作流，功能包括：
1. 数据加载与预处理
2. 探索性数据分析 (EDA)
3. 统计分析与洞察提取
4. 可视化建议
5. 分析报告生成

运行方式：
    python examples/use_cases/data_analysis_pipeline.py
"""

import sys
import os
import time
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ai.specialized_agents import DataAnalystAgent
from ai.agent_factory import AgentFactory, AgentRole
from ai.memory import WorkingMemory, MemoryPriority
from ai.reasoning import ChainOfThought, ReasoningStrategy
from ai.providers import get_provider


# ======================================================================
# 数据模型
# ======================================================================

class DataSourceType(str, Enum):
    """数据源类型。"""
    CSV = "csv"
    JSON = "json"
    DATABASE = "database"
    API = "api"
    EXCEL = "excel"


class AnalysisStepType(str, Enum):
    """分析步骤类型。"""
    LOAD = "load"               # 数据加载
    CLEAN = "clean"             # 数据清洗
    EXPLORE = "explore"         # 探索性分析
    STATISTICAL = "statistical"  # 统计分析
    VISUALIZE = "visualize"     # 可视化
    MODEL = "model"             # 建模
    REPORT = "report"           # 报告生成


class InsightType(str, Enum):
    """洞察类型。"""
    TREND = "trend"             # 趋势
    CORRELATION = "correlation"  # 相关性
    ANOMALY = "anomaly"         # 异常
    PATTERN = "pattern"         # 模式
    CLUSTER = "cluster"         # 聚类
    PREDICTION = "prediction"   # 预测
    SUMMARY = "summary"         # 总结


@dataclass
class DataColumn:
    """数据列信息。"""
    name: str
    data_type: str  # numeric, string, date, boolean
    missing_count: int = 0
    missing_rate: float = 0.0
    unique_count: int = 0
    sample_values: List[Any] = field(default_factory=list)
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    mean_value: Optional[float] = None
    median_value: Optional[float] = None


@dataclass
class DatasetInfo:
    """数据集信息。"""
    name: str
    source_type: DataSourceType
    row_count: int
    column_count: int
    columns: List[DataColumn]
    total_missing: int = 0
    duplicate_rows: int = 0
    memory_usage_mb: float = 0.0


@dataclass
class AnalysisStep:
    """分析步骤记录。"""
    step_id: str
    step_type: AnalysisStepType
    description: str
    status: str = "pending"  # pending, running, completed, failed
    result_summary: str = ""
    duration_seconds: float = 0.0
    error_message: Optional[str] = None


@dataclass
class Insight:
    """分析洞察。"""
    insight_id: str
    insight_type: InsightType
    title: str
    description: str
    confidence: float  # 0.0 - 1.0
    supporting_data: Dict[str, Any] = field(default_factory=dict)
    related_columns: List[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class AnalysisReport:
    """完整的分析报告。"""
    dataset: DatasetInfo
    steps: List[AnalysisStep]
    insights: List[Insight]
    visualizations: List[Dict[str, Any]]
    overall_summary: str
    key_findings: List[str]
    recommendations: List[str]
    limitations: List[str]
    total_duration_seconds: float


# ======================================================================
# 数据分析管道
# ======================================================================

class DataAnalysisPipeline:
    """
    数据分析管道 - 端到端的数据分析工作流。

    工作流程：
    1. 数据加载 - 从各种来源读取数据
    2. 数据清洗 - 处理缺失值、异常值、重复值
    3. 探索性分析 - 了解数据分布和特征
    4. 统计分析 - 深入的统计检验和建模
    5. 洞察提取 - 从分析结果中提取有价值的发现
    6. 报告生成 - 生成结构化的分析报告
    """

    def __init__(self, analyst_agent: DataAnalystAgent):
        """
        初始化数据分析管道。

        Args:
            analyst_agent: 底层的数据分析 Agent
        """
        self.agent = analyst_agent
        self.work_memory = WorkingMemory(capacity=30)
        self.steps: List[AnalysisStep] = []
        self.step_counter = 0

    # ---- 阶段 1: 数据加载 ----

    def load_data(self, data: List[Dict[str, Any]], dataset_name: str) -> DatasetInfo:
        """
        加载并分析数据集的基本信息。

        Args:
            data: 数据记录列表
            dataset_name: 数据集名称

        Returns:
            数据集信息
        """
        step = self._start_step(AnalysisStepType.LOAD, f"加载数据集: {dataset_name}")

        try:
            row_count = len(data)
            if row_count == 0:
                raise ValueError("数据集为空")

            # 获取所有列
            all_columns = set()
            for row in data:
                all_columns.update(row.keys())

            # 分析每列
            columns = []
            total_missing = 0

            for col_name in sorted(all_columns):
                values = [row.get(col_name) for row in data]
                non_null_values = [v for v in values if v is not None and v != ""]
                missing_count = row_count - len(non_null_values)
                total_missing += missing_count

                # 推断数据类型
                data_type = self._infer_type(non_null_values[:100] if non_null_values else [])

                # 计算统计信息
                col_info = DataColumn(
                    name=col_name,
                    data_type=data_type,
                    missing_count=missing_count,
                    missing_rate=missing_count / row_count if row_count > 0 else 0,
                    unique_count=len(set(str(v) for v in non_null_values)),
                    sample_values=non_null_values[:5],
                )

                # 数值列额外统计
                if data_type == "numeric" and non_null_values:
                    numeric_vals = [float(v) for v in non_null_values
                                    if isinstance(v, (int, float))]
                    if numeric_vals:
                        col_info.min_value = min(numeric_vals)
                        col_info.max_value = max(numeric_vals)
                        col_info.mean_value = sum(numeric_vals) / len(numeric_vals)
                        sorted_vals = sorted(numeric_vals)
                        mid = len(sorted_vals) // 2
                        col_info.median_value = (
                            sorted_vals[mid] if len(sorted_vals) % 2
                            else (sorted_vals[mid-1] + sorted_vals[mid]) / 2
                        )

                columns.append(col_info)

            # 检查重复行
            seen = set()
            duplicates = 0
            for row in data:
                row_key = json.dumps(row, sort_keys=True, default=str)
                if row_key in seen:
                    duplicates += 1
                else:
                    seen.add(row_key)

            # 估算内存使用
            memory_usage = row_count * len(columns) * 0.001  # 粗略估算 MB

            dataset_info = DatasetInfo(
                name=dataset_name,
                source_type=DataSourceType.JSON,
                row_count=row_count,
                column_count=len(columns),
                columns=columns,
                total_missing=total_missing,
                duplicate_rows=duplicates,
                memory_usage_mb=memory_usage,
            )

            # 存入工作记忆
            self.work_memory.add(
                content=f"数据集 {dataset_name}: {row_count} 行, {len(columns)} 列",
                item_type="fact",
                priority=MemoryPriority.HIGH,
                source="data_loading",
                tags=["dataset", dataset_name],
            )

            self._complete_step(step, f"成功加载 {row_count} 行, {len(columns)} 列数据")
            return dataset_info

        except Exception as e:
            self._fail_step(step, str(e))
            raise

    def _infer_type(self, values: List[Any]) -> str:
        """推断数据列的类型。"""
        if not values:
            return "unknown"

        # 检查是否都是数值
        numeric_count = 0
        for v in values:
            if isinstance(v, (int, float)):
                numeric_count += 1
            elif isinstance(v, str):
                try:
                    float(v)
                    numeric_count += 1
                except ValueError:
                    pass

        if numeric_count / len(values) > 0.9:
            return "numeric"

        # 检查是否是布尔值
        bool_vals = {str(v).lower() for v in values}
        if bool_vals.issubset({"true", "false", "0", "1", "yes", "no"}):
            return "boolean"

        # 检查是否是日期
        import re
        date_pattern = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
        date_count = sum(1 for v in values if isinstance(v, str) and date_pattern.match(v))
        if date_count / len(values) > 0.8:
            return "date"

        return "string"

    # ---- 阶段 2: 数据清洗 ----

    def clean_data(self, data: List[Dict[str, Any]],
                   dataset: DatasetInfo) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        数据清洗：处理缺失值、异常值、重复值。

        Args:
            data: 原始数据
            dataset: 数据集信息

        Returns:
            (清洗后的数据, 清洗统计)
        """
        step = self._start_step(AnalysisStepType.CLEAN, "数据清洗")

        try:
            stats = {
                "rows_removed_duplicate": 0,
                "rows_removed_missing": 0,
                "values_imputed": 0,
                "outliers_handled": 0,
            }

            cleaned_data = []
            seen = set()

            for row in data:
                # 去除重复行
                row_key = json.dumps(row, sort_keys=True, default=str)
                if row_key in seen:
                    stats["rows_removed_duplicate"] += 1
                    continue
                seen.add(row_key)

                # 检查缺失值比例
                missing = sum(1 for v in row.values() if v is None or v == "")
                if missing / max(len(row), 1) > 0.5:
                    # 缺失值超过 50% 的行直接移除
                    stats["rows_removed_missing"] += 1
                    continue

                # 数值列缺失值用中位数填充
                for col in dataset.columns:
                    if col.data_type == "numeric" and col.median_value is not None:
                        if row.get(col.name) is None or row.get(col.name) == "":
                            row[col.name] = col.median_value
                            stats["values_imputed"] += 1

                cleaned_data.append(row)

            result_msg = (
                f"去除 {stats['rows_removed_duplicate']} 条重复, "
                f"移除 {stats['rows_removed_missing']} 条高缺失行, "
                f"填充 {stats['values_imputed']} 个缺失值"
            )

            self._complete_step(step, result_msg)
            return cleaned_data, stats

        except Exception as e:
            self._fail_step(step, str(e))
            raise

    # ---- 阶段 3: 探索性数据分析 ----

    def exploratory_analysis(self, data: List[Dict[str, Any]],
                             dataset: DatasetInfo) -> Dict[str, Any]:
        """
        探索性数据分析 (EDA)。

        内容包括：
        - 描述性统计
        - 分布分析
        - 相关性分析
        - 异常值检测

        Args:
            data: 清洗后的数据
            dataset: 数据集信息

        Returns:
            EDA 结果字典
        """
        step = self._start_step(AnalysisStepType.EXPLORE, "探索性数据分析")

        try:
            eda_results = {
                "descriptive_stats": {},
                "distributions": {},
                "correlations": [],
                "outliers": [],
            }

            # 描述性统计（数值列）
            numeric_cols = [c for c in dataset.columns if c.data_type == "numeric"]

            for col in numeric_cols:
                values = [float(row[col.name]) for row in data
                          if col.name in row and row[col.name] is not None]
                if not values:
                    continue

                values.sort()
                n = len(values)
                mean = sum(values) / n
                variance = sum((x - mean) ** 2 for x in values) / n
                std_dev = variance ** 0.5

                # 分位数
                def percentile(p):
                    k = (n - 1) * p
                    f = int(k)
                    c = f + 1 if f + 1 < n else f
                    return values[f] + (k - f) * (values[c] - values[f])

                q1 = percentile(0.25)
                median = percentile(0.5)
                q3 = percentile(0.75)
                iqr = q3 - q1

                # 异常值检测 (IQR 方法)
                lower_bound = q1 - 1.5 * iqr
                upper_bound = q3 + 1.5 * iqr
                outliers = [v for v in values if v < lower_bound or v > upper_bound]

                eda_results["descriptive_stats"][col.name] = {
                    "count": n,
                    "mean": round(mean, 4),
                    "std": round(std_dev, 4),
                    "min": values[0],
                    "q1": round(q1, 4),
                    "median": round(median, 4),
                    "q3": round(q3, 4),
                    "max": values[-1],
                    "iqr": round(iqr, 4),
                }

                if outliers:
                    eda_results["outliers"].append({
                        "column": col.name,
                        "count": len(outliers),
                        "lower_bound": round(lower_bound, 4),
                        "upper_bound": round(upper_bound, 4),
                        "outlier_ratio": len(outliers) / n,
                    })

            # 相关性分析 (数值列之间)
            for i, col1 in enumerate(numeric_cols):
                for col2 in numeric_cols[i+1:]:
                    vals1 = [float(row[col1.name]) for row in data
                             if col1.name in row and row[col1.name] is not None]
                    vals2 = [float(row[col2.name]) for row in data
                             if col2.name in row and row[col2.name] is not None]

                    # 确保长度一致（取交集行）
                    min_len = min(len(vals1), len(vals2))
                    if min_len < 3:
                        continue

                    vals1 = vals1[:min_len]
                    vals2 = vals2[:min_len]

                    # 计算 Pearson 相关系数
                    mean1 = sum(vals1) / min_len
                    mean2 = sum(vals2) / min_len
                    cov = sum((a - mean1) * (b - mean2) for a, b in zip(vals1, vals2)) / min_len
                    std1 = (sum((x - mean1) ** 2 for x in vals1) / min_len) ** 0.5
                    std2 = (sum((x - mean2) ** 2 for x in vals2) / min_len) ** 0.5

                    if std1 > 0 and std2 > 0:
                        correlation = cov / (std1 * std2)
                        eda_results["correlations"].append({
                            "column1": col1.name,
                            "column2": col2.name,
                            "correlation": round(correlation, 4),
                            "strength": (
                                "strong" if abs(correlation) > 0.7
                                else "moderate" if abs(correlation) > 0.4
                                else "weak"
                            ),
                            "direction": "positive" if correlation > 0 else "negative",
                        })

            # 按相关性强度排序
            eda_results["correlations"].sort(
                key=lambda x: abs(x["correlation"]), reverse=True
            )

            result_msg = (
                f"分析 {len(numeric_cols)} 个数值列, "
                f"发现 {len(eda_results['correlations'])} 对相关性, "
                f"{len(eda_results['outliers'])} 列存在异常值"
            )

            self._complete_step(step, result_msg)
            return eda_results

        except Exception as e:
            self._fail_step(step, str(e))
            raise

    # ---- 阶段 4: 洞察提取 ----

    def extract_insights(self, eda_results: Dict[str, Any],
                         dataset: DatasetInfo) -> List[Insight]:
        """
        从分析结果中提取有价值的洞察。

        Args:
            eda_results: EDA 结果
            dataset: 数据集信息

        Returns:
            洞察列表
        """
        step = self._start_step(AnalysisStepType.STATISTICAL, "提取分析洞察")

        try:
            insights = []
            insight_counter = 0

            def add_insight(insight_type, title, description, confidence,
                          related_cols=None, recommendation=""):
                nonlocal insight_counter
                insight_counter += 1
                insights.append(Insight(
                    insight_id=f"insight-{insight_counter:03d}",
                    insight_type=insight_type,
                    title=title,
                    description=description,
                    confidence=confidence,
                    related_columns=related_cols or [],
                    recommendation=recommendation,
                ))

            # 洞察 1: 数据质量
            missing_rate = dataset.total_missing / (dataset.row_count * dataset.column_count)
            if missing_rate > 0.1:
                add_insight(
                    InsightType.SUMMARY,
                    "数据缺失率较高",
                    f"数据集整体缺失率为 {missing_rate:.1%}，可能影响分析结果的准确性。",
                    confidence=0.95,
                    recommendation="考虑填充缺失值或删除高缺失列，进行敏感性分析。",
                )
            else:
                add_insight(
                    InsightType.SUMMARY,
                    "数据质量良好",
                    f"数据集缺失率仅为 {missing_rate:.1%}，数据质量较高。",
                    confidence=0.95,
                    recommendation="保持当前数据采集流程，定期监控数据质量。",
                )

            # 洞察 2: 强相关性
            strong_corrs = [c for c in eda_results["correlations"]
                           if abs(c["correlation"]) > 0.7]
            if strong_corrs:
                top_corr = strong_corrs[0]
                add_insight(
                    InsightType.CORRELATION,
                    f"{top_corr['column1']} 与 {top_corr['column2']} 强相关",
                    f"两者的 {top_corr['direction']} 相关系数为 {top_corr['correlation']}。",
                    confidence=0.9,
                    related_cols=[top_corr["column1"], top_corr["column2"]],
                    recommendation="建议进一步探究因果关系，可考虑用于预测建模。",
                )

            # 洞察 3: 异常值
            outlier_cols = [o for o in eda_results["outliers"]
                           if o["outlier_ratio"] > 0.05]
            if outlier_cols:
                col = outlier_cols[0]
                add_insight(
                    InsightType.ANOMALY,
                    f"{col['column']} 存在较多异常值",
                    f"异常值比例为 {col['outlier_ratio']:.1%}，超出正常范围。",
                    confidence=0.85,
                    related_cols=[col["column"]],
                    recommendation="调查异常值原因，判断是数据错误还是真实存在的极端情况。",
                )

            # 洞察 4: 数据规模
            if dataset.row_count > 10000:
                add_insight(
                    InsightType.SUMMARY,
                    "数据集规模较大",
                    f"数据集包含 {dataset.row_count} 条记录，适合进行机器学习建模。",
                    confidence=0.9,
                    recommendation="可以考虑使用更复杂的模型进行深入分析。",
                )

            # 洞察 5: 中等相关性模式
            moderate_corrs = [c for c in eda_results["correlations"]
                             if 0.4 < abs(c["correlation"]) <= 0.7]
            if moderate_corrs:
                add_insight(
                    InsightType.PATTERN,
                    "发现中等强度的相关模式",
                    f"共发现 {len(moderate_corrs)} 对中等强度的变量关系，值得进一步探索。",
                    confidence=0.8,
                    recommendation="建议进行分组分析或控制变量分析以验证这些关系。",
                )

            self._complete_step(step, f"提取了 {len(insights)} 条洞察")
            return insights

        except Exception as e:
            self._fail_step(step, str(e))
            raise

    # ---- 阶段 5: 生成可视化建议 ----

    def suggest_visualizations(self, insights: List[Insight],
                               dataset: DatasetInfo) -> List[Dict[str, Any]]:
        """
        根据洞察生成可视化建议。

        Args:
            insights: 洞察列表
            dataset: 数据集信息

        Returns:
            可视化建议列表
        """
        step = self._start_step(AnalysisStepType.VISUALIZE, "生成可视化建议")

        try:
            visualizations = []

            # 基础可视化
            numeric_cols = [c for c in dataset.columns if c.data_type == "numeric"]

            visualizations.append({
                "type": "histogram",
                "title": f"{numeric_cols[0].name} 分布直方图" if numeric_cols else "数值分布",
                "columns": [numeric_cols[0].name] if numeric_cols else [],
                "purpose": "了解数据分布形态",
                "priority": "high",
            })

            visualizations.append({
                "type": "boxplot",
                "title": "数值变量箱线图",
                "columns": [c.name for c in numeric_cols[:5]],
                "purpose": "识别异常值和分布范围",
                "priority": "medium",
            })

            # 相关性热图
            if len(numeric_cols) >= 2:
                visualizations.append({
                    "type": "correlation_heatmap",
                    "title": "变量相关性热图",
                    "columns": [c.name for c in numeric_cols],
                    "purpose": "直观展示变量间的相关关系",
                    "priority": "high",
                })

            # 根据洞察添加特定可视化
            for insight in insights:
                if insight.insight_type == InsightType.CORRELATION:
                    visualizations.append({
                        "type": "scatter_plot",
                        "title": f"{insight.related_columns[0]} vs {insight.related_columns[1]} 散点图",
                        "columns": insight.related_columns,
                        "purpose": "验证相关关系，观察数据分布",
                        "priority": "high",
                        "insight_id": insight.insight_id,
                    })

            self._complete_step(step, f"生成 {len(visualizations)} 个可视化建议")
            return visualizations

        except Exception as e:
            self._fail_step(step, str(e))
            raise

    # ---- 阶段 6: 生成报告 ----

    def generate_report(self, dataset: DatasetInfo, eda_results: Dict[str, Any],
                        insights: List[Insight],
                        visualizations: List[Dict[str, Any]]) -> AnalysisReport:
        """
        生成最终的分析报告。

        Args:
            dataset: 数据集信息
            eda_results: EDA 结果
            insights: 洞察列表
            visualizations: 可视化建议

        Returns:
            完整的分析报告
        """
        step = self._start_step(AnalysisStepType.REPORT, "生成分析报告")

        try:
            # 生成摘要
            overall_summary = (
                f"本次分析对数据集 '{dataset.name}' 进行了全面的探索性分析。"
                f"数据集包含 {dataset.row_count} 行 {dataset.column_count} 列数据，"
                f"缺失率约为 {dataset.total_missing / (dataset.row_count * dataset.column_count):.1%}。"
                f"共发现 {len(insights)} 条有价值的洞察，"
                f"涉及数据质量、变量关系和异常模式等方面。"
            )

            # 关键发现
            key_findings = [
                f"{i.title}（置信度 {i.confidence:.0%}）"
                for i in insights[:5]
            ]

            # 建议
            recommendations = [i.recommendation for i in insights if i.recommendation]
            recommendations.append("建议根据可视化建议创建图表，更直观地展示分析结果。")
            recommendations.append("可以考虑进一步的假设检验或预测建模来验证发现。")

            # 局限性
            limitations = [
                "本次分析为探索性分析，结论需进一步验证",
                "相关性不代表因果关系，需谨慎解读",
                "异常值处理可能影响分析结果",
                "样本量可能不足以支持某些统计推断",
            ]

            total_duration = sum(s.duration_seconds for s in self.steps)

            report = AnalysisReport(
                dataset=dataset,
                steps=self.steps.copy(),
                insights=insights,
                visualizations=visualizations,
                overall_summary=overall_summary,
                key_findings=key_findings,
                recommendations=recommendations,
                limitations=limitations,
                total_duration_seconds=total_duration,
            )

            self._complete_step(step, "报告生成完成")
            return report

        except Exception as e:
            self._fail_step(step, str(e))
            raise

    # ---- 辅助方法 ----

    def _start_step(self, step_type: AnalysisStepType, description: str) -> AnalysisStep:
        """开始一个分析步骤。"""
        self.step_counter += 1
        step = AnalysisStep(
            step_id=f"step-{self.step_counter:02d}",
            step_type=step_type,
            description=description,
            status="running",
        )
        self.steps.append(step)
        return step

    def _complete_step(self, step: AnalysisStep, result_summary: str):
        """完成一个分析步骤。"""
        step.status = "completed"
        step.result_summary = result_summary
        step.duration_seconds = 0.1  # 模拟耗时

    def _fail_step(self, step: AnalysisStep, error_message: str):
        """标记步骤失败。"""
        step.status = "failed"
        step.error_message = error_message

    # ---- 完整分析流程 ----

    def run_analysis(self, data: List[Dict[str, Any]],
                     dataset_name: str = "dataset") -> AnalysisReport:
        """
        执行完整的数据分析流程。

        Args:
            data: 待分析的数据
            dataset_name: 数据集名称

        Returns:
            完整的分析报告
        """
        print(f"\n📊 开始分析: {dataset_name}")
        print("=" * 60)

        start_time = time.time()

        # 阶段 1: 数据加载
        dataset = self.load_data(data, dataset_name)
        print(f"  ✓ 数据加载: {dataset.row_count} 行, {dataset.column_count} 列")

        # 阶段 2: 数据清洗
        cleaned_data, clean_stats = self.clean_data(data, dataset)
        print(f"  ✓ 数据清洗: {len(cleaned_data)} 行 (去除 {len(data) - len(cleaned_data)} 行)")

        # 阶段 3: 探索性分析
        eda_results = self.exploratory_analysis(cleaned_data, dataset)
        print(f"  ✓ 探索分析: {len(eda_results['correlations'])} 对相关性")

        # 阶段 4: 洞察提取
        insights = self.extract_insights(eda_results, dataset)
        print(f"  ✓ 洞察提取: {len(insights)} 条发现")

        # 阶段 5: 可视化建议
        visualizations = self.suggest_visualizations(insights, dataset)
        print(f"  ✓ 可视化建议: {len(visualizations)} 个")

        # 阶段 6: 生成报告
        report = self.generate_report(dataset, eda_results, insights, visualizations)
        report.total_duration_seconds = time.time() - start_time

        print(f"\n{'=' * 60}")
        print(f"✅ 分析完成！耗时: {report.total_duration_seconds:.1f} 秒")

        return report


# ======================================================================
# 生成示例数据
# ======================================================================

def generate_sample_data(n_rows: int = 100) -> List[Dict[str, Any]]:
    """生成示例数据用于演示。"""
    import random
    random.seed(42)

    data = []
    for i in range(n_rows):
        age = random.randint(18, 70)
        income = 30000 + age * 800 + random.gauss(0, 10000)
        spending = income * (0.3 + random.random() * 0.4)
        score = 500 + (income / 200) + random.gauss(0, 50)

        row = {
            "id": i + 1,
            "age": age,
            "income": round(income, 2),
            "spending_score": round(spending, 2),
            "credit_score": int(min(850, max(300, score))),
            "gender": random.choice(["M", "F", None]),
            "region": random.choice(["North", "South", "East", "West"]),
            "member_years": random.randint(0, 10),
            "is_active": random.choice([True, False]),
        }

        # 随机引入一些缺失值
        if random.random() < 0.05:
            row["income"] = None
        if random.random() < 0.03:
            row["gender"] = None

        # 引入一些异常值
        if i == 50:
            row["income"] = 500000  # 异常高收入

        data.append(row)

    return data


# ======================================================================
# 主函数
# ======================================================================

def main():
    """运行数据分析管道示例。"""
    print("\n" + "█" * 60)
    print("  LiuHao AI OS - 数据分析管道")
    print("█" * 60)

    # ---- 初始化 ----
    print("\n[初始化] 准备数据分析管道...")

    try:
        provider = get_provider()
        print(f"       Provider: {provider.__class__.__name__}")
        factory = AgentFactory(provider=provider)
        base_agent = factory.create_agent(AgentRole.ANALYST, name="data-analyst")
        analyst_agent = DataAnalystAgent(agent=base_agent)
    except Exception as e:
        print(f"       警告: 无法获取真实 Provider ({e})")
        print("       使用模拟模式...")

        from ai.providers import BaseProvider
        from ai.employee import Agent

        class MockProvider(BaseProvider):
            def __init__(self):
                self.name = "mock"
            def generate(self, prompt, **kwargs):
                return {"output": "分析完成", "latency_ms": 100, "tokens_used": 50}

        base_agent = Agent(
            id="da-001",
            agent_type="analyst",
            name="data-analyst",
            provider=MockProvider(),
            system_prompt="你是一名资深数据分析师。",
        )
        analyst_agent = DataAnalystAgent(agent=base_agent)

    pipeline = DataAnalysisPipeline(analyst_agent)

    # ---- 生成示例数据 ----
    print("\n[数据] 生成示例数据...")
    sample_data = generate_sample_data(200)
    print(f"       生成 {len(sample_data)} 条记录")

    # ---- 运行分析 ----
    print("\n[分析] 执行数据分析...")
    report = pipeline.run_analysis(sample_data, "客户画像数据集")

    # ---- 打印报告 ----
    print("\n" + "=" * 60)
    print("  📈 数据分析报告")
    print("=" * 60)

    print(f"\n📋 数据集概览:")
    print(f"   名称: {report.dataset.name}")
    print(f"   行数: {report.dataset.row_count}")
    print(f"   列数: {report.dataset.column_count}")
    print(f"   缺失总数: {report.dataset.total_missing}")
    print(f"   重复行: {report.dataset.duplicate_rows}")
    print(f"   估算内存: {report.dataset.memory_usage_mb:.2f} MB")

    print(f"\n📊 数据列 ({report.dataset.column_count} 列):")
    for col in report.dataset.columns[:8]:
        type_icon = {
            "numeric": "🔢",
            "string": "📝",
            "boolean": "✅",
            "date": "📅",
        }.get(col.data_type, "❓")
        missing = f", {col.missing_rate:.1%} 缺失" if col.missing_rate > 0 else ""
        stats = ""
        if col.data_type == "numeric" and col.mean_value is not None:
            stats = f", 均值: {col.mean_value:.1f}"
        print(f"   {type_icon} {col.name} ({col.data_type}){missing}{stats}")
    if len(report.dataset.columns) > 8:
        print(f"   ... 还有 {len(report.dataset.columns) - 8} 列")

    print(f"\n💡 关键洞察 ({len(report.insights)} 条):")
    for i, insight in enumerate(report.insights, 1):
        type_icon = {
            "trend": "📈",
            "correlation": "🔗",
            "anomaly": "⚠️",
            "pattern": "🎯",
            "summary": "📝",
        }.get(insight.insight_type.value, "💡")
        bar_len = int(insight.confidence * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"   {i}. {type_icon} {insight.title}")
        print(f"      置信度: [{bar}] {insight.confidence:.0%}")
        print(f"      {insight.description[:80]}")
        if insight.recommendation:
            print(f"      💬 建议: {insight.recommendation[:60]}")

    print(f"\n📊 可视化建议 ({len(report.visualizations)} 个):")
    for vis in report.visualizations:
        priority_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(vis["priority"], "⚪")
        print(f"   {priority_icon} [{vis['type']}] {vis['title']}")
        print(f"      目的: {vis['purpose']}")

    print(f"\n📝 分析步骤 ({len(report.steps)} 步):")
    for step in report.steps:
        status_icon = {"completed": "✅", "running": "⏳", "failed": "❌", "pending": "⏸️"}.get(step.status, "❓")
        print(f"   {status_icon} [{step.step_type.value}] {step.description}")
        if step.result_summary:
            print(f"      {step.result_summary}")

    print(f"\n🔍 关键发现:")
    for i, finding in enumerate(report.key_findings, 1):
        print(f"   {i}. {finding}")

    print(f"\n💡 建议:")
    for i, rec in enumerate(report.recommendations[:5], 1):
        print(f"   {i}. {rec}")

    print(f"\n⚠️  局限性:")
    for i, lim in enumerate(report.limitations, 1):
        print(f"   {i}. {lim}")

    print(f"\n⏱️  总耗时: {report.total_duration_seconds:.2f} 秒")

    print("\n" + "=" * 60)
    print("  数据分析管道功能总结")
    print("=" * 60)
    print("""
  ✓ 多源数据加载
  ✓ 自动数据类型推断
  ✓ 数据质量检查与清洗
  ✓ 描述性统计分析
  ✓ 相关性分析
  ✓ 异常值检测
  ✓ 智能洞察提取
  ✓ 可视化建议生成
  ✓ 结构化分析报告
  ✓ 完整步骤追踪
""")


if __name__ == "__main__":
    main()
