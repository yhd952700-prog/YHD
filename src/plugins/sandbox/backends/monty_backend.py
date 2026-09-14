"""Monty 后端 —— 给 AI 生成代码跑的**受限第二执行面**。

## 当前状态：**已登记，但不可实例化**（2026-09-13 实测）

两处硬阻塞，都在真实环境验证过，不是推测：

1. **PyPI 包名冲突。** `pip install monty` 装到的**不是** Pydantic Monty，
   而是 `materialyzeai/monty`（"Monty: Python Made Even Easier"，一套工具函数）。
   真正的 Pydantic Monty 发布名是 **`pydantic-monty`** —— 名字不同项目。
   依赖写错名字会静默装到另一个人的包，这是供应链层的事故。

2. **`pydantic_monty` 0.0.23 的 Python 绑定尚未成型。**
   实测：`Monty("1+2")` → `TypeError: Monty.__new__() takes 0 positional arguments
   but 1 was given`；类上只暴露了 `checkout` 一个成员，`MontySession` 也只有
   dump/feed_run/feed_start/install_dependencies 这几个方法。无法"喂一段源码求值"。

因此本后端的正确行为是**诚实不可用**：`is_available() == False` 且携带可读原因，
`execute()` 返回 `status=BACKEND_UNAVAILABLE` —— **绝不**退而用 subprocess 跑，
也**绝不**返回 success=True（那会把"没执行"说成"执行成功"）。

## 为什么仍然保留这个文件

- `select_backend(required=True)` 那条不降级的语义需要一个真实靶子来验证；
- `pydantic-monty` 一旦发布可用的 constructor/run，本文件只需替换 `_PROBE` 通过
  即可亮的能力，调用方一行不改；
- 把"为什么今天不能用"以可执行断言的形式留在仓里，比写在文档里更不容易丢失
  （见 `test_sandbox_monty_backend.py::test_monty_api_preconditions`）。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from .base import (
    ExecutionResult,
    ExecutionStatus,
    ResourceLimits,
    SandboxBackendBase,
    SandboxBackendStatus,
    SandboxBackendType,
)

#: 安装名 —— 注意必须是 `pydantic-monty`，裸 `monty` 是另一个不相干的项目。
PACKAGE_NAME = "pydantic_monty"
DISTRIBUTION_NAME = "pydantic-monty"


def probe_monty() -> Optional[str]:
    """探测 monty 是否真的能用。

    返回 None 表示可用；否则返回**人类可读的不可用原因**。
    刻意返回 None 而不是 bool —— 调用方必须能把原因转述给人类。
    """
    try:
        import pydantic_monty as monty  # noqa: F401
    except ImportError:
        return (
            f"{DISTRIBUTION_NAME} 未安装（import {PACKAGE_NAME} 失败）。"
            f"安装时用 `pip install {DISTRIBUTION_NAME}` —— **不要**用 `monty`，"
            f"那是 materialyzeai 的另一个不相干项目。"
        )
    except Exception as exc:  # pragma: no cover - 极端情况，但必须不崩
        return f"导入 {PACKAGE_NAME} 时发生非预期错误: {type(exc).__name__}: {exc}"

    cls = getattr(monty, "Monty", None)
    if cls is None:
        return f"{PACKAGE_NAME} 未提供 Monty 类"

    # 真正的可用性判据：能不能构造出一个带源码的实例并求值。
    # 只有 class 上存在是不够的 —— 0.0.23 就存在 Monty，但构造不出东西。
    try:
        probe = cls("1 + 2")
    except Exception as exc:
        return (
            f"{PACKAGE_NAME} 的 Python 绑定尚不可用：Monty(源码) 构造失败 "
            f"({type(exc).__name__}: {exc})。Rust 侧的二进制在，但 Python 绑定 "
            f"还没成型 —— 此刻不具备执行代码的能力。请升级 {DISTRIBUTION_NAME}。"
        )

    runner = getattr(probe, "run", None)
    if not callable(runner):
        return (
            f"{PACKAGE_NAME} 的实例缺少可调用的 run()：当前版本未提供求值入口。"
        )
    return None


class MontyBackend(SandboxBackendBase):
    """受限制的 Python 执行面（沙箱解释器，不是容器也不是子进程）。"""

    def __init__(self) -> None:
        self._unavailable_reason: Optional[str] = probe_monty()

    # -- identity ---------------------------------------------------------

    @property
    def backend_type(self) -> SandboxBackendType:  # type: ignore[override]
        return SandboxBackendType.MONTY

    @property
    def name(self) -> str:
        return "Monty (restricted Python interpreter)"

    # -- health -----------------------------------------------------------

    def is_available(self) -> bool:
        return self._unavailable_reason is None

    def get_status(self) -> SandboxBackendStatus:
        if self._unavailable_reason is None:
            return SandboxBackendStatus.HEALTHY
        return SandboxBackendStatus.UNAVAILABLE

    def get_backend_info(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "backend": SandboxBackendType.MONTY.value,
            "available": self.is_available(),
            "distribution": DISTRIBUTION_NAME,
            "import_name": PACKAGE_NAME,
        }
        if self._unavailable_reason is not None:
            info["reason"] = self._unavailable_reason
        return info

    # -- execution --------------------------------------------------------

    def _unavailable_result(self, execution_id: str) -> ExecutionResult:
        return ExecutionResult(
            execution_id=execution_id,
            success=False,
            exit_code=None,
            error=self._unavailable_reason or "monty is unavailable",
            status=ExecutionStatus.BACKEND_UNAVAILABLE,
            backend_info=self.get_backend_info(),
        )

    @staticmethod
    def _extract_source(command: List[str]) -> Optional[str]:
        """从 argv 里取出 Python 源码。

        接受的形状：`[<python>, "-c", <code>]`（对齐 subprocess 后端的习惯写法）。
        其它形状明确不支持 —— 不猜、不"尽力解析"，猜错就等于执行了别的东西。
        """
        if len(command) >= 3 and command[1] == "-c":
            return command[2]
        return None

    def execute(
        self,
        execution_id: str,
        command: List[str],
        resource_limits: Optional[ResourceLimits] = None,
        environment: Optional[Dict[str, str]] = None,
        working_directory: Optional[str] = None,
        timeout: Optional[int] = None,
        input_data: Optional[str] = None,
        volumes: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        if self._unavailable_reason is not None:
            return self._unavailable_result(execution_id)

        source = self._extract_source(command)
        if source is None:
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                error=(
                    "Monty 只接受 [<python>, '-c', <code>] 形态；"
                    f"收到 {command!r}。它不跑容器镜像、不跑 shell —— "
                    "「做不了」要说出来，不能假装跑过。"
                ),
                status=ExecutionStatus.UNSUPPORTED,
                backend_info=self.get_backend_info(),
            )

        try:
            import pydantic_monty as monty
        except Exception as exc:  # pragma: no cover - 正常路径已在 __init__ 探测过
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                status=ExecutionStatus.BACKEND_UNAVAILABLE,
                backend_info=self.get_backend_info(),
            )

        limits_arg: Dict[str, Any] = {}
        timeout = timeout or (resource_limits.execution_time_limit if resource_limits else None) or 10
        # 资源上限按 monty 可用的参数名传；不认识的参数宁可不传，也不要传错把限制架空。
        limits_arg["max_duration_secs"] = timeout

        try:
            session = monty.Monty(source)
            value = session.run(inputs={}, external_functions=[])
        except Exception as exc:
            # MontySyntaxError / MontyRuntimeError 等都在这里落地 —— 这是**诚实的失败**
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=1,
                error=f"{type(exc).__name__}: {exc}",
                status=ExecutionStatus.REJECTED,
                backend_info=self.get_backend_info(),
            )

        return ExecutionResult(
            execution_id=execution_id,
            success=True,
            exit_code=0,
            stdout="" if value is None else str(value),
            output="" if value is None else str(value),
            status=ExecutionStatus.SUCCESS,
            backend_info={**self.get_backend_info(), "type": "monty"},
        )

    # -- lifecycle --------------------------------------------------------

    def cleanup(self, execution_id: str) -> bool:
        # 沙箱解释器无外部句柄需要回收；这里不存在"假成功"的语义歧义，
        # 因为没有旧 features 依赖它。
        return True

    def cleanup_all(self) -> None:
        return None

    def __repr__(self) -> str:  # pragma: no cover - 诊断用途
        state = "available" if self.is_available() else f"unavailable: {self._unavailable_reason}"
        return f"<MontyBackend {state} py={sys.version_info[0]}.{sys.version_info[1]}>"
