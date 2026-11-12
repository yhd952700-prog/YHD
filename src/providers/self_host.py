"""SelfHostProvider - Self-hosted provider adapter contract.

#345 (follow-up) -- honest failure instead of fake success
---------------------------------------------------------
This adapter is the *twin* of :mod:`src.providers.openai`: it has **no real
self-hosted backend** and never opens a socket. It used to

* return fabricated ``chat`` / ``generate`` / ``embeddings`` payloads carrying
  real-looking model names (``self-hosted-model`` / ``self-hosted-embedding``),
  so a caller could not tell "the backend answered" from "a hard-coded echo";
* for ``chat`` it echoed the caller's own prompt back as the assistant's
  content -- an echo dressed up as an answer;
* report ``health_check() == {"status": "healthy", "provider": "self_host"}``
  unconditionally -- a probe that structurally cannot report a fault.

``c3643614`` fixed the ``openai`` twin (and ``src/providers/llm_base`` documents
``openai`` *and* ``self_host`` as the two contract stubs), but left this one
unfixed. Reachable via :func:`src.providers.registry.get_provider` and the
knowledge embedding pipeline, it silently poisoned downstream retrieval with
all-zero vectors while ``src/knowledge/embedding.py`` claimed it failed closed.

Now (identical contract to the ``openai`` adapter):

* the payload methods **fail closed** -- they raise
  :class:`~src.providers.llm_base.ProviderNotConfiguredError` unless the operator
  explicitly opts into simulation with the repo-wide
  ``LIUHAO_ALLOW_SIMULATED_EXECUTION`` contract (the same switch read by
  ``src/kernels/execution``, ``src/ai/providers.py``, ``src/gateway/dashboard.py``
  and the ``openai`` stub -- no second mechanism is invented here);
* when opted in, the returned payload is labelled ``simulated=True`` and no
  longer impersonates a real model name;
* ``health_check()`` never reports ``healthy`` while this adapter has no real
  backend, and returns a machine-readable ``reason`` plus an actionable
  ``action``.

For real self-hosted calls, point ``AI_PROVIDER_TYPE`` at a concrete provider
(e.g. ``ollama``) -- see :class:`src.ai.providers.OllamaProvider`.
"""

from __future__ import annotations

import os
from typing import Any

from src.providers.llm_base import LLMProvider, ProviderNotConfiguredError

#: Repo-wide opt-in for simulated execution. Read live (never cached) so a test
#: or demo can flip it for a single call -- the same semantics used by the
#: ``openai`` stub and the kernel's ``simulated_execution_enabled()``. Kept as a
#: literal (not imported) so this low-level adapter does not depend upward.
_SIMULATION_OPT_IN_ENV = "LIUHAO_ALLOW_SIMULATED_EXECUTION"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _simulation_opted_in() -> bool:
    """Whether the operator explicitly opted into simulated results."""
    value = (os.environ.get(_SIMULATION_OPT_IN_ENV) or "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


class SelfHostProvider(LLMProvider):
    """Self-hosted provider adapter contract (no real backend)."""

    @property
    def name(self) -> str:
        return "self_host"

    @property
    def type(self) -> str:
        return "self_host"

    def _not_configured(self, operation: str) -> ProviderNotConfiguredError:
        """Build the fail-closed error, with an actionable next step."""
        return ProviderNotConfiguredError(
            f"cannot perform {operation!r}: this adapter has no real self-hosted "
            "backend and is not simulating. Configure a concrete self-hosted "
            "provider (e.g. AI_PROVIDER_TYPE=ollama), or set "
            f"{_SIMULATION_OPT_IN_ENV}=1 to opt into labelled simulation.",
            provider="self_host",
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Chat completion -- labelled simulated echo, or fail closed.

        There is no backend behind this method: when simulation is not opted in
        it raises instead of echoing the caller's prompt back as an "answer".
        """
        if not _simulation_opted_in():
            raise self._not_configured("chat")
        content = messages[-1].get("content", "") if messages else ""
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "model": "self-host-stub",
            "provider": "self_host",
            "simulated": True,
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": len(content.split()),
                "total_tokens": len(content.split()),
            },
        }

    def generate(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        """Single-prompt generation -- labelled simulated echo, or fail closed."""
        if not _simulation_opted_in():
            raise self._not_configured("generate")
        return {
            "choices": [{"message": {"role": "assistant", "content": prompt}}],
            "model": "self-host-stub",
            "provider": "self_host",
            "simulated": True,
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": 0,
                "total_tokens": len(prompt.split()),
            },
        }

    def embeddings(self, texts: list[str], **kwargs: Any) -> dict[str, Any]:
        """Embeddings -- labelled simulated zero vectors, or fail closed."""
        if not _simulation_opted_in():
            raise self._not_configured("embeddings")
        dim = kwargs.get("dimension", 1536)
        return {
            "data": [{"embedding": [0.0] * dim, "index": 0, "object": "embedding"}],
            "model": "self-host-stub",
            "provider": "self_host",
            "simulated": True,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    def supports(self, capability: str) -> bool:
        """Check capability support."""
        capabilities = {"chat", "generate", "embeddings", "streaming"}
        return capability in capabilities

    def health_check(self) -> dict[str, Any]:
        """Report that this adapter cannot serve real requests.

        It has no real self-hosted backend, so it must never claim to be
        healthy: a probe that structurally cannot report a fault is a
        decoration, not a health check. ``status`` is ``"simulated"`` when the
        operator has opted into simulation, and ``"unavailable"`` otherwise --
        never ``"healthy"``.
        """
        simulated = _simulation_opted_in()
        return {
            "status": "simulated" if simulated else "unavailable",
            "provider": "self_host",
            "reason": "not_configured",
            "simulated": simulated,
            "detail": (
                "SelfHostProvider is a contract stub with no real self-hosted "
                "backend; it cannot serve chat/generate/embeddings."
            ),
            "action": (
                "Configure a concrete self-hosted provider "
                "(e.g. AI_PROVIDER_TYPE=ollama); set "
                f"{_SIMULATION_OPT_IN_ENV}=1 only for demo/tests, where payloads "
                "are labelled simulated=True."
            ),
        }
