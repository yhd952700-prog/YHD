"""Tk 生命周期护栏（测试侧公共实现，目录级与单文件级共用同一套检查）。

为什么需要它（本机 Windows + CPython 3.12.14 + tcl86t.dll 实测）：

* ``_tkinter.TkappType``（Tcl 解释器）**不被 CPython 的循环 GC 追踪**
  （``gc.is_tracked() == False``、``gc.get_objects()`` 看不到、不支持弱引用），
  只能靠引用计数释放；而 ``tkinter.Tk`` / ``Toplevel`` / ``Variable`` 是被追踪的。
* 所以一旦 ``Tk`` 对象落进引用环，它要等到 cyclic GC 才回收；GC 若恰好跑在
  后台线程上，回收 ``Tk`` 会连带 dealloc 那个 tkapp，``Tcl_AsyncDelete`` 便执行
  ``Tcl_Panic("Tcl_AsyncDelete: async handler deleted by the wrong thread")``。
  Windows 上 panic 落到 ``tcl86t.dll`` 的 ``Tcl_PanicVA`` -> ``int 3``，
  进程以 ``0xC0000003``（``-2147483645``）abort，pytest 完全拦不住。
  这就是「全量套件跑到 9% 左右进程级 abort」的根因。
* 若反过来先 ``destroy()`` 再让控件/变量析构，它们会在已删除的 Tcl 解释器上
  dealloc，产生 native 访问违例（``Windows fatal exception: page error``）。

两个入口（检查实现只有 :func:`assert_tk_reclaimed` 一份）：

* 目录级 —— ``tests/desktop/conftest.py`` 导入 :func:`pytest_runtest_teardown`，
  覆盖整个 ``tests/desktop`` 子树；
* 单文件级 —— ``tests/test_first_run_gui.py`` 导入 :func:`tk_lifecycle_guard`
  这个 autouse fixture（测试模块不能注册 hook，只能用 fixture）。

两者都在**全部 fixture teardown 之后**、pytest 主线程里执行：先 ``gc.collect()``
一轮；仍有 Tk 顶层/变量存活就明确失败并给出引用者摘要，而不是留给后台线程的
GC 把整个进程带走。
"""

from __future__ import annotations

import gc
import sys

import pytest

#: 引用者摘要最多打印几个，避免失败信息被无关对象淹没。
_MAX_REFERRERS = 5


def _snapshot_tk_types() -> tuple[type, ...]:
    """在 import 时固定原始类型，测试里对 ``tkinter.Tk`` 的 monkeypatch 不影响判断。"""
    module = sys.modules.get("tkinter")
    if module is None:
        try:
            import tkinter as module  # noqa: PLC0415 - 只为固定类型，不创建窗口
        except Exception:  # noqa: BLE001 - 无 tkinter 的环境直接禁用护栏
            return ()
    candidates = (
        getattr(module, "Tk", None),
        getattr(module, "Toplevel", None),
        getattr(module, "Variable", None),
    )
    return tuple(obj for obj in candidates if isinstance(obj, type))


_TK_TYPES: tuple[type, ...] = _snapshot_tk_types()


def live_tk_objects() -> list[object]:
    """仍存活的 Tk 顶层/变量对象（它们持有不可追踪的 tkapp，是崩溃的载体）。"""
    if not _TK_TYPES:
        return []
    return [obj for obj in gc.get_objects() if isinstance(obj, _TK_TYPES)]


def _describe_referrers(obj: object, ignore: tuple[object, ...] = ()) -> str:
    lines: list[str] = []
    for ref in gc.get_referrers(obj):
        # 跳过护栏自身制造出来的引用（leaked 列表、列表推导的 genexpr 帧），
        # 否则诊断信息会被自己淹没。
        if any(ref is item for item in ignore):
            continue
        code = getattr(ref, "gi_code", None) or getattr(ref, "f_code", None)
        if code is not None and code.co_filename.endswith("_tk_thread_guard.py"):
            continue
        try:
            lines.append(f"{type(ref).__name__}: {repr(ref)[:120]}")
        except Exception:  # noqa: BLE001 - 只是诊断信息
            lines.append(f"{type(ref).__name__}: <unrepr>")
        if len(lines) >= _MAX_REFERRERS:
            break
    return " | ".join(lines) if lines else "<no referrers>"


def assert_tk_reclaimed(nodeid: str) -> None:
    """在 pytest 主线程里回收一轮；仍有 Tk 存活则明确失败（绝不留给后台 GC）。"""
    if not _TK_TYPES:
        return
    leaked = live_tk_objects()
    if not leaked:
        return
    # 只在确实有存活对象时才付 full GC 的代价，并且限定在 pytest 主线程里做，
    # 保证 tkapp 的 dealloc 落回创建它的线程。
    gc.collect()
    leaked = live_tk_objects()
    if not leaked:
        return
    details = "；".join(_describe_referrers(obj, ignore=(leaked,)) for obj in leaked)
    pytest.fail(
        f"测试结束后仍有 {len(leaked)} 个 Tk 对象存活（{nodeid}），"
        "引用者：" + details + "。tkapp 不被循环 GC 追踪，只能靠引用计数释放；"
        "若它的最后一个引用在后台线程被回收，Tcl_AsyncDelete 会触发 Tcl_Panic "
        "-> int3，整个 pytest 进程以 0xC0000003 abort。请在创建 Tk 的同一线程里"
        "先断开控件/变量引用、再 destroy()、最后释放 root。",
        pytrace=False,
    )


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """目录级入口：conftest 导入本函数即可覆盖该目录下全部测试。"""
    assert_tk_reclaimed(item.nodeid)


@pytest.fixture(autouse=True)
def tk_lifecycle_guard(request: pytest.FixtureRequest):
    """单文件级入口：模块里导入本 fixture 即对所有用例生效（autouse）。"""
    yield
    assert_tk_reclaimed(request.node.nodeid)
