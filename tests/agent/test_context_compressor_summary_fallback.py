"""Regression tests for the summary-model-not-found fallback in
ContextCompressor._generate_summary.

The fallback retry used to call ``self._generate_summary(messages,
summary_budget)`` where ``messages`` was never defined — a NameError
raised from inside the exception handler, so a configured summary
model that returned 404/503 crashed compression entirely instead of
falling back to the main model. It would also have bound
``summary_budget`` to the ``focus_topic`` parameter positionally.

These tests pin the corrected contract: the 404/503 path retries
immediately with the MAIN model, reusing the same turns and focus
topic, and succeeds.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor


class _ModelNotFound(Exception):
    """Mimics an API error carrying a status code."""

    status_code = 404


def _response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def _make_compressor():
    c = object.__new__(ContextCompressor)
    c.model = "main-model"
    c.provider = "openrouter"
    c.base_url = "https://api.example/v1"
    c.api_key = "sk-test"
    c.api_mode = "openai"
    c.summary_model = "missing-summary-model"
    c._summary_failure_cooldown_until = 0.0
    c._summary_model_fallen_back = False
    c._previous_summary = None
    c._compute_summary_budget = lambda turns: 500
    c._serialize_for_summary = lambda turns: "serialized turns"
    c._with_summary_prefix = lambda s: s
    return c


class TestSummaryModelFallback:
    def test_404_summary_model_falls_back_to_main_model(self):
        """A 404 from the configured summary model must retry with the
        main model and return the summary — not raise NameError."""
        comp = _make_compressor()
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise _ModelNotFound("model_not_found: no such model")
            return _response("SUMMARY TEXT")

        with patch(
            "agent.context_compressor.call_llm", side_effect=fake_call_llm
        ):
            result = comp._generate_summary(
                [{"role": "user", "content": "turn"}], focus_topic="auth"
            )

        assert result == "SUMMARY TEXT"
        assert len(calls) == 2
        # First call: the configured summary model.
        assert calls[0]["model"] == "missing-summary-model"
        # Retry: main model (summary_model cleared), same task/messages.
        assert "model" not in calls[1]
        assert calls[1]["main_runtime"]["model"] == "main-model"
        # The fallback state was reset on success.
        assert comp._summary_model_fallen_back is False
        assert comp.summary_model == ""

    def test_503_summary_model_also_falls_back(self):
        comp = _make_compressor()
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise type("E", (Exception,), {"status_code": 503})(
                    "Service Unavailable"
                )
            return _response("SUMMARY")

        with patch(
            "agent.context_compressor.call_llm", side_effect=fake_call_llm
        ):
            result = comp._generate_summary([{"role": "user", "content": "t"}])

        assert result == "SUMMARY"
        assert len(calls) == 2

    def test_fallback_only_happens_once(self):
        """If the main model also reports model-not-found, the fallback
        must not recurse forever (the _summary_model_fallen_back flag
        guards it)."""
        comp = _make_compressor()
        calls = []

        def fake_call_llm(**kwargs):
            calls.append(kwargs)
            raise _ModelNotFound("model_not_found")

        with patch(
            "agent.context_compressor.call_llm", side_effect=fake_call_llm
        ):
            result = comp._generate_summary([{"role": "user", "content": "t"}])

        # Exactly one retry with the main model, then the cooldown path.
        assert len(calls) == 2
        assert result is None

    def test_transient_error_does_not_trigger_model_fallback(self):
        """A non-model-not-found error must not clear the summary model."""
        comp = _make_compressor()

        def fake_call_llm(**kwargs):
            raise RuntimeError("connection reset by peer")

        with patch(
            "agent.context_compressor.call_llm", side_effect=fake_call_llm
        ):
            comp._generate_summary([{"role": "user", "content": "t"}])

        assert comp.summary_model == "missing-summary-model"
