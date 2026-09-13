"""Vault availability must reflect reality, not a package that cannot exist.

What these tests protect
------------------------
``src/kernels/security/__init__.py`` used to advertise a Vault integration like
this::

    try:
        from vault_connect import VaultClient, secret_id
        VAULT_AVAILABLE = True
    except ImportError:
        VAULT_AVAILABLE = False

        class VaultClient:                       # fallback stub
            def read(self, *args, **kwargs):
                return {"data": None}

Measured on 2026-09-13, two separate things were wrong with that:

1. **``vault_connect`` does not exist.** ``pip index versions vault_connect``
   answers ``No matching distribution found``, and the name appears in no
   dependency manifest (``pyproject.toml`` / ``requirements.txt`` /
   ``oss-registry.yaml``). So ``VAULT_AVAILABLE`` was not "an optional
   dependency that happens to be missing" -- it was a branch that could
   *never* be taken. A phantom dependency.
2. **The fallback stub lied.** ``read()`` returned ``{"data": None}``, and
   ``write()`` / ``delete()`` discarded silently. A secret read therefore
   failed by returning nothing rather than by reporting anything -- the same
   "silent no-op" shape this project has been hunting down elsewhere.

Neither the stub, nor ``secret_id``, nor even ``VAULT_AVAILABLE`` had a single
consumer anywhere in the repo. The block was dead weight whose only effect was
to make the kernel look more capable than it is. It was replaced by a probe of
``hvac`` -- the library the real implementations
(``src/security/vault_client.py`` and ``src/integrations/vault/``) actually use.

Three things could rot back, so all three are pinned here:

* the phantom dependency cannot reappear -- as an import *or* as a declared dep;
* ``VAULT_AVAILABLE`` must equal the live ``hvac`` probe. This is a **control
  assertion**: it does not hardcode ``True`` or ``False``, it demands the flag
  agree with reality on whatever machine runs the suite;
* the kernel must not reach into the layers that own the real implementations.

Comments are deliberately excluded from the import scan: the replacement block
*names* the phantom package in prose to explain why it was removed, and honest
documentation should not trip a guard. The scan parses the AST instead.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
KERNEL_SECURITY = REPO_ROOT / "src" / "kernels" / "security" / "__init__.py"

PHANTOM = "vault_connect"
SCAN_DIRS = ("src", "scripts", "tests")
MANIFESTS = ("pyproject.toml", "requirements.txt", "oss-registry.yaml")

# Layers that own the real Vault implementations. The kernel sits *below* them,
# so importing either one from a kernel module would be an inverted dependency.
FORBIDDEN_INNER_LAYERS = ("src.security", "src.integrations")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _python_sources():
    for rel in SCAN_DIRS:
        base = REPO_ROOT / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _imports_module(source: str, module_name: str) -> bool:
    """True if *source* imports *module_name* (or a submodule of it).

    AST-based on purpose: a mere mention in a comment or docstring is not an
    import, and this repo documents its removals in prose.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_name or alias.name.startswith(module_name + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == module_name or node.module.startswith(module_name + ".")
            ):
                return True
    return False


def _defined_classes(source: str) -> set[str]:
    return {
        node.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ClassDef)
    }


# --------------------------------------------------------------------------- #
# 1. the phantom dependency cannot come back
# --------------------------------------------------------------------------- #
def test_no_module_imports_the_phantom_dependency():
    offenders = []
    for path in _python_sources():
        source = path.read_text(encoding="utf-8", errors="replace")
        if _imports_module(source, PHANTOM):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        f"{PHANTOM!r} does not exist on PyPI and is declared nowhere; "
        f"these files would import a module that can never be installed: {offenders}"
    )


def test_no_manifest_declares_the_phantom_dependency():
    declared = []
    for name in MANIFESTS:
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        if PHANTOM in path.read_text(encoding="utf-8", errors="replace"):
            declared.append(name)
    assert declared == [], f"{PHANTOM!r} must not be declared as a dependency: {declared}"


# --------------------------------------------------------------------------- #
# 2. the flag must equal reality (control assertion, not a hardcoded value)
# --------------------------------------------------------------------------- #
def test_vault_available_matches_the_live_hvac_probe():
    import src.kernels.security as security_kernel

    hvac_installed = importlib.util.find_spec("hvac") is not None
    assert security_kernel.VAULT_AVAILABLE == hvac_installed, (
        "VAULT_AVAILABLE must be derived from the real client library "
        f"(hvac importable={hvac_installed}, flag={security_kernel.VAULT_AVAILABLE}). "
        "If it is hardcoded, or tied to a package that cannot be installed, it "
        "stops telling the truth."
    )


# --------------------------------------------------------------------------- #
# 3. no silent stub, no inverted dependency
# --------------------------------------------------------------------------- #
def test_kernel_does_not_redefine_a_vault_client_stub():
    source = KERNEL_SECURITY.read_text(encoding="utf-8")
    defined = _defined_classes(source)
    assert "VaultClient" not in defined, (
        "The kernel must not define its own VaultClient. The previous fallback "
        "stub returned {'data': None} from read() -- a silent no-op. The real "
        "clients live in src/security/ and src/integrations/vault/."
    )


def test_kernel_does_not_import_the_layers_owning_real_vault():
    source = KERNEL_SECURITY.read_text(encoding="utf-8")
    inverted = [m for m in FORBIDDEN_INNER_LAYERS if _imports_module(source, m)]
    assert inverted == [], (
        f"src/kernels/ must not import {inverted}: the kernel sits below those "
        "layers (spec -> kernels -> ai -> gateway)."
    )


# --------------------------------------------------------------------------- #
# 4. meta-guard: prove the phantom check can actually fail
# --------------------------------------------------------------------------- #
def test_phantom_detector_rejects_a_synthetic_offender():
    """A guard that cannot fail is a rubber stamp.

    The two snippets below are exactly the shapes that were removed. If
    ``_imports_module`` ever stops seeing them, the guard above has quietly
    become decorative.
    """
    offending_sources = [
        "from vault_connect import VaultClient, secret_id\n",
        "import vault_connect\n",
        "import vault_connect.client\n",
    ]
    for source in offending_sources:
        assert _imports_module(source, PHANTOM) is True, (
            f"detector missed a real offender: {source!r}"
        )

    # And it must not fire on prose that merely mentions the name, otherwise
    # honest documentation of the removal would break the suite.
    innocent_source = (
        "# 此处原本是 from vault_connect import VaultClient —— 已移除\n"
        "VAULT_AVAILABLE = False\n"
    )
    assert _imports_module(innocent_source, PHANTOM) is False
