"""U1 — host-command capability model tests (default-empty => deny-all)."""

from src.ai.host_command.capability import CapabilityCatalog, HostCommandCapability
from src.ai.host_command.models import HostCommandRequest, SandboxSpec


def _req(command="git status", use_shell=False, adapter="shell"):
    return HostCommandRequest(command=command, adapter=adapter, use_shell=use_shell)


def test_empty_catalog_matches_nothing():
    cat = CapabilityCatalog()
    assert cat.match(_req()) == []


def test_command_glob_match():
    cat = CapabilityCatalog()
    cat.register(HostCommandCapability("git", command_glob="git *"))
    matched = cat.match(_req("git pull"))
    assert len(matched) == 1
    assert matched[0].capability_id == "git"


def test_command_glob_no_match():
    cat = CapabilityCatalog()
    cat.register(HostCommandCapability("git", command_glob="git *"))
    assert cat.match(_req("rm -rf /")) == []


def test_adapter_filter():
    cat = CapabilityCatalog()
    cat.register(HostCommandCapability("shell-ls", command_glob="ls *", adapter="shell"))
    # A request arriving on a non-shell adapter must NOT match a shell-only grant.
    assert cat.match(_req("ls -la", adapter="python_compute")) == []
    # A request arriving on the shell adapter matches.
    req = _req("ls -la", adapter="shell")
    assert len(cat.match(req)) == 1


def test_use_shell_filter():
    cat = CapabilityCatalog()
    cat.register(HostCommandCapability("raw", command_glob="*", require_shell=True))
    assert cat.match(_req("anything", use_shell=False)) == []
    assert len(cat.match(_req("anything", use_shell=True))) == 1


def test_revoked_capability_no_longer_matches():
    cat = CapabilityCatalog()
    cap = HostCommandCapability("git", command_glob="git *")
    cat.register(cap)
    assert cat.match(_req("git x"))
    assert cat.revoke("git") is True
    assert cat.match(_req("git x")) == []


def test_sandbox_propagates_from_capability():
    sb = SandboxSpec(cwd="/workspace", network=False)
    cat = CapabilityCatalog()
    cat.register(HostCommandCapability("git", command_glob="git *", sandbox=sb))
    matched = cat.match(_req("git x"))
    assert matched[0].sandbox is sb


def test_default_empty_is_deny_all():
    # The catalog shipped with no grants must deny all requests.
    cat = CapabilityCatalog()
    assert cat.match(_req("git status")) == []
    assert cat.match(_req("ls")) == []
    assert cat.match(_req("rm -rf /", use_shell=True)) == []
