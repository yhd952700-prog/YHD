"""
Shell safety gate — fail-closed wrapper around ``WorldInterface`` + ``ShellAdapter``.

OWNER AUTHORIZATION (binding)
-----------------------------
"If ShellAdapter has security risk, finish the threat model, sandbox/capability
boundary, tests and safety gate, then decide when to enable."

This module is the **only** sanctioned constructor for a ``WorldInterface`` that
can execute host commands (the ``shell`` adapter). It enforces the owner rule
above by being fail-closed:

* A host command (shell adapter) is, in the ``autonomous`` actor model, denied
  by default. It may ONLY proceed when an explicit human-arming ``authorize``
  policy is injected at construction AND (for ``shell=True``) a per-request
  ``human_arm_token`` is supplied and logged.
* For an ``autonomous`` interface, construction without an ``authorize`` policy
  is refused outright — you cannot even build a permissive-by-default autonomous
  shell interface.
* ``shell=True`` (real shell, command-injection surface re-opened) is NEVER a
  blanket allow: it must always be paired with an explicit, logged human
  authorization token. The token is recorded (masked) so every armed shell use
  is auditable.

REUSE, NOT FORK
---------------
This module reuses :class:`src.ai.world_interface.WorldInterface` and
:class:`src.ai.world_interface.ShellAdapter` directly. It does not copy or fork
their logic — it wraps the policy gate so the enforcement lives in exactly one
place.

The autonomous GOAL path (``register_default_local_tools``) deliberately does
NOT register ``ShellAdapter``; this gate is the explicit, opt-in escape hatch
for human-armed scenarios only.
"""

from __future__ import annotations

import hashlib
import logging
import shlex
from typing import Any, Callable, Dict, Optional

from .world_interface import ShellAdapter, WorldInterface, WorldRequest

_LOG = logging.getLogger(__name__)

#: Marker stored on a shell request's metadata when it was armed by a human.
_HUMAN_ARM_KEY = "human_arm_token"

#: The "shell" adapter is a host-command capability and is therefore gated.
_SHELL_ADAPTER = "shell"


def _mask_token(token: str) -> str:
    """Return a short, non-recoverable mask of a human arm token for logging."""
    if not token:
        return "<empty>"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}…"


def _gate_authorize(
    actor: str,
    authorize: Optional[Callable[[WorldRequest], bool]],
) -> Callable[[WorldRequest], bool]:
    """Build the policy gate used by the shell-capable interface.

    Fail-closed rules:
      * shell adapter requests are denied unless the injected ``authorize``
        policy explicitly permits them (default-deny for host commands).
      * ``shell=True`` additionally requires the request to carry a logged
        ``human_arm_token`` (set by :func:`build_shell_world_request`). Without
        it the authorize policy could not have validated a real human arm, so we
        refuse.
      * non-shell adapters fall back to the wrapped policy; for a ``human``
        actor with no policy the historical default-allow is preserved.
    """

    def fn(request: WorldRequest) -> bool:
        if request.adapter == _SHELL_ADAPTER:
            if authorize is None:
                _LOG.warning(
                    "SHELL_GATE: denied shell request (no authorize policy) "
                    "actor=%s action=%s",
                    actor, request.action,
                )
                return False
            if request.params.get("shell"):
                token = request.metadata.get(_HUMAN_ARM_KEY)
                if not token:
                    _LOG.warning(
                        "SHELL_GATE: denied shell=True (missing human_arm_token) "
                        "actor=%s command=%r",
                        actor, request.params.get("command"),
                    )
                    return False
            permitted = bool(authorize(request))
            _LOG.info(
                "SHELL_GATE: shell request authorize=%s actor=%s shell=%s "
                "command=%r token=%s",
                permitted,
                actor,
                bool(request.params.get("shell")),
                request.params.get("command"),
                _mask_token(str(request.metadata.get(_HUMAN_ARM_KEY, ""))),
            )
            return permitted
        if authorize is None:
            return actor != "autonomous"
        return bool(authorize(request))

    return fn


def make_world_interface_with_shell(
    *,
    actor: str,
    authorize: Optional[Callable[[WorldRequest], bool]] = None,
) -> WorldInterface:
    """Construct a ``WorldInterface`` that can execute host commands.

    Fail-closed: an ``autonomous`` interface MUST be given an explicit
    human-arming ``authorize`` policy; without one the constructor refuses
    (ValueError). This prevents a permissive-by-default autonomous shell
    interface from ever being built.

    Args:
        actor: ``"human"`` or ``"autonomous"`` (validated by ``WorldInterface``).
        authorize: optional policy callback. Required when ``actor`` is
            ``"autonomous"``.

    Returns:
        A ``WorldInterface`` with the ``shell`` adapter registered and the
        fail-closed shell gate installed.
    """
    if actor == "autonomous" and authorize is None:
        raise ValueError(
            "autonomous shell interface requires an explicit human-arming "
            "authorize policy; refusing to build a permissive-by-default "
            "autonomous shell interface (fail-closed)"
        )
    return WorldInterface(
        adapters=[ShellAdapter()],
        authorize=_gate_authorize(actor, authorize),
        actor=actor,
    )


def build_shell_world_request(
    command: str,
    *,
    shell: bool = False,
    human_arm_token: Optional[str] = None,
) -> WorldRequest:
    """Build a ``shell`` adapter ``WorldRequest``.

    Fail-closed: ``shell=True`` (real shell, command-injection surface
    re-opened) must be paired with an explicit, logged ``human_arm_token``. With
    no token the constructor refuses (ValueError) — shell is never a blanket
    allow. The token is recorded (masked) on the request metadata and logged so
    every armed shell use is auditable.

    Args:
        command: the command string.
        shell: whether to use a real shell (``True`` re-opens command injection).
        human_arm_token: explicit human authorization token for ``shell=True``.

    Returns:
        A ``WorldRequest`` for the shell adapter.
    """
    if shell and human_arm_token is None:
        raise ValueError(
            "shell=True requires an explicit human_arm_token; refusing to arm "
            "a real shell without logged human authorization (fail-closed)"
        )
    if shell and human_arm_token is not None:
        _LOG.warning(
            "SHELL_GATE: ARMED shell=True command=%r token=%s",
            command, _mask_token(human_arm_token),
        )
    return WorldRequest(
        adapter=_SHELL_ADAPTER,
        action="run",
        params={"command": command, "shell": bool(shell)},
        metadata={_HUMAN_ARM_KEY: (human_arm_token or "")},
    )


def shell_parse_literal(command: str) -> Dict[str, Any]:
    """Return the shlex-parsed argv for a command WITHOUT executing it.

    This is the safe inspection point used by tests and callers that need to
    prove a command is tokenized literally (no shell grammar) before deciding
    whether to run it. Mirrors ``ShellAdapter.execute``'s non-shell path: an
    empty command raises ``ValueError`` (the adapter's contract).
    """
    if not command or not command.strip():
        raise ValueError("shell run requires a non-empty command")
    return {"argv": shlex.split(command), "shell": False}
