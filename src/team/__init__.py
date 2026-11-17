"""贾维斯带一队：内部 worker 分工（0.4.6 D 组，Muse+Grok 融合后端）。

Joseph 只跟一个 Jarvis 说话。Jarvis 收到任务后拆成子任务，派给内部
worker（查资料 / 整理文件 / 管提醒 / 写代码），子任务进度在聊天页可
见，需要拍板时回到审批中心问 Joseph。

**内部 worker，不对外设 Bot 列表**：没有「一队 Bot 任你一个一个管」
的入口 —— worker 只有 kind（research / files / reminder / code），
没有名字、没有单独的聊天入口、不能被用户直接指派。用户只看到「任务 →
子任务 → 进度」，以及「等你拍板」的待办。

诚实边界（v1）：

* ``files`` 是真的：只读列工作区文件，按关键词筛出匹配项，返回真实
  数量与文件名；
* ``reminder`` 是真的：走提醒服务创建提醒；建不出来（时间解析失败、
  服务不可用）就转拍板，不假装「已提醒」；
* ``research`` / ``code`` 目前没有可执行的真实 handler（联网搜索是
  6-4、自改代码的自动提议是 6-2 二期）：拆出来后直接进审批中心问
  Joseph「范围对不对、要不要做」，批了也只记为「已批准、等能力接线」，
  绝不假装已经查完 / 写完。
"""

from __future__ import annotations

from .dispatcher import (
    SUBTASK_KINDS,
    advance_task,
    create_team_task,
    decompose,
    sync_decisions,
)
from .store import TeamStore, get_team_store

__all__ = [
    "SUBTASK_KINDS",
    "TeamStore",
    "advance_task",
    "create_team_task",
    "decompose",
    "get_team_store",
    "sync_decisions",
]
