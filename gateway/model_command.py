"""The /model command handler, extracted from gateway.run.

Behavior-preserving verbatim move (run.py decomposition): the whole
/model surface — flag parsing, config + session-override state, the
interactive picker path (including its async switch callback), the
text-list fallback, cached-agent in-place switching, --global
persistence, and confirmation building — moved out of GatewayRunner.
gateway.run keeps a thin delegating stub; every call site and test
keeps working unchanged.

The handler reaches back into the runner for shared state (adapters,
session overrides, agent cache, eviction) and reads the agent home
via _bassera_home_path() so monkeypatching gateway.run._bassera_home
still applies (call-time lookup, same pattern as gateway.self_update).
"""

import logging
from typing import Optional

from gateway.platforms.base import MessageEvent

logger = logging.getLogger(__name__)


def _bassera_home_path():
    """The Bassera agent home (deferred import avoids a cycle with run.py)."""
    from gateway.run import _bassera_home
    return _bassera_home


async def handle_model_command(runner, event: MessageEvent) -> Optional[str]:
    """Handle /model command — switch model for this session.

    Supports:
      /model                              — interactive picker (Telegram/Discord) or text list
      /model <name>                       — switch for this session only
      /model <name> --global              — switch and persist to config.yaml
      /model <name> --provider <provider> — switch provider + model
      /model --provider <provider>        — switch to provider, auto-detect model
    """
    import yaml
    from bassera_cli.model_switch import (
        switch_model as _switch_model, parse_model_flags,
        list_authenticated_providers,
    )
    from bassera_cli.providers import get_label

    raw_args = event.get_command_args().strip()

    # Parse --provider and --global flags
    model_input, explicit_provider, persist_global = parse_model_flags(raw_args)

    # Read current model/provider from config
    current_model = ""
    current_provider = "openrouter"
    current_base_url = ""
    current_api_key = ""
    user_provs = None
    custom_provs = None
    config_path = _bassera_home_path() / "config.yaml"
    try:
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, dict):
                current_model = model_cfg.get("default", "")
                current_provider = model_cfg.get("provider", current_provider)
                current_base_url = model_cfg.get("base_url", "")
            user_provs = cfg.get("providers")
            try:
                from bassera_cli.config import get_compatible_custom_providers
                custom_provs = get_compatible_custom_providers(cfg)
            except Exception:
                custom_provs = cfg.get("custom_providers")
    except Exception:
        pass

    # Check for session override
    source = event.source
    session_key = runner._session_key_for_source(source)
    override = runner._session_model_overrides.get(session_key, {})
    if override:
        current_model = override.get("model", current_model)
        current_provider = override.get("provider", current_provider)
        current_base_url = override.get("base_url", current_base_url)
        current_api_key = override.get("api_key", current_api_key)

    # No args: show interactive picker (Telegram/Discord) or text list
    if not model_input and not explicit_provider:
        # Try interactive picker if the platform supports it
        adapter = runner.adapters.get(source.platform)
        has_picker = (
            adapter is not None
            and getattr(type(adapter), "send_model_picker", None) is not None
        )

        if has_picker:
            try:
                providers = list_authenticated_providers(
                    current_provider=current_provider,
                    user_providers=user_provs,
                    custom_providers=custom_provs,
                    max_models=50,
                )
            except Exception:
                providers = []

            if providers:
                # Build a callback closure for when the user picks a model.
                # Captures runner + locals needed for the switch logic.
                _self = runner
                _session_key = session_key
                _cur_model = current_model
                _cur_provider = current_provider
                _cur_base_url = current_base_url
                _cur_api_key = current_api_key

                async def _on_model_selected(
                    _chat_id: str, model_id: str, provider_slug: str
                ) -> str:
                    """Perform the model switch and return confirmation text."""
                    result = _switch_model(
                        raw_input=model_id,
                        current_provider=_cur_provider,
                        current_model=_cur_model,
                        current_base_url=_cur_base_url,
                        current_api_key=_cur_api_key,
                        is_global=False,
                        explicit_provider=provider_slug,
                        user_providers=user_provs,
                        custom_providers=custom_provs,
                    )
                    if not result.success:
                        return f"Error: {result.error_message}"

                    # Update cached agent in-place
                    cached_entry = None
                    _cache_lock = getattr(_self, "_agent_cache_lock", None)
                    _cache = getattr(_self, "_agent_cache", None)
                    if _cache_lock and _cache is not None:
                        with _cache_lock:
                            cached_entry = _cache.get(_session_key)
                    if cached_entry and cached_entry[0] is not None:
                        try:
                            cached_entry[0].switch_model(
                                new_model=result.new_model,
                                new_provider=result.target_provider,
                                api_key=result.api_key,
                                base_url=result.base_url,
                                api_mode=result.api_mode,
                            )
                        except Exception as exc:
                            logger.warning("Picker model switch failed for cached agent: %s", exc)

                    # Store model note + session override
                    if not hasattr(_self, "_pending_model_notes"):
                        _self._pending_model_notes = {}
                    _self._pending_model_notes[_session_key] = (
                        f"[Note: model was just switched from {_cur_model} to {result.new_model} "
                        f"via {result.provider_label or result.target_provider}. "
                        f"Adjust your runner-identification accordingly.]"
                    )
                    _self._session_model_overrides[_session_key] = {
                        "model": result.new_model,
                        "provider": result.target_provider,
                        "api_key": result.api_key,
                        "base_url": result.base_url,
                        "api_mode": result.api_mode,
                    }

                    # Evict cached agent so the next turn creates a fresh
                    # agent from the override rather than relying on the
                    # stale cache signature to trigger a rebuild.
                    _self._evict_cached_agent(_session_key)

                    # Build confirmation text
                    plabel = result.provider_label or result.target_provider
                    lines = [f"Model switched to `{result.new_model}`"]
                    lines.append(f"Provider: {plabel}")
                    mi = result.model_info
                    if mi:
                        if mi.context_window:
                            lines.append(f"Context: {mi.context_window:,} tokens")
                        if mi.max_output:
                            lines.append(f"Max output: {mi.max_output:,} tokens")
                        if mi.has_cost_data():
                            lines.append(f"Cost: {mi.format_cost()}")
                        lines.append(f"Capabilities: {mi.format_capabilities()}")
                    lines.append("_(session only — use `/model <name> --global` to persist)_")
                    return "\n".join(lines)

                metadata = {"thread_id": source.thread_id} if source.thread_id else None
                result = await adapter.send_model_picker(
                    chat_id=source.chat_id,
                    providers=providers,
                    current_model=current_model,
                    current_provider=current_provider,
                    session_key=session_key,
                    on_model_selected=_on_model_selected,
                    metadata=metadata,
                )
                if result.success:
                    return None  # Picker sent — adapter handles the response

        # Fallback: text list (for platforms without picker or if picker failed)
        provider_label = get_label(current_provider)
        lines = [f"Current: `{current_model or 'unknown'}` on {provider_label}", ""]

        try:
            providers = list_authenticated_providers(
                current_provider=current_provider,
                user_providers=user_provs,
                custom_providers=custom_provs,
                max_models=5,
            )
            for p in providers:
                tag = " (current)" if p["is_current"] else ""
                lines.append(f"**{p['name']}** `--provider {p['slug']}`{tag}:")
                if p["models"]:
                    model_strs = ", ".join(f"`{m}`" for m in p["models"])
                    extra = f" (+{p['total_models'] - len(p['models'])} more)" if p["total_models"] > len(p["models"]) else ""
                    lines.append(f"  {model_strs}{extra}")
                elif p.get("api_url"):
                    lines.append(f"  `{p['api_url']}`")
                lines.append("")
        except Exception:
            pass

        lines.append("`/model <name>` — switch model")
        lines.append("`/model <name> --provider <slug>` — switch provider")
        lines.append("`/model <name> --global` — persist")
        return "\n".join(lines)

    # Perform the switch
    result = _switch_model(
        raw_input=model_input,
        current_provider=current_provider,
        current_model=current_model,
        current_base_url=current_base_url,
        current_api_key=current_api_key,
        is_global=persist_global,
        explicit_provider=explicit_provider,
        user_providers=user_provs,
        custom_providers=custom_provs,
    )

    if not result.success:
        return f"Error: {result.error_message}"

    # If there's a cached agent, update it in-place
    cached_entry = None
    _cache_lock = getattr(runner, "_agent_cache_lock", None)
    _cache = getattr(runner, "_agent_cache", None)
    if _cache_lock and _cache is not None:
        with _cache_lock:
            cached_entry = _cache.get(session_key)

    if cached_entry and cached_entry[0] is not None:
        try:
            cached_entry[0].switch_model(
                new_model=result.new_model,
                new_provider=result.target_provider,
                api_key=result.api_key,
                base_url=result.base_url,
                api_mode=result.api_mode,
            )
        except Exception as exc:
            logger.warning("In-place model switch failed for cached agent: %s", exc)

    # Store a note to prepend to the next user message so the model
    # knows about the switch (avoids system messages mid-history).
    if not hasattr(runner, "_pending_model_notes"):
        runner._pending_model_notes = {}
    runner._pending_model_notes[session_key] = (
        f"[Note: model was just switched from {current_model} to {result.new_model} "
        f"via {result.provider_label or result.target_provider}. "
        f"Adjust your runner-identification accordingly.]"
    )

    # Store session override so next agent creation uses the new model
    runner._session_model_overrides[session_key] = {
        "model": result.new_model,
        "provider": result.target_provider,
        "api_key": result.api_key,
        "base_url": result.base_url,
        "api_mode": result.api_mode,
    }

    # Evict cached agent so the next turn creates a fresh agent from the
    # override rather than relying on cache signature mismatch detection.
    runner._evict_cached_agent(session_key)

    # Persist to config if --global
    if persist_global:
        try:
            if config_path.exists():
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            else:
                cfg = {}
            model_cfg = cfg.setdefault("model", {})
            model_cfg["default"] = result.new_model
            model_cfg["provider"] = result.target_provider
            if result.base_url:
                model_cfg["base_url"] = result.base_url
            from bassera_cli.config import save_config
            save_config(cfg)
        except Exception as e:
            logger.warning("Failed to persist model switch: %s", e)

    # Build confirmation message with full metadata
    provider_label = result.provider_label or result.target_provider
    lines = [f"Model switched to `{result.new_model}`"]
    lines.append(f"Provider: {provider_label}")

    # Rich metadata from models.dev
    mi = result.model_info
    if mi:
        if mi.context_window:
            lines.append(f"Context: {mi.context_window:,} tokens")
        if mi.max_output:
            lines.append(f"Max output: {mi.max_output:,} tokens")
        if mi.has_cost_data():
            lines.append(f"Cost: {mi.format_cost()}")
        lines.append(f"Capabilities: {mi.format_capabilities()}")
    else:
        try:
            from agent.model_metadata import get_model_context_length
            ctx = get_model_context_length(
                result.new_model,
                base_url=result.base_url or current_base_url,
                api_key=result.api_key or current_api_key,
                provider=result.target_provider,
            )
            lines.append(f"Context: {ctx:,} tokens")
        except Exception:
            pass

    # Cache notice
    cache_enabled = (
        ("openrouter" in (result.base_url or "").lower() and "claude" in result.new_model.lower())
        or result.api_mode == "anthropic_messages"
    )
    if cache_enabled:
        lines.append("Prompt caching: enabled")

    if result.warning_message:
        lines.append(f"Warning: {result.warning_message}")

    if persist_global:
        lines.append("Saved to config.yaml (`--global`)")
    else:
        lines.append("_(session only -- add `--global` to persist)_")

    return "\n".join(lines)
