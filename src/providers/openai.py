"""OpenAIProvider - OpenAI-compatible provider adapter contract.

#345 -- honest failure instead of fake success
----------------------------------------------
This adapter has **no real OpenAI client**: it never opens a socket. It used to
(a) return fabricated ``chat`` / ``generate`` / ``embeddings`` payloads carrying
real model names (``gpt-4o-mini`` / ``text-embedding-3-small``) and (b) report
``health_check() == {"status": "healthy", "provider": "openai"}``
unconditionally. A caller could not tell "the model answered" from "a hard-coded
echo", and a monitor wired to the health probe could never see a fault.

Now:

* the payload methods **fail closed** -- they raise
  :class:`~src.providers.llm_base.ProviderNotConfiguredError` unless the operator
  explicitly opts into simulation with the repo-wide
  ``LIUHAO_ALLOW_SIMULATED_EXECUTION`` contract (the same switch read by
  ``src/kernels/execution/__init__.py``, ``src/ai/providers.py`` and
  ``src/gateway/dashboard.py`` -- no second mechanism is invented here);
* when opted in, the returned payload is labelled ``simulated=True`` and no
  longer impersonates a real model name;
* ``health_check()`` never reports ``healthy`` while this adapter has no real
  backend, and returns a machine-readable ``reason`` plus an actionable
  ``action``.

For real OpenAI calls use :class:`src.ai.providers.OpenAIProvider`
(``AI_PROVIDER_TYPE=openai``).
"""

from __future__ import annotations

import os
from typing import Any

from src.providers.llm_base import LLMProvider, ProviderNotConfiguredError

#: Repo-wide opt-in for simulated execution. Read live (never cached) so a test
#: or demo can flip it for a single call -- the same semantics as the kernel's
#: ``simulated_execution_enabled()``. Deliberately NOT imported from the kernel:
#: a provider adapter must not depend on an upper execution layer.
_SIMULATION_OPT_IN_ENV = "LIUHAO_ALLOW_SIMULATED_EXECUTION"
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _simulation_opted_in() -> bool:
    """Whether the operator explicitly opted into simulated results."""
    value = (os.environ.get(_SIMULATION_OPT_IN_ENV) or "").strip().lower()
    return value in _TRUTHY_ENV_VALUES


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider adapter contract (no real client)."""

    @property
    def name(self) -> str:
        return "openai"

    @property
    def type(self) -> str:
        return "openai"

    def _not_configured(self, operation: str) -> ProviderNotConfiguredError:
        """Build the fail-closed error, with an actionable next step."""
        return ProviderNotConfiguredError(
            f"cannot perform {operation!r}: this adapter has no real OpenAI "
            "client and is not simulating. Use src.ai.providers.OpenAIProvider "
            "for real calls (AI_PROVIDER_TYPE=openai), or set "
            f"{_SIMULATION_OPT_IN_ENV}=1 to opt into labelled simulation.",
            provider="openai",
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 1.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Chat completion -- labelled simulated echo, or fail closed.

        There is no model behind this method: when simulation is not opted in it
        raises instead of returning a fabricated completion.
        """
        if not _simulation_opted_in():
            raise self._not_configured("chat")
        content = messages[-1].get("content", "") if messages else ""
        return {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "model": "openai-stub",
            "provider": "openai",
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
            "model": "openai-stub",
            "provider": "openai",
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
            "model": "openai-stub",
            "provider": "openai",
            "simulated": True,
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    def supports(self, capability: str) -> bool:
        """Check capability support."""
        capabilities = {"chat", "generate", "embeddings", "streaming"}
        return capability in capabilities

    def health_check(self) -> dict[str, Any]:
        """Report that this adapter cannot serve real requests.

        It has no real OpenAI backend, so it must never claim to be healthy: a
        probe that structurally cannot report a fault is a decoration, not a
        health check. ``status`` is ``"simulated"`` when the operator has opted
        into simulation, and ``"unavailable"`` otherwise -- never ``"healthy"``.
        """
        simulated = _simulation_opted_in()
        return {
            "status": "simulated" if simulated else "unavailable",
            "provider": "openai",
            "reason": "not_configured",
            "simulated": simulated,
            "detail": (
                "OpenAIProvider is a contract stub with no real OpenAI client; it "
                "cannot serve chat/generate/embeddings."
            ),
            "action": (
                "For real calls use src.ai.providers.OpenAIProvider "
                "(AI_PROVIDER_TYPE=openai); set "
                f"{_SIMULATION_OPT_IN_ENV}=1 only for demo/tests, where payloads "
                "are labelled simulated=True."
            ),
        }
