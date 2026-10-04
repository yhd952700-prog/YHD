"""信任内核只读产品面 —— 把信任内核的真实状态摊开给人看（只读）。

LHX-C-010 信任内核此前长期处于 PRIMITIVE-ONLY：它的决策函数（``is_revoked`` /
``evaluate_trust`` / ``get_score``）只在两个**孤儿模块**里被调用，从未走上任何真实
产品路径，于是信任状态对真实用户决策毫无影响。

本模块给产品 app 补上第一个诚实的、人类可见的信任出口：

- ``GET /v1/trust/{entity_id}`` 返回 ``entity_id`` 的**真实**信任状态
  （来自进程单例 ``get_trust_manager()``）：
  - ``revoked``：``is_revoked(entity_id)`` 的真实布尔值；
  - ``scores``：``get_all_scores(entity_id)`` 的真实分数字典，按 scope 展开为
    ``{level, score, expires_at, is_expired}``；**若该 scope 没有任何分数则直接省略**，
    绝不造值；
  - ``chain_summary``：以 ``source == target == entity_id`` 探针调用
    ``get_trust_chain(entity_id, entity_id)`` 的真实结果序列化（自信任链恒有自环）；
    若内核确实没有该实体的任何链（不可能为 None，故这里始终返回真实链），照实返回；
  - ``stats``：``manager.stats()`` 的真实统计。

- 人类主权闸门（``require_human_principal``）：在 ``main.py`` 挂载时声明
  （见 ``dependencies=[Depends(_require_human)]``），未带令牌 → 401；
- 严格只读：本模块没有任何写入 / 撤销入口，不会伪造任何信任状态。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter

from src.kernels.trust import (
    TrustChain,
    TrustChainLink,
    TrustScore,
    TrustScope,
    get_trust_manager,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/trust", tags=["trust"])


def _json_safe_dt(value) -> Optional[str]:
    """datetime → ISO 字符串；None → None。绝不把 None 说成某个时间。"""
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _score_summary(scope: TrustScope, score: TrustScore) -> Dict[str, Any]:
    """把一个真实 TrustScore 收缩成前端友好的只读摘要。"""
    return {
        "level": score.level.value if hasattr(score.level, "value") else str(score.level),
        "score": score.score,
        "expires_at": _json_safe_dt(score.valid_until),
        "is_expired": bool(score.is_expired),
    }


def _chain_link_summary(link: TrustChainLink) -> Dict[str, Any]:
    return {
        "from_entity": link.from_entity,
        "to_entity": link.to_entity,
        "trust_score": link.trust_score,
        "scope": link.scope.value if hasattr(link.scope, "value") else str(link.scope),
        "active": bool(link.active),
        "expires_at": _json_safe_dt(link.expires_at),
        "is_expired": bool(link.is_expired),
    }


def _chain_summary(chain: TrustChain) -> Dict[str, Any]:
    """把真实 TrustChain 收缩成前端友好的只读摘要。"""
    return {
        "source": chain.source,
        "target": chain.target,
        "composite_score": chain.composite_score,
        "scope": chain.scope.value if hasattr(chain.scope, "value") else str(chain.scope),
        "valid": bool(chain.valid),
        "computed_at": _json_safe_dt(chain.computed_at),
        "links": [_chain_link_summary(link) for link in chain.links],
    }


@router.get("/{entity_id}")
def get_trust(entity_id: str) -> Dict[str, Any]:
    """读取某个实体的真实信任状态（只读）。

    数据全部来自信任内核进程单例，绝不另建一份可能分叉 / 造假的副本。
    """
    mgr = get_trust_manager()

    revoked = mgr.is_revoked(entity_id)

    # 真实分数：若该实体没有任何分数，``get_all_scores`` 返回空 dict，
    # 这里原样呈现（scores 为 {}），不补任何占位值。
    scores: Dict[str, Any] = {}
    all_scores = mgr.get_all_scores(entity_id)
    for scope, score in all_scores.items():
        scope_key = scope.value if hasattr(scope, "value") else str(scope)
        scores[scope_key] = _score_summary(scope, score)

    # 真实自信任链探针：source == target == entity_id。
    chain = mgr.get_trust_chain(entity_id, entity_id)
    chain_summary = _chain_summary(chain) if chain is not None else None

    return {
        "entity_id": entity_id,
        "revoked": revoked,
        "scores": scores,
        "chain_summary": chain_summary,
        "stats": mgr.stats(),
    }
