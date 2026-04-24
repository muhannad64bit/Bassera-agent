"""Tests for the Nous-Wafi-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"wafi"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``wafi-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "wafi" tag namespace.

``is_nous_wafi_non_agentic`` should only match the actual Nous Research
Wafi-3 / Wafi-4 chat family.
"""

from __future__ import annotations

import pytest

from wafi_cli.model_switch import (
    _HERMES_MODEL_WARNING,
    _check_wafi_model_warning,
    is_nous_wafi_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/Wafi-3-Llama-3.1-70B",
        "NousResearch/Wafi-3-Llama-3.1-405B",
        "wafi-3",
        "Wafi-3",
        "wafi-4",
        "wafi-4-405b",
        "wafi_4_70b",
        "openrouter/wafi3:70b",
        "openrouter/nousresearch/wafi-4-405b",
        "NousResearch/Wafi3",
        "wafi-3.1",
    ],
)
def test_matches_real_nous_wafi_chat_models(model_name: str) -> None:
    assert is_nous_wafi_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Wafi 3/4"
    )
    assert _check_wafi_model_warning(model_name) == _HERMES_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "wafi-brain:qwen3-14b-ctx16k",
        "wafi-brain:qwen3-14b-ctx32k",
        "wafi-honcho:qwen3-8b-ctx8k",
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
        # Non-chat Wafi models we don't warn about
        "wafi-llm-2",
        "wafi2-pro",
        "nous-wafi-2-mistral",
        # Edge cases
        "",
        "wafi",  # bare "wafi" isn't the 3/4 family
        "wafi-brain",
        "brain-wafi-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_wafi_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous Wafi 3/4"
    )
    assert _check_wafi_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_wafi_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_wafi_model_warning("") == ""
