"""Regression test: the generated cloud bundle must arm CRITICAL, not HIGH,CRITICAL.

Decision D24 (2026-09-12) narrowed the production deployment enforcement to
CRITICAL only. The shippable cloud bundle's launcher template
(``scripts/build_cloud_bundle.py`` -> ``_LAUNCHER``) previously armed
``HIGH,CRITICAL``; this test proves the *actual* launcher source -- the
``_LAUNCHER`` template after the same placeholder substitutions ``build()``
performs -- arms ``CRITICAL`` and contains no ``HIGH,CRITICAL``.

This is a runtime probe of the real artifact content, not a test-expectation
change: it reads the template directly and (when the console build output is
present) runs the real ``build()`` and inspects the generated ``serve.py``.
"""
import importlib.util
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Load scripts/build_cloud_bundle.py as a module (scripts/ has no __init__.py).
_BUNDLE_PATH = os.path.join(REPO_ROOT, "scripts", "build_cloud_bundle.py")
_spec = importlib.util.spec_from_file_location("build_cloud_bundle", _BUNDLE_PATH)
bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle)

ARMING_LINE = 'os.environ.setdefault("LIUHAO_KERNEL_POLICY_ENFORCE", "CRITICAL")'


def _rendered_launcher() -> str:
    """Apply the same placeholder substitutions build() applies."""
    source = bundle._LAUNCHER
    source = source.replace(bundle.LLM_PLACEHOLDER, "")
    source = source.replace(bundle.JWT_PLACEHOLDER, "")
    return source


def test_bundle_launcher_template_arms_critical_not_high_critical():
    """The _LAUNCHER template, after build()'s substitutions, arms CRITICAL."""
    launcher = _rendered_launcher()
    # (a) the arming line sets exactly "CRITICAL"
    assert 'LIUHAO_KERNEL_POLICY_ENFORCE' in launcher
    assert ARMING_LINE in launcher
    # (b) no HIGH,CRITICAL anywhere in the rendered launcher
    assert "HIGH,CRITICAL" not in launcher


def test_bundle_full_build_arms_critical(tmp_path):
    """If the console dist exists, run the real build and check generated serve.py.

    Skipped otherwise -- the full build requires the frontend
    (apps/console/console/dist/index.html).
    """
    console_index = bundle.CONSOLE_DIST_SRC / "index.html"
    if not console_index.is_file():
        pytest.skip("console dist missing; full build requires the frontend")

    out = tmp_path / "bundle"
    rc = bundle.build(out)
    assert rc == 0, "build_cloud_bundle.py returned non-zero"

    serve_py = out / bundle.LAUNCHER_NAME
    assert serve_py.is_file(), "generated serve.py missing"
    text = serve_py.read_text(encoding="utf-8")
    assert ARMING_LINE in text
    assert "HIGH,CRITICAL" not in text
