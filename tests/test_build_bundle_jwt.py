"""发布包启动脚本**必然**固定 JWT 签名密钥的回归测试。

``scripts/build_cloud_bundle.py`` 生成 ``serve.py``。若它不固定签名密钥，发布
出来的应用就会在每个进程里现生成密钥 —— 重启即全端登出，多 worker 时登录
「成功」而下一个请求 401。这是那种在本地单进程跑得好好的、一上线才发作的
缺陷，所以在构建期就钉死：

1. ``_render_jwt_block`` **永远**产出 ``os.environ.setdefault('LIUHAO_JWT_SECRET', ...)``；
2. 构建环境给了密钥就用它，没给就生成一把强的（非空、且每次都不同）；
3. 完整渲染后两个占位符都被消费、产物是合法 Python；
4. 产出的 ``serve.py`` 至少能被解析，且密钥确实进了环境变量赋值。
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "build_cloud_bundle.py"
SECRET_ENV = "LIUHAO_JWT_SECRET"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_cloud_bundle", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_cloud_bundle"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


def _render(builder) -> str:
    src = builder._LAUNCHER.replace(
        builder.LLM_PLACEHOLDER, builder._render_llm_block()
    )
    return src.replace(builder.JWT_PLACEHOLDER, builder._render_jwt_block())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(SECRET_ENV, raising=False)


def test_block_always_emits_a_pinned_secret(builder):
    block = builder._render_jwt_block()
    assert f"os.environ.setdefault({SECRET_ENV!r}" in block
    # 非空密钥：空串会被 app 读作「未配置」而退回逐进程密钥，等于没修。
    assert "''" not in block.split("setdefault", 1)[1][:80]


def test_build_env_secret_is_honoured(builder, monkeypatch):
    monkeypatch.setenv(SECRET_ENV, "operator-chosen-secret-0123456789")
    assert "operator-chosen-secret-0123456789" in builder._render_jwt_block()


def test_generated_secret_is_random_and_strong(builder):
    first = builder._render_jwt_block()
    second = builder._render_jwt_block()
    assert first != second, "未提供密钥时必须每次生成新的一把，而不是写死"
    # 至少 32 字符，避免把弱密钥烘进发布包。
    import re

    value = re.search(r"setdefault\('LIUHAO_JWT_SECRET', '([^']+)'\)", first).group(1)
    assert len(value) >= 32


def test_full_launcher_consumes_placeholders_and_parses(builder):
    src = _render(builder)
    assert builder.LLM_PLACEHOLDER not in src
    assert builder.JWT_PLACEHOLDER not in src
    ast.parse(src)  # 产物必须是合法 Python


def test_rendered_secret_reaches_the_environment(builder):
    import os

    src = _render(builder)
    # 只执行到 setdefault 为止，避免真的把网关拉起来。``__file__`` 指向一个
    # 不存在的包目录：模板用它做 isdir/isfile 判断，为假即跳过，正是我们要的。
    namespace = {"__file__": str(REPO_ROOT / "deploy" / "cloud" / "serve.py")}
    head = src.split("from src.gateway.__main__ import main")[0]

    snapshot = dict(os.environ)
    try:
        exec(compile(head, "serve_head", "exec"), namespace)
        assert os.environ.get(SECRET_ENV), "serve.py 必须把密钥写进环境变量"
    finally:
        os.environ.clear()
        os.environ.update(snapshot)
