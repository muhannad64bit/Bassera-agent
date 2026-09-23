"""Tests for the Nous-Bassera-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"bassera"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``bassera-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "bassera" tag namespace.

``is_nous_bassera_non_agentic`` should only match the actual Nous Research
Bassera-3 / Bassera-4 chat family.
"""

from __future__ import annotations

import pytest

from bassera_cli.model_switch import (
    _BASSERA_MODEL_WARNING,
    _check_bassera_model_warning,
    is_nous_bassera_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/Bassera-3-Llama-3.1-70B",
        "NousResearch/Bassera-3-Llama-3.1-405B",
        "bassera-3",
        "Bassera-3",
        "bassera-4",
        "bassera-4-405b",
        "bassera_4_70b",
        "openrouter/bassera3:70b",
        "openrouter/nousresearch/bassera-4-405b",
        "NousResearch/Bassera3",
        "bassera-3.1",
    ],
)
def test_matches_real_nous_bassera_chat_models(model_name: str) -> None:
    assert is_nous_bassera_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Bassera 3/4"
    )
    assert _check_bassera_model_warning(model_name) == _BASSERA_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "bassera-brain:qwen3-14b-ctx16k",
        "bassera-brain:qwen3-14b-ctx32k",
        "bassera-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat Bassera models we don't warn about
        "bassera-llm-2",
        "bassera2-pro",
        "nous-bassera-2-mistral",
        # Edge cases
        "",
        "bassera",  # bare "bassera" isn't the 3/4 family
        "bassera-brain",
        "brain-bassera-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_bassera_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous Bassera 3/4"
    )
    assert _check_bassera_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_bassera_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_bassera_model_warning("") == ""
