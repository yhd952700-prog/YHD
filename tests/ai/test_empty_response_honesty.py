"""Empty LLM responses must FAIL honestly, never fake success.

The defect
----------
Several providers normalise a missing completion to ``""`` -- e.g.
``response.choices[0].message.content or ""`` in ``src/ai/providers.py`` -- since
the SDK type allows ``None``. That ``""`` used to be returned as a normal result
from ``generate_with_retry``, and ``Agent.execute`` then:

* incremented ``completed_tasks``,
* set ``AgentStatus.COMPLETED``,
* emitted a ``completed`` observability metric,
* returned ``{"status": "completed", "result": ""}``.

Every observable signal claimed the task succeeded while the model had in fact
produced nothing. A caller trusting that status stores an empty string as if it
were the model's answer. This is a silent fake success: no error, no exception,
no log -- just a confidently wrong record.

The fix
-------
Emptiness is checked at the PROVIDER BOUNDARY (``generate_with_retry``), the one
choke point every consumer already routes through. So the fix covers the existing
call sites (``employee.execute``, ``goal_task_graph``) and any future one, rather
than relying on each call site remembering to check.

Design constraints (do not weaken these to make tests pass)
----------------------------------------------------------
* A genuinely empty completion is a FAILURE and must be retryable -- a transient
  upstream hiccup should not be reported as a completed task on the first try.
* A real response must pass through untouched. This check must not become a new
  way to reject valid output (the "fix" that breaks the product is not a fix).
* The raised error must be ACTIONABLE and must not be re-wrapped by
  ``classify_error`` into a generic ProviderError, which would triple the message
  and bury the cause.
* ``None``, ``""`` and whitespace-only are all "no model output". Checking only
  falsiness would still admit ``"   \\n"`` as a successful answer.
"""


from __future__ import annotations

import pytest

from error_types import ProviderEmptyResponseError
from src.ai.employee import Agent
from src.ai.providers import BaseProvider


class _StubProvider(BaseProvider):
    """Provider whose ``generate`` returns a fixed value."""

    def __init__(self, value, name="stub"):
        super().__init__(name, "stub-model")
        self.max_retries = 2
        self.retry_base_delay = 0.0
        self._value = value
        self.calls = 0

    def generate(self, prompt, **kwargs):
        self.calls += 1
        return self._value


def _agent(provider) -> Agent:
    return Agent(
        id="t-empty", name="t", agent_type="analyst", provider=provider
    )


# --------------------------------------------------------------------------- #
# 1. Negative controls: no model output is a failure, not a success
# --------------------------------------------------------------------------- #
class TestEmptyResponseIsAFailure:
    @pytest.mark.parametrize(
        "value,label",
        [
            ("", "empty string"),
            (None, "None"),
            ("   \n\t  ", "whitespace only"),
        ],
        ids=["empty", "none", "whitespace"],
    )
    def test_provider_boundary_raises(self, value, label):
        p = _StubProvider(value)
        with pytest.raises(ProviderEmptyResponseError) as exc:
            p.generate_with_retry("hello")
        assert exc.value.error_code == "EMPTY_RESPONSE"
        assert p.name in str(exc.value), "the error must name the provider"

    def test_failure_message_is_single_wrapped_and_actionable(self):
        """Guards against classify_error() re-wrapping the empty-response error
        into a generic ProviderError (which triples the message)."""
        p = _StubProvider("")
        with pytest.raises(ProviderEmptyResponseError) as exc:
            p.generate_with_retry("hello")
        msg = str(exc.value)
        assert msg.count("returned an empty response") == 1, (
            f"error message is re-wrapped and unreadable: {msg}"
        )
        assert "No model output" in msg, "must state the consequence plainly"

    def test_empty_response_is_retried_before_failing(self):
        """A transient empty completion should be retried, not failed at once."""
        p = _StubProvider("")
        with pytest.raises(ProviderEmptyResponseError):
            p.generate_with_retry("hello")
        assert p.calls == p.max_retries + 1, (
            "an empty completion must consume its retries -- a transient "
            "upstream hiccup should not be reported as a failed task on try #1"
        )


# --------------------------------------------------------------------------- #
# 2. Positive control: real output must be untouched
# --------------------------------------------------------------------------- #
class TestRealOutputIsUnaffected:
    @pytest.mark.parametrize(
        "value",
        ["Paris.", "0", "0.0", "false", "null", "[]", "{}"],
        ids=["text", "zero-str", "zero-float", "false", "null", "empty-list",
             "empty-dict"],
    )
    def test_falsy_but_meaningful_values_are_not_treated_as_empty(self, value):
        """Guard against a truthiness check that would reject legitimate output.

        ``"0"`` / ``"false"`` / ``"[]"`` are all valid model answers that a naive
        ``if not response`` would discard -- turning the fix into a new outage.
        """
        p = _StubProvider(value)
        assert p.generate_with_retry("hello") == value

    def test_real_response_is_returned_without_retry(self):
        p = _StubProvider("Paris.")
        assert p.generate_with_retry("hello") == "Paris."
        assert p.calls == 1, "a good response must not burn retries"


# --------------------------------------------------------------------------- #
# 3. End-to-end: the consumer must stop reporting fake success
# --------------------------------------------------------------------------- #
class TestAgentReportsFailureHonestly:
    def test_empty_response_yields_failed_not_completed(self):
        agent = _agent(_StubProvider(""))
        result = agent.execute("What is the capital of France?")

        assert result["status"] == "failed", (
            "an empty model response was still reported as a completed task"
        )
        assert agent.completed_tasks == 0, "completed_tasks was incremented on empty output"
        assert agent.failed_tasks == 1
        assert result.get("error"), "a failure must carry a readable reason"

    def test_real_response_still_completes(self):
        agent = _agent(_StubProvider("Paris."))
        result = agent.execute("What is the capital of France?")

        assert result["status"] == "completed"
        assert result["result"] == "Paris."
        assert agent.completed_tasks == 1
        assert agent.failed_tasks == 0
        assert result.get("error") is None

    def test_agent_status_is_failed_not_completed(self):
        """The agent's own state must not claim COMPLETED after an empty answer."""
        from src.ai.employee import AgentStatus

        agent = _agent(_StubProvider(None))
        agent.execute("q")
        assert agent.status is AgentStatus.FAILED
        assert agent.current_task is None
