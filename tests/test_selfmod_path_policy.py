"""禁区 ∩ 可改范围 = ∅ — probe paths from SELF-EVOLUTION.md §2 must be refused.

The probe list is written from the spec, not from ``paths.py``, so deleting a
guard from ``paths.py`` makes this test fail instead of silently shrinking the
oracle. The positive control at the bottom keeps the refusal list honest: a
policy that refuses *everything* would pass the first half vacuously.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.selfmod import PathNotAllowed, PathPolicy

# §2.1 Authentication / authorization
_SPEC_PROHIBITED = [
    "src/security/abac.py",
    "src/security/secret_store.py",
    "src/identity/principal.py",
    "src/gateway/auth.py",
    "src/gateway/policy.py",
    "src/gateway/rbac.py",
    "src/gateway/principal_store.py",
    # §2.2 Secrets / key material
    ".env",
    ".env.production",
    "config/auth_secrets.json",
    "config/docker.env",
    "secrets/gateway.json",
    "deploy/server.pem",
    "deploy/server.key",
    "deploy/client.crt",
    "certs/wildcard.pfx",
    "certs/keystore.p12",
    "certs/signing.jks",
    # §2.3 Database schema
    "alembic/versions/0001_init.py",
    "migrations/0001_init.sql",
    "init-scripts/002_seed.sql",
    "src/integrations/orm_models.py",
    "src/knowledge/db.py",
    # §2.4 Installer / packaging
    "installer/setup.iss",
    "packaging/build_package.ps1",
    "scripts/launch_installed.py",
    "scripts/start_liuhao.py",
    "scripts/first_run_gui.py",
    "scripts/first_run_wizard.py",
    "scripts/liuhao_desktop.py",
    "start.bat",
    "install.ps1",
    # §2.5 Dependency manifests
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "uv.lock",
    "oss-registry.yaml",
    ".nvmrc",
    ".python-version",
    "apps/console/console/package.json",
    "apps/console/console/package-lock.json",
    # §2.6 Release gates
    "scripts/release/build.py",
    "scripts/verify_doc_links.py",
    "scripts/verify_docs_index_coverage.py",
    "scripts/lint_audit_claims.py",
    "scripts/check_docs_commands.py",
    ".github/workflows/release.yml",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    ".coveragerc",
    # §2.7 Evidence ledgers / rollback machinery
    ".selfmod-state/selfmod.db",
    ".selfmod-state/backups/abc/backup.json",
    "upgrade-state/status.json",
    "upgrade-backups/abc/files/app.py",
    "data/audit/audit.json",
    "docs/README.md",
    "docs/MASTER-TASK-LEDGER.md",
    "docs/known-gaps.md",
    "docs/spec/SELF-EVOLUTION.md",
    "RELEASE-READINESS-CONTRACT.md",
    "RELEASE-RUNBOOK.md",
    # §2.8 Tests
    "tests/test_selfmod_copy_test.py",
    "tests/conftest.py",
    "conftest.py",
    # §2.9 The self-modification machinery itself
    "src/selfmod/service.py",
    "src/selfmod/paths.py",
    "src/upgrade/manager.py",
]


@pytest.mark.parametrize("probe", _SPEC_PROHIBITED)
def test_spec_prohibited_zone_is_out_of_reach(tmp_path: Path, probe: str) -> None:
    policy = PathPolicy(tmp_path)
    with pytest.raises(PathNotAllowed):
        policy.normalise(probe)


@pytest.mark.parametrize(
    "probe",
    [
        "src/kernels/audit/delivery.py",
        "src/common/atomic_write.py",
        "apps/console/console/src/App.tsx",
        "docs/guides/new-page.md",
        "README.md",
    ],
)
def test_ordinary_source_files_remain_editable(tmp_path: Path, probe: str) -> None:
    """Positive control: the blocklist must not swallow the whole workspace."""
    policy = PathPolicy(tmp_path)
    assert policy.normalise(probe) == probe
