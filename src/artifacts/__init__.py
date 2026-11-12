"""产物索引（Artifact Index）。

对外只暴露索引本体与它的记录/校验数据结构；所有实现都在
:mod:`src.artifacts.index` 里。该模块刻意不 import 执行内核的运行时，
只以「鸭子类型的事件源」接入 journal / 目标历史，因此可以独立单测。
"""

from .index import (  # noqa: F401
    ArtifactIndex,
    ArtifactRecord,
    ArtifactVerification,
    WorkspaceViolation,
    default_index_path,
)

__all__ = [
    "ArtifactIndex",
    "ArtifactRecord",
    "ArtifactVerification",
    "WorkspaceViolation",
    "default_index_path",
]
