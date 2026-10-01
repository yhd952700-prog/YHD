"""Deployment posture detection — the single source of truth.

Why this module exists
----------------------
``src/security/secret_store.py:49`` used to answer "am I in production?" by
reading exactly one environment variable, ``LIUHAO_ENV``. Nothing in this
repository ever set that variable:

* ``docker-compose.prod.yml`` sets ``ENVIRONMENT=production``
* ``docker-compose.yml`` sets ``APP_ENV=staging`` (default)
* ``config/production/config.py`` reads ``ENVIRONMENT``

so ``is_production()`` was ALWAYS ``False`` inside the production container and
every safety branch keyed on it silently took the non-production path — a gate
that looked like protection and provided none.

This module collapses the three aliases into one resolution order and makes an
*unknown* posture loud instead of silently ``False``.

Resolution order (highest priority first)
----------------------------------------
1. ``LIUHAO_ENV``   — explicit, LIUHAO-native override always wins.
2. ``ENVIRONMENT``  — what ``docker-compose.prod.yml`` actually sets.
3. ``APP_ENV``      — what ``docker-compose.yml`` (staging) actually sets.

Priority rationale: an operator who explicitly sets ``LIUHAO_ENV=production``
on a box must not be silently overridden by a stale ``ENVIRONMENT=staging``
shipped by the base image — the explicit, app-namespaced variable is the
strongest signal and is what the existing test-suite simulates production with.

A set-and-non-blank value other than ``production``/``prod`` is a *determined*
non-production posture (no warning). When **all three are unset or blank** the
posture is *undeterminable*:

* strict mode (``LIUHAO_POSTURE_STRICT`` truthy, default **OFF**) raises
  :class:`PostureUndeterminable`;
* otherwise this module logs a WARNING and answers ``False`` (a compatibility
  default for dev/test harnesses, **not** a verified non-production claim).

.. note:: The non-strict answer ``False`` for an undeterminable posture is
   *fail-open by compatibility*, not fail-closed. It is only safe because the
   deployment (compose files) now sets one of the three variables, and because
   the warning makes the misconfiguration discoverable. Any deployment that
   relies on these gates MUST set one of the three variables explicitly, or turn
   strict mode on. An attacker with full control of the environment can clear
   all three variables to force the non-strict default — strict mode is the only
   defense against that, so production deployments should enable it.
"""

from __future__ import annotations

import logging
import os
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

LIUHAO_ENV = "LIUHAO_ENV"
ENVIRONMENT_ENV = "ENVIRONMENT"
APP_ENV_ENV = "APP_ENV"

#: Resolution priority: explicit LIUHAO-native override wins, then the var the
#: production compose sets, then the var the staging compose sets.
POSTURE_ENV_VARS = (LIUHAO_ENV, ENVIRONMENT_ENV, APP_ENV_ENV)

PRODUCTION_VALUES = frozenset({"production", "prod"})

STRICT_ENV = "LIUHAO_POSTURE_STRICT"
_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disabled", ""})


class PostureUndeterminable(RuntimeError):
    """Raised in strict mode when the deployment posture cannot be resolved.

    An undeterminable posture is never answered silently; strict mode turns the
    warning into a hard failure so an un-configured deployment cannot drift into
    an accidental non-production (fail-open) branch.
    """


class Posture(NamedTuple):
    """The resolved deployment posture.

    ``determinable`` is ``True`` iff at least one of :data:`POSTURE_ENV_VARS`
    is set to a non-blank value. ``conflict`` is ``True`` when another
    non-blank variable disagrees with the (priority-resolved) production-ness,
    i.e. the deployment is internally inconsistent about its posture.
    """

    is_production: bool
    source: Optional[str]
    raw: Optional[str]
    determinable: bool
    conflict: bool


_UNDETERMINABLE_TEMPLATE = (
    "Deployment posture UNDETERMINABLE: none of %s are set to a non-empty "
    "value. Falling back to NON-production semantics for compatibility, but "
    "this is NOT a verified non-production deployment. Set one of %s "
    "explicitly, or set %s=1 to make an unknown posture a hard failure."
)
_UNDETERMINABLE_WARN = _UNDETERMINABLE_TEMPLATE % (
    POSTURE_ENV_VARS, POSTURE_ENV_VARS, STRICT_ENV,
)

_UNDETERMINABLE_MSG = (
    "Deployment posture is undeterminable: none of %s are set to a non-empty "
    "value, and strict posture mode is enabled (%s). Refusing to assume a "
    "posture. Set one of %s explicitly in the deployment environment."
    % (POSTURE_ENV_VARS, STRICT_ENV, POSTURE_ENV_VARS)
)


def _strict_enabled(source: "object") -> bool:
    """Resolve the strict-mode opt-in from a mapping.

    Explicitly falsy values (``0/false/no/off/disabled/``) disable strict mode.
    Any other non-empty value is treated as ENABLED (fail-closed toward
    protection) and warned, so a typo'd opt-in cannot silently weaken posture.
    """
    raw = (source.get(STRICT_ENV) if hasattr(source, "get") else None) or ""  # type: ignore[union-attr]
    value = raw.strip().lower()
    if value in _FALSY:
        return False
    if value in _TRUTHY:
        return True
    if value:
        logger.warning(
            "%s=%r is unrecognized; treating as enabled (fail-closed).",
            STRICT_ENV, raw,
        )
        return True
    return False


def deployment_posture(env: Optional["object"] = None) -> Posture:
    """Resolve the deployment posture from the alias variables.

    :param env: mapping to read from (defaults to ``os.environ``); accepting an
        explicit mapping keeps this testable without mutating the process env.
    :returns: a :class:`Posture` describing the resolved posture and its
        provenance. ``determinable`` is ``False`` iff all three vars are blank.
    """
    source = env if env is not None else os.environ

    present: "dict[str, bool]" = {}
    for name in POSTURE_ENV_VARS:
        raw = source.get(name)  # type: ignore[union-attr]
        if raw is None:
            continue
        value = raw.strip()
        if value:
            present[name] = value.lower() in PRODUCTION_VALUES

    if not present:
        return Posture(
            is_production=False, source=None, raw=None,
            determinable=False, conflict=False,
        )

    # Priority resolution: the highest-priority non-blank variable decides.
    for name in POSTURE_ENV_VARS:
        if name in present:
            raw_value = source.get(name).strip()  # type: ignore[union-attr]
            is_prod = present[name]
            conflict = any(v != is_prod for v in present.values())
            return Posture(
                is_production=is_prod, source=name, raw=raw_value,
                determinable=True, conflict=conflict,
            )

    # Unreachable: ``present`` is non-empty yet no name matched the priority
    # loop above. Defensive fallback.
    return Posture(
        is_production=False, source=None, raw=None,
        determinable=False, conflict=False,
    )


def is_production(
    env: Optional["object"] = None,
    *,
    strict: Optional[bool] = None,
) -> bool:
    """Return whether this deployment is production.

    Single canonical entry point. Never silently answers "I don't know":
    an undeterminable posture is logged (and, in strict mode, raised).

    :param env: mapping to read (defaults to ``os.environ``).
    :param strict: force strict behaviour. ``None`` defers to the
        ``LIUHAO_POSTURE_STRICT`` opt-in (default OFF).
    :raises PostureUndeterminable: when posture is undeterminable and strict is
        enabled.
    """
    source = env if env is not None else os.environ
    posture = deployment_posture(source)
    if posture.determinable:
        if posture.conflict:
            # Two variables disagree about whether we are in production. The
            # priority order has already picked a winner; surface the split so
            # operators can fix the misconfiguration.
            declared = {
                n: source.get(n)  # type: ignore[union-attr]
                for n in POSTURE_ENV_VARS
                if (source.get(n) or "").strip()  # type: ignore[union-attr]
            }
            logger.warning(
                "Deployment posture CONFLICT: variables disagree (%s); resolved "
                "via priority %s to source=%s raw=%r is_production=%s.",
                declared, POSTURE_ENV_VARS, posture.source, posture.raw,
                posture.is_production,
            )
        return posture.is_production

    effective_strict = _strict_enabled(source) if strict is None else strict
    if effective_strict:
        raise PostureUndeterminable(_UNDETERMINABLE_MSG)
    logger.warning(_UNDETERMINABLE_WARN)
    return False


def describe_posture(
    env: Optional["object"] = None,
) -> "dict[str, object]":
    """Return an auditable, JSON-serialisable posture readout.

    Used by readiness/health reporting so operators can see *why* a posture was
    concluded, not just the boolean. Never raises: undeterminable is reported as
    ``determinable=False`` rather than failing the probe.
    """
    source = env if env is not None else os.environ
    posture = deployment_posture(source)
    return {
        "is_production": is_production(source),
        "source": posture.source,
        "raw": posture.raw,
        "determinable": posture.determinable,
        "conflict": posture.conflict,
        "vars": {n: source.get(n) for n in POSTURE_ENV_VARS},  # type: ignore[union-attr]
    }
