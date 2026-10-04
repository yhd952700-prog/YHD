"""护栏自证：测试不得删除仓库工作树内的文件（2026-10-04 事故的直接对策）。

这个文件的存在意义不是"再测一遍功能"，而是让**护栏本身可被证伪**。一个从不
失败的护栏和一个不存在的护栏，在测试报告里长得一模一样。因此这里双向证明：

* 正向（护栏该放行的放行）：删除 ``tmp_path`` 内的文件**必须成功**；
* 反向（护栏该拦的拦住）：删除仓库内的文件**必须失败**，且失败信息里必须同时
  看得见**完整路径**和**调用栈**——否则报错等于没说；
* 豁免机制：登记后放行、缺原因拒绝、登记时已存在拒绝、腐化能被检出。

双向证明的另一半在 ``test_guard_reports_the_offending_test`` 里：把一个真实的
越界删除包进 ``pytester`` 子进程运行，断言子进程**红**并精确点名那个测试。
只测"护栏会报错"是不够的——必须证明它在**真实的 pytest 生命周期**里也报错。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFTEST = REPO_ROOT / "tests" / "conftest.py"

# 本文件自己在仓库内创建、跑完即删的探针。它们是"本次运行创建"的 legit 产物，
# 用来证明白名单机制本身可用；用 try/finally 保证一定清理。
_SELF_PROBE = REPO_ROOT / "tests" / "_zz_guard_selfcheck.tmp"
_SELF_PROBE_DIR = REPO_ROOT / "tests" / "_zz_guard_selfcheck_dir"


# ==================== 正向：临时目录内的删除必须放行 ====================


def test_deleting_inside_tmp_path_is_allowed(tmp_path):
    """tmp_path 下的文件随便删——这是 99% 测试的正常行为，护栏不能挡它。"""
    victim = tmp_path / "scratch.json"
    victim.write_text("{}", encoding="utf-8")
    os.remove(str(victim))
    assert not victim.exists()

    # Path.unlink 走的是 os.unlink，必须一并放行。
    other = tmp_path / "scratch2.json"
    other.write_text("{}", encoding="utf-8")
    other.unlink()
    assert not other.exists()

    # missing_ok 语义也不能被护栏改变。
    (tmp_path / "never_existed.json").unlink(missing_ok=True)


def test_rmtree_inside_tmp_path_is_allowed(tmp_path):
    """shutil.rmtree 在临时目录内同样放行。"""
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f.txt").write_text("x", encoding="utf-8")
    shutil.rmtree(str(tree))
    assert not tree.exists()


def test_the_host_shim_bypass_and_this_guard_agree_on_the_temp_root():
    """护栏放行的临时根必须与宿主 safe-delete shim 的 bypass 根一致。

    不一致会产生最难查的一类假失败：护栏放行、宿主 shim 却抛 SystemExit(1)，
    于是 SystemExit 又落回 xunit 钩子，重演 2026-10-04 的 finalizer 栈损坏。
    两边判据必须锚在同一个 ``tempfile.gettempdir()`` 上。
    """
    import tempfile

    guard_root = Path(tempfile.gettempdir()).resolve()
    assert guard_root == Path(os.path.abspath(tempfile.gettempdir())).resolve()
    # 本机事实（2026-10-04 实测）：临时根在仓库之外，因此"临时根 ∩ 仓库"为空。
    # 若将来有人在仓库内设 TEMP，本护栏会退化成"什么都拦"，必须在这里变红。
    assert not str(guard_root).lower().startswith(str(REPO_ROOT).lower()), (
        "临时根 %s 落在仓库 %s 之内：放行区与拒绝区重叠，护栏会退化为拦下一切。"
        % (guard_root, REPO_ROOT)
    )


# ==================== 反向：仓库内的删除必须被拒 ====================


def test_deleting_a_repo_file_is_rejected_with_path_and_stack():
    """删除仓库内文件必须 pytest.fail，且信息里有完整路径和调用栈。"""
    with pytest.raises(BaseException) as excinfo:
        os.remove(str(REPO_ROOT / "README.md"))
    message = str(excinfo.value)
    assert "README.md" in message, f"报错没有点名被删的文件：\n{message}"
    assert str(REPO_ROOT / "README.md") in message, (
        f"报错没有给出完整路径：\n{message}"
    )
    assert "test_deleting_a_repo_file_is_rejected_with_path_and_stack" in message, (
        f"报错没有点名当前用例：\n{message}"
    )
    # 调用栈：必须能看到发起删除的那一行在哪个文件里。
    assert "test_cwd_delete_guard.py" in message, (
        f"报错里没有调用栈（应指向发起删除的测试文件）：\n{message}"
    )
    # 判据本身也要写在报错里，否则下一个人不知道怎么合法地删。
    assert "allow_repo_deletion" in message, f"报错没有告诉人怎么豁免：\n{message}"
    # 文件必须真的还在——护栏是先拦后删，不存在"拦了但已经删了"。
    assert (REPO_ROOT / "README.md").is_file()


def test_a_cwd_relative_repo_path_is_rejected():
    """CWD 相对路径也必须被拦——2026-10-04 事故的**原始形态**就是相对路径。

    进程 CWD 是仓库根，所以 ``os.remove('README.md')`` 指向仓库内的真实文件。
    如果护栏只认绝对路径，这个事故会原样重演。测试里用 monkeypatch.chdir
    把 CWD 切到一个 tmp 目录来验证相对路径确实被按 CWD 解析——这正是生产代码
    （如 ``src/knowledge/memory.py`` 的 ``MEMORY_PERSIST_DIR``兜底值``memory_data``）
    的行为模型。
    """
    with pytest.raises(BaseException) as excinfo:
        os.remove("README.md")
    message = str(excinfo.value)
    assert str(REPO_ROOT / "README.md") in message, (
        f"相对路径未被按 CWD 解析成绝对路径，报错里看不到真实目标：\n{message}"
    )


def test_repo_inventory_and_frozen_evidence_are_inside_the_denial_surface():
    """护栏的拒绝面必须覆盖整个仓库，包括 git 跟踪的核心文件。

    特别是仓库根的 ``audit_store.db``（冻结的 HC-01 证据）：任何测试试图删除
    它都会当场变红，而不是悄悄毁掉证据。
    """
    victim = REPO_ROOT / "audit_store.db"
    if not victim.exists():
        pytest.skip("audit_store.db 不在本次检出中")
    with pytest.raises(BaseException) as excinfo:
        os.remove(str(victim))
    assert "audit_store.db" in str(excinfo.value)
    assert victim.exists(), "audit_store.db 被删了——护栏没能保护冻结证据"


def test_path_unlink_is_also_guarded():
    """``Path.unlink()`` 同样必须被拦 —— 本护栏第一版在这里是**瞎的**。

    实测踩到的洞：本机CodeBuddy CLI 的 ``sitecustomize.py`` shim 在导入期重绑了
    ``pathlib.Path.unlink``，其放行分支直连原生实现而**不经过** ``os.unlink``。
    第一版只patch 了 ``os.unlink``，于是 ``Path(...).unlink()`` 从补丁下方溜走。
    而仓库里现役的测试恰恰大量用这个 API（``tests/test_oss_ecosystem_entries.py:163``、
    ``tests/test_knowledge_memory.py:48``）—— 护栏在真正需要它的地方反而失效。

    这条断言是那个洞的回归锁：它必须显式覆盖 ``Path.unlink``，而不是靠
    "``os.unlink`` 会被调到"这个假设。
    """
    with pytest.raises(BaseException) as excinfo:
        (REPO_ROOT / "README.md").unlink()
    message = str(excinfo.value)
    assert "pathlib.Path.unlink" in message, (
        f"Path.unlink 未被护栏覆盖（报错里的接口名不对）：\n{message}"
    )
    assert (REPO_ROOT / "README.md").is_file()


def test_path_rmdir_is_also_guarded():
    """``Path.rmdir()`` 同理——它也能删掉仓库里的东西，且同样绕过 ``os.rmdir``。"""
    with pytest.raises(BaseException) as excinfo:
        (REPO_ROOT / "oss-ecosystem" / "capabilities").rmdir()
    assert "pathlib.Path.rmdir" in str(excinfo.value)
    assert (REPO_ROOT / "oss-ecosystem" / "capabilities").is_dir()


def test_rmtree_of_a_repo_directory_is_rejected():
    """rmtree 是最危险的一个：它不报错，只是让目录连同内容一起消失。"""
    with pytest.raises(BaseException) as excinfo:
        shutil.rmtree(str(REPO_ROOT / "oss-ecosystem" / "capabilities"))
    message = str(excinfo.value)
    assert "shutil.rmtree" in message
    assert (REPO_ROOT / "oss-ecosystem" / "capabilities").is_dir(), (
        "capabilities/ 被 rmtree 掉了——护栏失效"
    )


def test_a_repo_subdirectory_is_rejected_not_just_the_root(tmp_path):
    """拒绝面必须覆盖子目录，而不只是仓库根那一层。"""
    with pytest.raises(BaseException) as excinfo:
        os.unlink(str(REPO_ROOT / "src" / "knowledge" / "memory.py"))
    assert "memory.py" in str(excinfo.value)
    assert (REPO_ROOT / "src" / "knowledge" / "memory.py").is_file()


# ==================== 豁免机制 ====================


def test_allowlist_lets_a_self_created_repo_path_through(allow_repo_deletion):
    """测试自建、跑完即删的仓库内路径：登记后放行。"""
    target = allow_repo_deletion.register(
        _SELF_PROBE, reason="护栏自证：本文件自建的临时探针，跑完即删"
    )
    assert not target.exists(), "register 返回的路径应指向一个尚不存在的文件"
    try:
        target.write_text("probe", encoding="utf-8")
        target.unlink()  # 这一句若无白名单会 pytest.fail
        assert not target.exists()
    finally:
        if target.exists():
            target.unlink()


def test_allowlist_requires_a_non_empty_reason(allow_repo_deletion):
    """白名单条目必须带原因，且原因不能是空白。

    没有原因的豁免无法被复核：下一个人无法判断它是深思熟虑还是懒得改。
    做法照抄 tests/test_guardrail_scripts.py 对 DIAGNOSTIC_REPORTERS 的要求。
    """
    for bad in ("", "   ", "\n\t "):
        with pytest.raises(AssertionError) as excinfo:
            allow_repo_deletion.register(_SELF_PROBE, reason=bad)
        assert "reason" in str(excinfo.value)


def test_allowlist_refuses_a_path_that_already_exists(allow_repo_deletion):
    """登记时已存在的路径不是"本次运行创建的"，必须拒绝登记。

    这一条是白名单的防滥用核心：否则任何测试都能登记 ``README.md`` 然后删掉它，
    护栏就等于没装。
    """
    existing = REPO_ROOT / "README.md"
    with pytest.raises(AssertionError) as excinfo:
        allow_repo_deletion.register(existing, reason="想删仓库文件")
    assert "已经存在" in str(excinfo.value)
    assert existing.is_file()


def test_allowlist_is_single_use(allow_repo_deletion):
    """一次登记只消费一次删除，防止一个登记被复用来连续删一批文件。

    残留清理也走合法路径：外层目录**提前**登记，因此收尾的 rmtree 是被放行的
    （这同时证明了 rmtree + 登记的组合可用）。
    """
    outer = allow_repo_deletion.register(
        _SELF_PROBE_DIR, reason="护栏自证：外层目录，收尾时整目录 rmtree"
    )
    outer.mkdir(parents=True, exist_ok=True)
    target = allow_repo_deletion.register(
        outer / "inner.tmp", reason="护栏自证：验证一次性消费"
    )
    target.write_text("a", encoding="utf-8")
    target.unlink()  # 第一次：放行，登记被消费
    target.write_text("b", encoding="utf-8")
    with pytest.raises(BaseException) as excinfo:
        target.unlink()  # 第二次：登记已失效，必须被拦
    assert "登记" in str(excinfo.value)
    # 收尾：外层目录的登记仍未消费，这一次 rmtree 合法通过。
    shutil.rmtree(str(outer))
    assert not outer.exists()


def test_rot_is_detected_when_a_registration_is_never_consumed(allow_repo_deletion):
    """登记了却没删 = 白名单腐化，护栏必须自己发现。

    这是"白名单本身的腐化要能被检出"的要求：如果腐化只能靠人 review diff
    发现，它早晚会烂掉。这里通过直接调用 finalize 模拟用例结束时的检查。
    """
    allow_repo_deletion.register(
        _SELF_PROBE, reason="护栏自证：登记后故意不删，验证腐化能被检出"
    )
    with pytest.raises(BaseException) as excinfo:
        allow_repo_deletion.finalize()
    message = str(excinfo.value)
    assert "腐化" in message
    assert "从未被删除" in message
    # 检查完必须清空，否则污染后续用例。
    assert not allow_repo_deletion._entries


def test_each_test_gets_a_fresh_allowlist(allow_repo_deletion):
    """登记表必须逐用例独立，否则一个用例的残留会污染下一个。

    这不是洁癖：本护栏第一版把 ``_entries`` 放在类上（模块级共享），结果
    "上一个用例登记了但没删"变成"下一个用例莫名报『已被登记』"——失败现场与
    真实原因相隔一个测试，比没有护栏更难查。

    （本用例也顺带证明腐化检测不是摆设：下面必须真的消费掉登记，否则 teardown
    会报"登记后从未被删除"。）
    """
    assert allow_repo_deletion._entries == {}
    target = allow_repo_deletion.register(
        _SELF_PROBE, reason="护栏自证：本用例的登记表是空的"
    )
    assert len(allow_repo_deletion._entries) == 1
    target.write_text("probe", encoding="utf-8")
    target.unlink()  # 消费掉登记，teardown 的腐化检测才会通过
    assert not target.exists()


def test_allowlist_refuses_paths_outside_the_repo(allow_repo_deletion, tmp_path):
    """登记仓库外的路径是误解：那里的删除本来就被放行。"""
    with pytest.raises(AssertionError) as excinfo:
        allow_repo_deletion.register(tmp_path / "x.json", reason="多余的登记")
    assert "无需登记" in str(excinfo.value)


# ==================== 双向证明：真实 pytest 生命周期里的红/绿 ====================

#: 沙箱必须**既不在**任何临时根之下（否则护栏按判据就该放行，证不了任何事），
#: **也不在**真实仓库之内（否则父进程要删它就必须走白名单，把两件事搅在一起）。
#: tempfile.gettempdir() 的父目录正好满足这两条，且与临时根同盘、可写。
_SANDBOX_PARENT = Path(tempfile.gettempdir()).resolve().parent

_SUBPROCESS_TEST = '''
import os

import pytest


def test_this_is_the_offender():
    # 复刻 2026-10-04 的真实成因：CWD 相对路径的删除。进程 CWD 是沙箱仓库根，
    # 所以这一句瞄准的是沙箱里的真实文件。
    os.remove("README.md")
'''

#: 护栏代码块在 conftest.py 里的起止标记。子进程复用**同一份**护栏源码
#: （而不是抄一份），因此这里证明的就是真正上线的那段代码。
_GUARD_BEGIN = "# ==================== Guard: tests must not delete files in the work tree ===="
_GUARD_END = "# ==================== Console auth (bearer token) ===================="


def _guard_source() -> str:
    """从真实 conftest.py 里切出护栏代码块，作为子进程 conftest 的全部内容。"""
    text = CONFTEST.read_text(encoding="utf-8")
    start = text.index(_GUARD_BEGIN)
    end = text.index(_GUARD_END)
    block = text[start:end]
    assert "def _forbid_deleting_repository_files(" in block, (
        "护栏代码块里找不到 _forbid_deleting_repository_files —— "
        "conftest.py 的结构变了，本测试的切分前提失效"
    )
    return (
        '"""子进程专用 conftest：只含仓库删除护栏（源码取自真实 conftest.py）。"""\n'
        "import pytest\n\n" + block
    )


def test_guard_reports_the_offending_test_in_a_real_pytest_run():
    """把越界删除放进**真实的 pytest 子进程**跑一遍，证明护栏会当场变红。

    为什么必须起子进程：同进程内 ``pytest.fail`` 已被本文件上面的用例捕获并断言
    过，但那只证明"异常类型与文案正确"。子进程证明的是**因果链完整**——护栏装上
    之后，一个普通的、看起来人畜无害的测试里的 ``os.remove('README.md')`` 真的会
    让整轮跑红，并且红在**这个测试自己身上**，而不是二十个不相干的测试身上。

    沙箱放在临时根的**父目录**下：若沙箱落在 tmp_path 里，护栏按判据就该放行
    （那是临时目录），这条测试会变成一个自己骗自己的假证明。
    """
    sandbox = _SANDBOX_PARENT / ("guard_sandbox_%d" % int(time.time()))
    if sandbox.exists():  # 极小概率的同名残留
        shutil.rmtree(str(sandbox), ignore_errors=True)
    (sandbox / "tests").mkdir(parents=True)
    (sandbox / "tests" / "conftest.py").write_text(_guard_source(), encoding="utf-8")
    (sandbox / "tests" / "test_offender.py").write_text(
        _SUBPROCESS_TEST, encoding="utf-8"
    )
    (sandbox / "README.md").write_text("sandbox readme\n", encoding="utf-8")

    env = dict(os.environ)
    # basetemp 放进沙箱内：既避开宿主 safe-delete shim 对 pytest 旧 basetemp 的
    # 批量回收（它会抛 SystemExit(1) 污染子进程输出），又顺带走一遍护栏的
    # --basetemp 登记分支（沙箱在"仓库"内，所以 basetemp 不在 gettempdir 之下）。
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(REPO_ROOT), env.get("PYTHONPATH", "")]
    )
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(sandbox / "tests" / "test_offender.py"),
                "-q", "--no-header", "-p", "no:cacheprovider",
                "--basetemp", str(sandbox / ".pytest_tmp"),
            ],
            cwd=str(sandbox),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0, (
            "护栏没拦住子进程里的越界删除，护栏是假的：\n" + output
        )
        # 红在**这个**测试上，而不是别处 —— 这正是 2026-10-04 事故缺的因果链。
        assert "test_this_is_the_offender" in output, (
            "护栏拦下了删除，但没有点名肇事的测试：\n" + output
        )
        # 路径与调用栈都在。
        assert "README.md" in output, f"报错里没有路径：\n{output}"
        assert "test_offender.py" in output, f"报错里没有调用栈：\n{output}"
        # 而且文件真的还在。
        assert (sandbox / "README.md").is_file(), (
            "README.md 真的被删了 —— 护栏是先拦后删这条不成立"
        )
    finally:
        shutil.rmtree(str(sandbox), ignore_errors=True)


def test_the_temporary_injection_used_for_proof_is_gone():
    """反向证明的收尾：验证用的临时注入不得留在仓库里。

    这条断言本身是对本文件的一次自检——如果哪天有人把 ``os.remove('README.md')``
    临时塞进某个测试而忘了恢复，这条会红。
    """
    assert not _SELF_PROBE.exists(), (
        "%s 残留在仓库里；护栏自证的临时产物必须跑完即删" % _SELF_PROBE
    )
    assert not _SELF_PROBE_DIR.exists(), (
        "%s 残留在仓库里；护栏自证的临时产物必须跑完即删" % _SELF_PROBE_DIR
    )


# ==================== 护栏自身的配置自检 ====================


def test_the_guard_is_wired_into_the_real_conftest():
    """护栏必须真的活在 tests/conftest.py 里，而不是只活在本文档里。"""
    source = CONFTEST.read_text(encoding="utf-8")
    assert "def _forbid_deleting_repository_files(" in source
    assert 'scope="session", autouse=True' in source
    for symbol in ("guarded_remove", "guarded_unlink", "guarded_rmtree",
                   "guarded_path_unlink", "guarded_path_rmdir"):
        assert symbol in source, "护栏漏掉了 %s" % symbol


def test_repo_root_resolution_is_correct():
    """仓库根 = conftest.py 上溯一级。判据锚错了地方，护栏就没意义。"""
    assert REPO_ROOT == CONFTEST.resolve().parent.parent
    assert (REPO_ROOT / "tests" / "conftest.py").is_file()
    assert (REPO_ROOT / ".git").exists() or (REPO_ROOT / "src").is_dir()
