"""``scripts/register_human_identity.py`` 的凭据支持与脱敏。

最要紧的一条断言是 :func:`test_no_command_ever_prints_the_plaintext`：注册
脚本会打印大量帮助信息，一旦某处把凭据对象整个 dump 出来，密码就进了终端
历史和 CI 日志。这条测试用真实凭据跑完全部命令，逐条检查输出里没有明文。
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "register_human_identity.py"

PASSWORD = "S3cret-Passw0rd!"
PRINCIPAL = "xin.hongda"


def _load_script():
    """Import the CLI module from its path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("register_human_identity", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["register_human_identity"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load_script()


@pytest.fixture
def workspace(cli, monkeypatch, tmp_path):
    """Isolated stores, and a CWD that swallows runtime files (audit store)."""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(tmp_path / "secrets.json"))
    monkeypatch.delenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", raising=False)
    monkeypatch.delenv("LIUHAO_HUMAN_IDENTITIES_DB", raising=False)
    monkeypatch.delenv(cli.PASSWORD_ENV, raising=False)
    # The login proof writes an audit event; keep that file out of the repo.
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _register(cli, password=PASSWORD):
    return cli.main(
        [
            "--principal", PRINCIPAL,
            "--display-name", "辛宏达",
            "--password", password,
        ]
    )


# ---------------------------------------------------------------------------
# password sources
# ---------------------------------------------------------------------------


class TestPasswordSources:
    def test_flag(self, cli, workspace):
        args = cli.argparse.Namespace(password="from-flag", password_stdin=False)
        assert cli.resolve_password(args) == "from-flag"

    def test_stdin(self, cli, workspace, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("from-stdin\n"))
        args = cli.argparse.Namespace(password=None, password_stdin=True)
        # The trailing newline is not part of the secret.
        assert cli.resolve_password(args) == "from-stdin"

    def test_environment(self, cli, workspace, monkeypatch):
        monkeypatch.setenv(cli.PASSWORD_ENV, "from-env")
        args = cli.argparse.Namespace(password=None, password_stdin=False)
        assert cli.resolve_password(args) == "from-env"

    def test_no_source_means_no_password(self, cli, workspace):
        args = cli.argparse.Namespace(password=None, password_stdin=False)
        assert cli.resolve_password(args) is None

    def test_empty_credential_is_refused(self, cli, workspace, capsys):
        assert cli.main(["--principal", PRINCIPAL, "--password", ""]) == 2
        assert "empty credential" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_registers_and_proves_the_login(self, cli, workspace, capsys):
        assert _register(cli) == 0
        out = capsys.readouterr().out
        assert "an actual sign-in with this credential succeeded" in out

    def test_writes_a_hashed_credential_not_the_secret(self, cli, workspace):
        _register(cli)
        document = json.loads((workspace / "secrets.json").read_text(encoding="utf-8"))
        record = document["credentials"][PRINCIPAL]
        assert sorted(record) == ["algo", "hash", "iterations", "salt"]
        assert PASSWORD not in json.dumps(document)
        assert record["algo"] == "pbkdf2_hmac_sha256"
        assert record["iterations"] >= 100_000

    def test_identity_file_marks_the_human(self, cli, workspace):
        _register(cli)
        document = json.loads((workspace / "humans.json").read_text(encoding="utf-8"))
        entry = next(h for h in document["humans"] if h["principal"] == PRINCIPAL)
        assert entry["display_name"] == "辛宏达"
        assert entry["scope"] == "L0"

    def test_registering_without_a_password_is_still_allowed_but_warns(
        self, cli, workspace, capsys
    ):
        assert cli.main(["--principal", PRINCIPAL]) == 0
        out = capsys.readouterr().out
        assert "cannot sign in" in out
        assert "verified  : a fresh kernel loads this principal as a human." in out

    def test_machine_principals_are_refused(self, cli, workspace, capsys):
        for machine in cli.MACHINE_PRINCIPALS:
            assert cli.main(["--principal", machine, "--password", PASSWORD]) == 2
        assert "machine" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# list / clear / revoke
# ---------------------------------------------------------------------------


class TestListAndRevoke:
    def test_empty_store_reports_success_is_impossible(self, cli, workspace, capsys):
        assert cli.main(["--list"]) == 1
        assert "Nobody can approve" in capsys.readouterr().out

    def test_list_shows_credential_status_without_leaking(self, cli, workspace, capsys):
        _register(cli)
        capsys.readouterr()  # drop the register output
        assert cli.main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "login credential set (algo=pbkdf2_hmac_sha256" in out
        assert "Can sign in: 1/1" in out
        assert PASSWORD not in out

    def test_list_reports_a_principal_that_cannot_sign_in(self, cli, workspace, capsys):
        cli.main(["--principal", PRINCIPAL])
        capsys.readouterr()
        cli.main(["--list"])
        out = capsys.readouterr().out
        assert "no login credential -- cannot sign in" in out
        assert "Can sign in: 0/1" in out

    def test_clear_credential_keeps_the_identity(self, cli, workspace, capsys):
        _register(cli)
        capsys.readouterr()
        assert cli.main(["--clear-credential", PRINCIPAL]) == 0
        capsys.readouterr()

        secrets = json.loads((workspace / "secrets.json").read_text(encoding="utf-8"))
        assert secrets["credentials"] == {}
        humans = json.loads((workspace / "humans.json").read_text(encoding="utf-8"))
        assert [h["principal"] for h in humans["humans"]] == [PRINCIPAL]

    def test_revoke_removes_both_halves(self, cli, workspace, capsys):
        _register(cli)
        capsys.readouterr()
        assert cli.main(["--revoke", PRINCIPAL]) == 0
        out = capsys.readouterr().out
        assert "(removed)" in out

        humans = json.loads((workspace / "humans.json").read_text(encoding="utf-8"))
        secrets = json.loads((workspace / "secrets.json").read_text(encoding="utf-8"))
        assert humans["humans"] == []
        # Leaving a stale secret behind would re-arm the old password if the
        # identity were ever re-registered.
        assert secrets["credentials"] == {}

    def test_revoking_something_unknown_is_non_zero(self, cli, workspace, capsys):
        assert cli.main(["--revoke", "ghost"]) == 1
        assert "nothing to revoke" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_credential_summary_never_leaks_material(self, cli, workspace):
        _register(cli)
        store = cli.resolve_secret_store()
        summary = cli.credential_summary(store, PRINCIPAL)
        assert "pbkdf2_hmac_sha256" in summary
        assert PASSWORD not in summary
        record = store.credential_for(PRINCIPAL)
        assert record["salt"] not in summary
        assert record["hash"] not in summary

    def test_credential_summary_on_a_missing_credential(self, cli, workspace):
        summary = cli.credential_summary(cli.resolve_secret_store(), "nobody")
        assert summary == "no login credential -- cannot sign in"

    def test_no_command_ever_prints_the_plaintext(self, cli, workspace, capsys):
        """Every command, run against a real credential, must stay silent."""
        outputs = []
        outputs.append((_register(cli), capsys.readouterr()))
        for argv in (
            ["--list"],
            ["--principal", PRINCIPAL, "--display-name", "别名"],
            ["--clear-credential", PRINCIPAL],
            ["--principal", PRINCIPAL, "--password", PASSWORD],
            ["--revoke", PRINCIPAL],
        ):
            outputs.append((cli.main(argv), capsys.readouterr()))

        for rc, captured in outputs:
            assert PASSWORD not in captured.out, f"plaintext leaked (rc={rc})"
            assert PASSWORD not in captured.err, f"plaintext leaked in stderr (rc={rc})"

    def test_help_does_not_contain_a_secret(self, cli, workspace, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--help"])
        out = capsys.readouterr().out
        assert "--password-stdin" in out
