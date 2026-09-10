"""kernel foundation 包 — 干净环境 import 回归测试（Round 51）。

背景：``LiuHao-O/packages/kernel/config.py`` 曾在模块顶层实例化 ``Settings()``，
三个必填 ``LHX_*`` 环境变量缺失时 import 即崩。而 ``tests/test_liuhao_packages_facade.py``
用 ``os.environ.setdefault`` 注入这些变量，把这个故障"遮住"了（测试全绿，但全新
checkout 真实环境 import 必失败）。

修复后 ``config.py`` 改为惰性单例 ``get_settings()``（import 无副作用，首次访问
才验证）。本测试在 subprocess 里剥掉所有 ``LHX_*`` 变量，锁住这两个语义：
1. 干净环境 import 必须成功（import 期无副作用）；
2. 首次访问 settings 仍会因缺配置而 fail-fast（ValidationError，不静默降级）。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LHX_DIR = os.path.join(ROOT, "LiuHao-O")

CLEAN_IMPORT_SNIPPET = (
    "import sys; sys.path.insert(0, %r); "
    "import packages.kernel as k; "
    "assert hasattr(k, 'get_settings'), 'get_settings missing'; "
    "print('IMPORT_OK')" % LHX_DIR
)

FAIL_FAST_SNIPPET = (
    "import sys; sys.path.insert(0, %r); "
    "import packages.kernel as k; "
    "k.get_settings()" % LHX_DIR
)


def _clean_env():
    """父进程环境副本，剥掉所有 LHX_*（facade 测试会 setdefault 注入它们）。"""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("LHX_"):
            del env[key]
    return env


class TestKernelCleanImport:
    def test_import_without_lhx_env_succeeds(self):
        proc = subprocess.run(
            [sys.executable, "-c", CLEAN_IMPORT_SNIPPET],
            capture_output=True,
            text=True,
            env=_clean_env(),
            timeout=120,
        )
        assert "IMPORT_OK" in proc.stdout, (
            "干净环境（无 LHX_*）import packages.kernel 失败:\n%s" % proc.stderr
        )

    def test_settings_access_without_lhx_env_fails_fast(self):
        proc = subprocess.run(
            [sys.executable, "-c", FAIL_FAST_SNIPPET],
            capture_output=True,
            text=True,
            env=_clean_env(),
            timeout=120,
        )
        assert proc.returncode != 0, (
            "缺 LHX_* 配置时 get_settings() 应 fail-fast，却返回了 0"
        )
        assert "ValidationError" in proc.stderr, (
            "期望 pydantic ValidationError，实际:\n%s" % proc.stderr[-800:]
        )
