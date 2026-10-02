"""Characterization tests for GatewayRunner._handle_model_command.

The /model handler (326 lines: flag parsing, config reading, session
overrides, the interactive picker path with its async switch callback,
the text-list fallback, cached-agent in-place switching, --global
persistence, and rich confirmation building) had only one test — the
custom-providers list path. These pin the rest of the observable
contract BEFORE any extraction from gateway/run.py, and must keep
passing unchanged after the handler moves (the runner keeps a thin
delegating stub).
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._evict_cached_agent = MagicMock()
    return runner


def _make_event(text="/model test-model"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"
        ),
    )


def _session_key(runner, event):
    return runner._session_key_for_source(event.source)


def _switch_result(**over):
    base = dict(
        success=True,
        error_message=None,
        new_model="test-model",
        target_provider="openrouter",
        api_key="sk-test",
        base_url="",
        api_mode="openai",
        provider_label="OpenRouter",
        model_info=None,
        warning_message=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _PickerAdapter:
    """Minimal adapter that advertises an interactive model picker."""

    def __init__(self):
        self.picker_calls = []

    async def send_model_picker(
        self,
        chat_id,
        providers,
        current_model,
        current_provider,
        session_key,
        on_model_selected,
        metadata=None,
    ):
        self.picker_calls.append(
            {
                "chat_id": chat_id,
                "providers": providers,
                "current_model": current_model,
                "current_provider": current_provider,
                "session_key": session_key,
                "on_model_selected": on_model_selected,
                "metadata": metadata,
            }
        )
        return SimpleNamespace(success=True)


@pytest.fixture
def hermetic_home(tmp_path, monkeypatch):
    """Empty BASSERA_HOME: no config.yaml, so defaults apply everywhere."""
    home = tmp_path / ".bassera"
    home.mkdir()
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_bassera_home", home)
    return home


class TestModelSwitchPath:
    @pytest.mark.asyncio
    async def test_switch_success_stores_override_and_note(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        event = _make_event("/model test-model")
        sk = _session_key(runner, event)
        captured = {}

        def fake_switch(**kwargs):
            captured.update(kwargs)
            return _switch_result()

        monkeypatch.setattr("bassera_cli.model_switch.switch_model", fake_switch)

        result = await runner._handle_model_command(event)

        assert "Model switched to `test-model`" in result
        assert "Provider: OpenRouter" in result
        assert "session only" in result
        # Session override stored with the full runtime bundle.
        assert runner._session_model_overrides[sk]["model"] == "test-model"
        assert runner._session_model_overrides[sk]["provider"] == "openrouter"
        assert runner._session_model_overrides[sk]["api_key"] == "sk-test"
        # A note is queued so the next user message tells the model.
        assert "just switched" in runner._pending_model_notes[sk]
        # The cached agent is evicted so the next turn rebuilds.
        runner._evict_cached_agent.assert_called_once_with(sk)
        # The switch ran against the default (no-config) state.
        assert captured["raw_input"] == "test-model"
        assert captured["is_global"] is False

    @pytest.mark.asyncio
    async def test_switch_updates_cached_agent_in_place(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        event = _make_event("/model test-model")
        sk = _session_key(runner, event)
        agent = MagicMock()
        runner._agent_cache[sk] = (agent, {"meta": True})

        monkeypatch.setattr(
            "bassera_cli.model_switch.switch_model",
            lambda **kw: _switch_result(),
        )

        await runner._handle_model_command(event)

        agent.switch_model.assert_called_once_with(
            new_model="test-model",
            new_provider="openrouter",
            api_key="sk-test",
            base_url="",
            api_mode="openai",
        )

    @pytest.mark.asyncio
    async def test_switch_failure_returns_error_and_stores_nothing(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        event = _make_event("/model nope")

        monkeypatch.setattr(
            "bassera_cli.model_switch.switch_model",
            lambda **kw: _switch_result(
                success=False, error_message="unknown model 'nope'"
            ),
        )

        result = await runner._handle_model_command(event)

        assert result.startswith("Error: unknown model 'nope'")
        assert runner._session_model_overrides == {}
        assert not hasattr(runner, "_pending_model_notes") or not runner._pending_model_notes
        runner._evict_cached_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_global_flag_persists_to_config(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        saved = {}

        monkeypatch.setattr(
            "bassera_cli.model_switch.switch_model", lambda **kw: _switch_result()
        )

        def fake_save(cfg):
            saved.update(cfg)

        monkeypatch.setattr("bassera_cli.config.save_config", fake_save)

        result = await runner._handle_model_command(
            _make_event("/model test-model --global")
        )

        assert "Saved to config.yaml" in result
        assert saved["model"]["default"] == "test-model"
        assert saved["model"]["provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_session_override_is_passed_as_current_state(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        event = _make_event("/model other-model")
        sk = _session_key(runner, event)
        runner._session_model_overrides[sk] = {
            "model": "old-model",
            "provider": "anthropic",
            "base_url": "https://old.example/v1",
            "api_key": "sk-old",
        }
        captured = {}

        def fake_switch(**kwargs):
            captured.update(kwargs)
            return _switch_result()

        monkeypatch.setattr("bassera_cli.model_switch.switch_model", fake_switch)

        await runner._handle_model_command(event)

        # The handler must switch FROM the session override, not the
        # (absent) global config.
        assert captured["current_model"] == "old-model"
        assert captured["current_provider"] == "anthropic"
        assert captured["current_base_url"] == "https://old.example/v1"
        assert captured["current_api_key"] == "sk-old"

    @pytest.mark.asyncio
    async def test_anthropic_api_mode_adds_cache_notice(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        monkeypatch.setattr(
            "bassera_cli.model_switch.switch_model",
            lambda **kw: _switch_result(api_mode="anthropic_messages"),
        )
        result = await runner._handle_model_command(_make_event())
        assert "Prompt caching: enabled" in result

    @pytest.mark.asyncio
    async def test_warning_message_is_surfaced(self, hermetic_home, monkeypatch):
        runner = _make_runner()
        monkeypatch.setattr(
            "bassera_cli.model_switch.switch_model",
            lambda **kw: _switch_result(warning_message="model is deprecated"),
        )
        result = await runner._handle_model_command(_make_event())
        assert "Warning: model is deprecated" in result


class TestNoArgsListPath:
    @pytest.mark.asyncio
    async def test_text_list_shows_current_and_hints(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        monkeypatch.setattr(
            "bassera_cli.model_switch.list_authenticated_providers",
            lambda **kw: [
                {
                    "name": "OpenRouter",
                    "slug": "openrouter",
                    "is_current": True,
                    "models": ["m1", "m2"],
                    "total_models": 2,
                    "api_url": None,
                }
            ],
        )

        result = await runner._handle_model_command(_make_event("/model"))

        assert result is not None
        assert "Current: `unknown`" in result
        assert "**OpenRouter** `--provider openrouter` (current):" in result
        assert "`m1`, `m2`" in result
        assert "`/model <name>` — switch model" in result
        assert "`/model <name> --global` — persist" in result


class TestInteractivePickerPath:
    @pytest.mark.asyncio
    async def test_picker_platform_sends_picker_and_returns_none(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        adapter = _PickerAdapter()
        runner.adapters[Platform.TELEGRAM] = adapter
        monkeypatch.setattr(
            "bassera_cli.model_switch.list_authenticated_providers",
            lambda **kw: [
                {
                    "name": "OpenRouter",
                    "slug": "openrouter",
                    "is_current": True,
                    "models": ["m1"],
                    "total_models": 1,
                    "api_url": None,
                }
            ],
        )

        result = await runner._handle_model_command(_make_event("/model"))

        # Picker sent — the adapter handles the response; the handler
        # returns None (no text reply).
        assert result is None
        assert len(adapter.picker_calls) == 1
        call = adapter.picker_calls[0]
        assert call["chat_id"] == "12345"
        assert call["current_provider"] == "openrouter"

    @pytest.mark.asyncio
    async def test_picker_callback_switches_model_and_updates_session(
        self, hermetic_home, monkeypatch
    ):
        runner = _make_runner()
        adapter = _PickerAdapter()
        runner.adapters[Platform.TELEGRAM] = adapter
        monkeypatch.setattr(
            "bassera_cli.model_switch.list_authenticated_providers",
            lambda **kw: [
                {
                    "name": "OpenRouter",
                    "slug": "openrouter",
                    "is_current": True,
                    "models": ["m1"],
                    "total_models": 1,
                    "api_url": None,
                }
            ],
        )

        # Patch BEFORE the handler call: the picker callback closure
        # captures switch_model when _handle_model_command imports it.
        monkeypatch.setattr(
            "bassera_cli.model_switch.switch_model",
            lambda **kw: _switch_result(new_model="picked-model"),
        )

        event = _make_event("/model")
        await runner._handle_model_command(event)

        callback = adapter.picker_calls[0]["on_model_selected"]
        assert callable(callback)

        confirmation = await callback("12345", "picked-model", "openrouter")

        assert "Model switched to `picked-model`" in confirmation
        assert "session only" in confirmation
        sk = _session_key(runner, event)
        assert runner._session_model_overrides[sk]["model"] == "picked-model"
        assert "picked-model" in runner._pending_model_notes[sk]
        runner._evict_cached_agent.assert_called_once_with(sk)
