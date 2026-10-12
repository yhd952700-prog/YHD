"""确定性推理引擎（十源 packages 层 reasoning 包的真实实现）。

把「推理」从 L-Core（``lcore.py``）与 execution kernel（GoalDecomposer/PlanBuilder）
中抽成一个显式的推理原语维度：

- ``ReasoningStep``：前提 → 结论 + 置信度 + 依据。继承链式思维包的同名
  数据类，额外携带 ``conclusion``，因此同时兼容两套 API。
- ``Reasoner.deduce``：**确定性演绎**（forward-chaining 规则匹配），不依赖 LLM，
  结果可复现、可测试（NO-FAKE）。
- ``Reasoner.reason``：生成式推理，可选注入 provider；未注入真实 provider 时
  诚实降级（MOCK 回显），不伪造「智能」结论。

复用而非重写：``deduce`` 的结论可作为 ``execution.GoalDecomposer`` /
``PlanBuilder`` 的输入，不重复实现其已有能力。

历史说明：本模块即原 ``src/ai/reasoning.py``；多策略推理包
（chain_of_thought / reflection / react）并入 ``src/ai/reasoning/`` 后，
演绎引擎迁移于此，包级导出的 ``Reasoner`` 保持不变。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..observability import observe
from .chain_of_thought import ReasoningStep as _ChainStep
from .chain_of_thought import StepType


@dataclass
class ReasoningStep(_ChainStep):
    """一步推理：由一组前提推出一个结论。

    继承链式思维的 ``ReasoningStep``（``step_id`` / ``step_type`` /
    ``content`` 等），并补充演绎语义下的 ``conclusion`` 字段；``content``
    与 ``conclusion`` 保持一致。
    """

    conclusion: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["conclusion"] = self.conclusion
        return d


def _make_step(
    premises: List[str],
    conclusion: str,
    confidence: float,
    justification: str,
) -> ReasoningStep:
    return ReasoningStep(
        step_id=f"rs_{uuid.uuid4().hex[:8]}",
        step_type=StepType.INFERENCE,
        content=conclusion,
        premises=list(premises),
        confidence=confidence,
        justification=justification,
        conclusion=conclusion,
    )


@dataclass
class Reasoner:
    """确定性演绎推理 + 可选生成式推理。

    ``deduce`` 的规则形如 ``(前提集合, 结论)``；当所有前提已知时推出结论。
    forward-chaining 会持续推导，直到没有新结论产生（闭包）。
    """

    @observe("Reasoner.deduce")
    def deduce(
        self,
        premises: List[str],
        rules: List[Tuple[List[str], str]],
    ) -> List[ReasoningStep]:
        """确定性演绎：返回所有可推出的结论（含推导顺序）。"""
        known: set = set(premises)
        steps: List[ReasoningStep] = []

        changed = True
        while changed:
            changed = False
            for conds, conclusion in rules:
                if conclusion in known:
                    continue
                if set(conds).issubset(known):
                    steps.append(
                        _make_step(
                            premises=sorted(conds),
                            conclusion=conclusion,
                            confidence=1.0,
                            justification="deduction",
                        )
                    )
                    known.add(conclusion)
                    changed = True

        return steps

    @observe("Reasoner.reason")
    def reason(
        self,
        question: str,
        context: Optional[List[str]] = None,
        provider: Any = None,
    ) -> ReasoningStep:
        """生成式推理：用 provider 从上下文回答问题。

        未注入真实 provider 时诚实降级为确定性回显（confidence=0.0），
        绝不伪造「智能」结论。
        """
        ctx = list(context or [])
        if provider is not None and hasattr(provider, "generate"):
            answer = provider.generate(
                f"Question: {question}\nContext: {ctx}",
            )
            return _make_step(
                premises=ctx,
                conclusion=answer,
                confidence=0.5,  # 生成式推理的置信度由 provider 能力决定，此处为占位
                justification="generative",
            )
        # 无真实 provider：诚实降级。
        return _make_step(
            premises=ctx,
            conclusion=f"<no-provider> {question}",
            confidence=0.0,
            justification="no-provider-fallback",
        )
