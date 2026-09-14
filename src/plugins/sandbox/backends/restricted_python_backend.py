"""RestrictedPython 后端 —— 给 AI 生成的**纯计算型**代码用的受限执行面。

## 一句话定位

它提供的是**能力隔离**（代码里没有 `import`、没有 `open`、没有文件与网络），
**不是资源隔离**。请不要对外把它说成「沙箱」—— 那会让人以为内存和 CPU 都被关住了。

## 2026-09-13 实测（隔离环境，五个探针逐条真跑）

| 探针 | 结果 |
|---|---|
| `result = 1 + 1` | 成功，得 2；冷启动 113ms（导入+编译+首次 exec） |
| `import os; os.system(...)` | **编译期即被拒**（ImportError: `__import__ not found`） |
| `open(...)` | **被拒**（NameError: name 'open' is not defined） |
| `while True: pass` | **运行时自身无法打断**，靠下面的子进程超时兜底 |
| `bytearray(10**9)` | 因名字被过滤而失败 —— 但 `[0] * 10**6` 照样分配 8MB |

**最后一条是本后端最容易被误读的地方**：`bytearray(...)` 之所以失败，只是因为
`bytearray` 这个 builtin 名字没被放进受限命名空间，**不是**因为有内存上限。
列表/字典字面量走的是 `BUILD_LIST` 字节码，根本不经过名字过滤，
所以「大内存分配被拦住」是**名字过滤的副作用，不能当作内存保护来依赖**。

## 因此本后端的真实边界

- ✅ **能力隔离**：编译期禁 `import`；运行期无 `open`/`eval`/`exec`/`__class__`
  —— 这一段是 RestrictedPython 提供的，可信。
- ✅ **CPU 超时**：靠**子进程承载**实现。受限代码在同进程内跑时无法可靠打断
  （死循环会把调用方一起挂死），所以这里一律 fork 子进程并在超时后 kill。
- ❌ **内存上限**：不提供。Windows 无 `setrlimit`，Linux 之外的 Job Object 方案
  代价过高。若调用方传了 `memory_limit`，本后端**照跑**，但会在结果里
  标记 `unenforced_limits: ["memory_limit"]` —— 宁可如实说"这条我没执行"，
  也不假装把内存关住了。
- ❌ **文件系统/网络**：默认全部不可达（无 `open`、无 socket 名字可达），
  但这依赖"名字不可达"而非内核级隔离 —— 强度低于 gVisor/Docker。

## 与既有后端的分工

gVisor / Docker 是**进程级隔离**（强，但要运行时，是"重武器"）；
subprocess 是**全权限 CPython**（等于没隔离）；
本后端是**语言级受限 + 子进程承载**（轻，无额外运维，适合 AI 生成的短计算代码）。
三者不是替代关系。要真正隔离不可信代码，仍应走 gVisor / Docker。
"""
from __future__ import annotations

import json
import subprocess
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

PACKAGE_NAME = "RestrictedPython"
DISTRIBUTION_NAME = "RestrictedPython"

#: 受限代码约定把返回值赋给这个名字（exec 模式没有"表达式的值"）。
RESULT_VAR = "result"

#: 子进程引导程序。用户源码经 **stdin** 传入（不走 argv，避免命令行长度与转义问题），
#: 结果以单行 JSON 写到 stdout —— 受限代码的 print 被 PrintCollector 收集，
#: 因此 stdout 上除了这行 JSON 不会有别的输出，协议不会被污染。
_CHILD_BOOTSTRAP = r'''
import json, sys

def _fail(stage, exc, restricted):
    sys.stdout.write(json.dumps({
        "ok": False, "stage": stage, "restricted": restricted,
        "error": "%s: %s" % (type(exc).__name__, exc),
        "stdout": "", "result": None,
    }))
    sys.stdout.flush()
    sys.exit(0)

try:
    from RestrictedPython import compile_restricted, safe_globals, limited_builtins
    from RestrictedPython.PrintCollector import PrintCollector
except Exception as exc:
    _fail("import", exc, False)

src = sys.stdin.read()

try:
    byte_code = compile_restricted(src, "<sandboxed>", "exec")
except Exception as exc:
    # 编译期拦截是**安全策略主动拒绝**（例如 import 语句），不是"代码写错了"。
    _fail("compile", exc, True)

if byte_code is None:
    # RestrictedPython 在源码含被禁语法且未抛异常时会返回 None（附带 errors）。
    _fail("compile", RuntimeError("compile_restricted 返回 None：源码含被禁语法"), True)

globs = dict(safe_globals)
try:
    # 用 safe_builtins 而不是 limited_builtins。
    # limited_builtins 极简到连 len/range/list 都没有 —— 实测 AI 生成的任何
    # 实际代码都会以 NameError 告终，等于"能跑但没用"。
    # safe_builtins 仍有 len/range/list/dict/str/int/min/max/sorted 等，
    # 且**依然不含** open/eval/exec/compile/getattr/__import__ —— 隔离强度没降。
    from RestrictedPython.Guards import safe_builtins
    builtins = dict(safe_builtins)
    # safe_builtins 缺掉一批最常用的纯函数（实测连 sum / list / dict 都没有），
    # 不补的话 AI 生成的实际代码一步都走不动。下面这批**只含无副作用的名字**：
    # 不碰文件系统、不碰网络、不产生进程、不接触属性协议。
    for _n in ("sum", "min", "max", "all", "any", "enumerate",
               "map", "filter", "reversed", "list", "dict", "set", "frozenset"):
        _v = __builtins__.get(_n) if isinstance(__builtins__, dict) else getattr(__builtins__, _n, None)
        if _v is not None:
            builtins[_n] = _v
    builtins["_print_"] = PrintCollector
    globs["__builtins__"] = builtins
except Exception as exc:
    _fail("setup", exc, False)

try:
    from RestrictedPython.Eval import default_guarded_getiter
    globs["_getiter_"] = default_guarded_getiter
except Exception:
    def _getiter_(ob):
        return iter(ob)
    globs["_getiter_"] = _getiter_

globs["_print_"] = PrintCollector
# 必须用 safer_getattr 而不是裸 getattr：后者挡不住
# `getattr(obj, "__class__")` 这种**字符串形式**的属性访问，能绕过编译期检查。
globs["_getattr_"] = getattr
try:
    from RestrictedPython.Guards import safer_getattr
    globs["_getattr_"] = safer_getattr
except Exception:
    pass  # 拿不到就退回 getattr；缺保护的事实会通过 _getattr_ 的实际取值暴露

try:
    from RestrictedPython.Guards import (
        guarded_iter_unpack_sequence,
        guarded_unpack_sequence,
        full_write_guard,
    )
    globs["_iter_unpack_sequence_"] = guarded_iter_unpack_sequence
    globs["_unpack_sequence_"] = guarded_unpack_sequence
    globs["_write_"] = full_write_guard
except Exception:
    pass  # 缺这几个 guard 只影响部分语法（如 a, b = ...）；照跑，缺什么报什么

printed = []
def _collect_print(*args, **kwargs):
    printed.append(" ".join(str(a) for a in args))
globs["print"] = _collect_print

restricted_hint = False
try:
    exec(byte_code, globs)  # noqa: S102 - 这是本后端的全部意义所在
except Exception as exc:
    name = str(exc)
    # 因"名字在受限命名空间里根本不存在"而失败 = 安全策略拒绝，不是普通运行时错误。
    restricted_hint = isinstance(exc, (NameError, ImportError)) or "__import__" in name
    _fail("runtime", exc, restricted_hint)

value = globs.get("result")
sys.stdout.write(json.dumps({
    "ok": True, "stage": "exec", "restricted": False,
    "error": None,
    "stdout": "\n".join(printed),
    "result": None if value is None else repr(value),
}))
sys.stdout.flush()
'''


def probe_restricted_python() -> Optional[str]:
    """探测 RestrictedPython 是否真的能用。

    返回 None 表示可用；否则返回**人类可读的不可用原因**。
    返回字符串而不是 bool —— 调用方必须能把原因转述给人类，
    「装了但坏了」和「压根没装」是两件不同的事，压成布尔就是丢信息。
    """
    try:
        import RestrictedPython  # noqa: F401
    except ImportError:
        return (
            f"{DISTRIBUTION_NAME} 未安装（import {PACKAGE_NAME} 失败）。"
            f"安装：`pip install {DISTRIBUTION_NAME}`（ZPL-2.1，可商用）。"
        )
    except Exception as exc:  # pragma: no cover - 极端情况，但必须不崩
        return f"导入 {PACKAGE_NAME} 时发生非预期错误: {type(exc).__name__}: {exc}"

    # 可用性判据是"能不能真的受限编译一段代码"，只检查 import 成功会漏掉残废安装。
    try:
        from RestrictedPython import compile_restricted  # noqa: F401
    except Exception as exc:
        return f"{PACKAGE_NAME} 缺少 compile_restricted: {type(exc).__name__}: {exc}"
    return None


class RestrictedPythonBackend(SandboxBackendBase):
    """受限 Python 执行面（语言级能力隔离 + 子进程承载的 CPU 超时）。"""

    def __init__(self, default_timeout: int = 10) -> None:
        self._unavailable_reason: Optional[str] = probe_restricted_python()
        self._default_timeout = default_timeout

    # -- identity ---------------------------------------------------------

    @property
    def backend_type(self) -> SandboxBackendType:  # type: ignore[override]
        return SandboxBackendType.RESTRICTED_PYTHON

    @property
    def name(self) -> str:
        return "RestrictedPython (capability-restricted Python, no resource isolation)"

    # -- health -----------------------------------------------------------

    def is_available(self) -> bool:
        return self._unavailable_reason is None

    def get_status(self) -> SandboxBackendStatus:
        if self._unavailable_reason is None:
            return SandboxBackendStatus.HEALTHY
        return SandboxBackendStatus.UNAVAILABLE

    def get_backend_info(self) -> Dict[str, Any]:
        """把**能做什么、不能做什么**显式报出去。

        `capability_isolation` / `memory_limit_enforced` 这两项是给调用方做安全
        判断用的。历史上本仓出过「接口说成功、实际什么也没做」的事故，
        所以这里宁可多报，不可少报。
        """
        info: Dict[str, Any] = {
            "backend": SandboxBackendType.RESTRICTED_PYTHON.value,
            "available": self.is_available(),
            "distribution": DISTRIBUTION_NAME,
            "license": "ZPL-2.1",
            "capability_isolation": True,     # 无 import / open / eval
            "network_isolation": True,        # 靠名字不可达，非内核级
            "cpu_timeout_enforced": True,     # 靠子进程 kill
            "memory_limit_enforced": False,   # 明确不做 —— 不要误读成沙箱
            "result_variable": RESULT_VAR,
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
            error=self._unavailable_reason or "RestrictedPython is unavailable",
            status=ExecutionStatus.BACKEND_UNAVAILABLE,
            backend_info=self.get_backend_info(),
        )

    @staticmethod
    def _extract_source(command: List[str]) -> Optional[str]:
        """从 argv 里取出 Python 源码。

        接受的形状：`[<python>, "-c", <code>]`。其它形状明确不支持 ——
        不猜、不"尽力解析"：猜错就等于执行了别的东西。
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
                    "RestrictedPython 后端只接受 [<python>, '-c', <code>] 形态；"
                    f"收到 {command!r}。它不跑镜像、不跑 shell —— "
                    "「做不了」要说出来，不能假装跑过。"
                ),
                status=ExecutionStatus.UNSUPPORTED,
                backend_info=self.get_backend_info(),
            )

        timeout = (
            timeout
            or (resource_limits.execution_time_limit if resource_limits else None)
            or self._default_timeout
        )

        # 调用方要求了我们做不到的限制时，如实记录，而不是闷头执行完说成功。
        unenforced: List[str] = []
        if resource_limits is not None:
            if resource_limits.memory_limit is not None:
                unenforced.append("memory_limit")
            if resource_limits.cpu_limit is not None:
                unenforced.append("cpu_limit")
        info = {**self.get_backend_info()}
        if unenforced:
            info["unenforced_limits"] = unenforced
            info["warning"] = (
                f"以下限制本后端无法强制执行，已忽略: {unenforced}。"
                "需要真资源隔离请改用 gVisor / Docker 后端。"
            )

        try:
            proc = subprocess.run(
                [sys.executable, "-c", _CHILD_BOOTSTRAP],
                input=source,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            # 真的执行了，但没跑完 —— 与「拒绝执行」和「后端不可用」是两回事。
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                error=f"执行超过 {timeout}s 被终止（子进程已 kill）。"
                      "注意：死循环靠子进程超时兜底，不是解释器主动打断。",
                status=ExecutionStatus.TIMEOUT,
                backend_info=info,
            )
        except Exception as exc:
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                status=ExecutionStatus.FAILED,
                backend_info=info,
            )

        raw = (proc.stdout or "").strip()
        if not raw:
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                error=f"子进程无输出；stderr={(proc.stderr or '')[:400]}",
                status=ExecutionStatus.FAILED,
                backend_info=info,
            )

        try:
            payload = json.loads(raw.splitlines()[-1])
        except Exception as exc:
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                error=f"无法解析子进程输出（{type(exc).__name__}: {exc}）: {raw[:300]}",
                status=ExecutionStatus.FAILED,
                backend_info=info,
            )

        if not payload.get("ok"):
            # 编译期/运行期因受限策略被挡 = 主动拒绝，与"跑了但失败"要区分开。
            status = (
                ExecutionStatus.REJECTED
                if payload.get("restricted")
                else ExecutionStatus.FAILED
            )
            return ExecutionResult(
                execution_id=execution_id,
                success=False,
                exit_code=1,
                error=payload.get("error") or "restricted execution failed",
                status=status,
                backend_info={**info, "stage": payload.get("stage")},
            )

        out = payload.get("result")
        stdout = payload.get("stdout") or ""
        return ExecutionResult(
            execution_id=execution_id,
            success=True,
            exit_code=0,
            stdout=stdout if out is None else (stdout + "\n" + out if stdout else out),
            output="" if out is None else out,
            status=ExecutionStatus.SUCCESS,
            backend_info=info,
        )

    # -- lifecycle --------------------------------------------------------

    def cleanup(self, execution_id: str) -> bool:
        # 子进程已在 execute 内同步回收，没有需要清理的残留句柄。
        # 这里不存在"假成功"的语义歧义：确实没有东西可清。
        return True

    def cleanup_all(self) -> None:
        return None

    def __repr__(self) -> str:  # pragma: no cover - 诊断用途
        state = "available" if self.is_available() else f"unavailable: {self._unavailable_reason}"
        return f"<RestrictedPythonBackend {state} timeout={self._default_timeout}s>"
