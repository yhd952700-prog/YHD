"""守住 ``src.gateway.local_proof`` 对 IPC 常量的再导出。

525bc28d 的 flake8 F401 清理把 ``HANDOFF_HEADER`` / ``RESET_HEADER`` 当成
"本文件未引用" 的未使用导入删掉，导致 ``src/gateway/accounts.py`` 里的
``local_proof.HANDOFF_HEADER`` 抛 ``AttributeError``，``POST /v1/accounts/register``
恒定 500（新装用户第一次注册即失败）。

这类坑只有测试能挡住：F401 自动修复看不到 "别的模块按属性取" 的用法，
所以这里直接断言四个名字仍悬挂在模块上，且与 ``src.common.local_proof_ipc``
里的同名对象是**同一对象**。
"""
from __future__ import annotations

import pytest

from src.common import local_proof_ipc
from src.gateway import local_proof

REEXPORTED_NAMES = (
    "HANDOFF_HEADER",
    "RESET_HEADER",
    "TRUSTED_LAUNCH_ENV",
    "channel_name",
)


@pytest.mark.parametrize("name", REEXPORTED_NAMES)
def test_local_proof_reexports_ipc_constant(name: str) -> None:
    assert hasattr(local_proof, name), f"src.gateway.local_proof 丢失再导出：{name}"
    assert getattr(local_proof, name) is getattr(local_proof_ipc, name)
