"""U1 — host-command capability model (default-empty => deny-all).

A :class:`HostCommandCapability` describes a *grant*: a scoped permission to run a
class of host commands. The catalog starts **empty**, so with no grants the
policy engine denies everything. Flipping the global enablement gate ON does not
grant anything -- capabilities must be registered explicitly. This is the
default-DENY guarantee at the capability layer.

Structured matching (anti-induction §1)
----------------------------------------
Grants are matched **structurally** -- program + subcommand + per-position
argument shapes -- never by whole-string glob. The old ``command_glob``
(``fnmatch("git *")``) let one entry bless ``git`` followed by *any*
arguments, including dangerous ones, so it is retired: registering a
capability that only sets ``command_glob`` raises :class:`ValueError`
unless the catalog was explicitly built with ``allow_legacy_glob=True``
(migration escape hatch; the broker never enables it).

Matching rules (all must hold):

* ``adapter`` / ``require_shell`` filters as before;
* ``use_shell=True`` requires the entry to set ``allow_shell=True``;
* argv[0] must equal ``program`` exactly (case-insensitive, ``.exe`` /
  ``.com`` / ``.bat`` / ``.cmd`` suffix ignored);
* if ``subcommands`` is set, argv[1] must be one of them;
* trailing arguments must match ``arg_patterns`` **position by position**
  (one token per pattern; ``*`` matches a single token). ``arg_patterns``
  unset means *no* trailing arguments are allowed -- there is no
  "and then anything" shape;
* no argv token may appear in ``forbidden_args`` (e.g. ``/s``, ``-rf``);
* if ``allowed_cwds`` is set, the request cwd must sit under one of them.
"""

from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass
from typing import Dict, List, Optional

from .models import HostCommandRequest, SandboxSpec

#: Program-name suffixes ignored when comparing argv[0] to ``program``.
_PROGRAM_SUFFIXES = (".exe", ".com", ".bat", ".cmd")


def _normalize_program(name: str) -> str:
    text = (name or "").strip().lower()
    for suffix in _PROGRAM_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text


def _tokenize(command: str) -> Optional[List[str]]:
    """Split a command into argv tokens; ``None`` when it cannot be split."""
    try:
        return shlex.split(command or "", posix=False)
    except ValueError:
        return None


def _normalize_cwd(path: str) -> str:
    return (path or "").replace("/", "\\").rstrip("\\").lower()


@dataclass(frozen=True)
class HostCommandCapability:
    """A scoped grant to run a class of host commands."""

    capability_id: str
    description: str = ""
    # Structured matching (all set fields must match):
    program: Optional[str] = None       # exact program name, e.g. "git"
    subcommands: Optional[List[str]] = None  # allowed argv[1] values; None = any
    arg_patterns: Optional[List[str]] = None  # per-position patterns for the
        # remaining tokens; None = no trailing arguments allowed
    forbidden_args: Optional[List[str]] = None  # tokens that must never appear
    allow_shell: bool = False           # may this grant cover use_shell=True?
    allowed_cwds: Optional[List[str]] = None  # cwd must sit under one of these
    max_seconds: Optional[float] = None  # recorded intent (executor may enforce)
    # Matching rules (all must match for the capability to apply):
    # DEPRECATED: whole-string glob (e.g. "git *"). Retired by anti-induction
    # §1 -- registering a capability that sets it raises ValueError unless
    # the catalog opted into allow_legacy_glob. Kept only so old call sites
    # fail loudly at registration instead of silently matching nothing.
    command_glob: Optional[str] = None  # e.g. "git *", "ls *"
    adapter: Optional[str] = None       # restrict to an adapter ("shell", ...)
    require_shell: Optional[bool] = None  # True/False/None(any)
    sandbox: Optional[SandboxSpec] = None
    # If True, even a match must be escalated to a human for approval (sovereignty).
    requires_approval: bool = False


class CapabilityCatalog:
    """Registry of granted capabilities. Empty by default => deny-all."""

    def __init__(self, *, allow_legacy_glob: bool = False) -> None:
        self._caps: Dict[str, HostCommandCapability] = {}
        self._allow_legacy_glob = allow_legacy_glob
        #: True when this catalog mirrors the persisted allowlist file
        #: (``data/host_command_allowlist.json``). The broker auto-reloads
        #: such a catalog when the file changes, so an Owner edit takes
        #: effect on the next command without a restart.
        self.from_file: bool = False

    @classmethod
    def load_persisted(cls) -> "CapabilityCatalog":
        """从落盘白名单建能力目录（文件不存在/损坏 → 空目录，默认 DENY）。"""
        from .allowlist import load_capabilities

        catalog = cls()
        catalog.from_file = True
        for cap in load_capabilities():
            catalog.register(cap)
        return catalog

    def register(self, cap: HostCommandCapability) -> None:
        if cap.command_glob is not None and not self._allow_legacy_glob:
            raise ValueError(
                "command_glob is retired (anti-induction §1): whole-string "
                f"fnmatch grants like {cap.command_glob!r} can bless dangerous "
                "arguments. Rewrite the grant structurally (program + "
                "subcommands + arg_patterns)."
            )
        self._caps[cap.capability_id] = cap

    def revoke(self, capability_id: str) -> bool:
        return self._caps.pop(capability_id, None) is not None

    def all(self) -> List[HostCommandCapability]:
        return list(self._caps.values())

    def match(self, req: HostCommandRequest) -> List[HostCommandCapability]:
        """Return capabilities whose rules all match ``req`` (order-independent)."""
        return [c for c in self._caps.values() if self._matches(c, req)]

    def _matches(self, cap: HostCommandCapability, req: HostCommandRequest) -> bool:
        if cap.adapter is not None and cap.adapter != req.adapter:
            return False
        if cap.require_shell is not None and cap.require_shell != req.use_shell:
            return False
        if req.use_shell and not cap.allow_shell:
            return False
        # Legacy whole-string glob: only when the catalog explicitly opted in.
        if cap.command_glob is not None:
            if not self._allow_legacy_glob:
                return False
            if not fnmatch.fnmatch(req.command, cap.command_glob):
                return False
            return True
        # Structured matching: a program is mandatory; without one the grant
        # matches nothing (fail closed).
        if cap.program is None:
            return False
        argv = _tokenize(req.command)
        if not argv:
            return False
        if _normalize_program(argv[0]) != _normalize_program(cap.program):
            return False
        rest = argv[1:]
        if cap.subcommands is not None:
            if not rest:
                return False
            if rest[0].lower() not in {s.lower() for s in cap.subcommands}:
                return False
            rest = rest[1:]
        if cap.arg_patterns is None:
            if rest:
                return False
        else:
            if len(rest) != len(cap.arg_patterns):
                return False
            for token, pattern in zip(rest, cap.arg_patterns):
                if not fnmatch.fnmatch(token.lower(), pattern.lower()):
                    return False
        if cap.forbidden_args:
            banned = {a.lower() for a in cap.forbidden_args}
            if any(tok.lower() in banned for tok in argv):
                return False
        if cap.allowed_cwds:
            cwd = _normalize_cwd(req.cwd or "")
            roots = [_normalize_cwd(r) for r in cap.allowed_cwds]
            if not any(cwd == root or cwd.startswith(root + "\\") for root in roots):
                return False
        return True
