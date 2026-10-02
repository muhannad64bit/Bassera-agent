"""Characterization tests for GatewayRunner._session_expiry_watcher.

The session-expiry background loop had only indirect coverage (one comment
reference in test_session_store_prune.py) before the watchers were extracted
from gateway/run.py. These pin the observable contract: which sessions get
flushed, when the flushed flag is persisted, retry-then-give-up semantics on
flush failures, cached-agent cleanup, and loop exit.

They drive the loop with the same fake-sleep idiom as
test_platform_reconnect.py / test_background_process_notifications.py and
must keep passing unchanged after the watcher moves to gateway.watchers
(the runner keeps a thin delegating stub).
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.run import GatewayRunner


class _FakeStore:
    """Minimal stand-in for SessionStore: the exact surface the watcher uses."""

    def __init__(self, entries):
        self._entries = entries
        self._lock = threading.Lock()
        self.saved = 0

    def _ensure_loaded(self):
        return None

    def _is_session_expired(self, entry):
        return getattr(entry, "expired", False)

    def _save(self):
        self.saved += 1

    def prune_old_entries(self, max_age):
        return 0


def _entry(session_id, *, expired=True, memory_flushed=False):
    return SimpleNamespace(
        session_id=session_id, expired=expired, memory_flushed=memory_flushed
    )


def _make_runner(entries):
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner.session_store = _FakeStore(entries)
    runner._async_flush_memories = AsyncMock(return_value=None)
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._running_agents = {}
    runner._cleanup_agent_resources = MagicMock()
    runner._evict_cached_agent = MagicMock()
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    runner.config = SimpleNamespace(session_store_max_age_days=0)
    return runner


def _run_watcher(runner, stop_after_call):
    """Run _session_expiry_watcher until the fake sleep count reaches the
    stop threshold (1 initial sleep + 1 sleep per interval pass)."""
    real_sleep = asyncio.sleep

    async def fake_sleep(n):
        fake_sleep.calls += 1
        if fake_sleep.calls >= stop_after_call:
            runner._running = False
        await real_sleep(0)

    fake_sleep.calls = 0

    async def run():
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await runner._session_expiry_watcher(interval=1)

    return run()


class TestSessionExpiryWatcherCharacterization:
    @pytest.mark.asyncio
    async def test_expired_session_flushed_marked_and_evicted(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})

        await _run_watcher(runner, stop_after_call=2)

        runner._async_flush_memories.assert_awaited_once_with(
            "sess-1", "agent:main:telegram:dm:123"
        )
        assert entry.memory_flushed is True
        assert runner.session_store.saved >= 1
        runner._evict_cached_agent.assert_called_once_with(
            "agent:main:telegram:dm:123"
        )

    @pytest.mark.asyncio
    async def test_already_flushed_entry_is_skipped(self):
        entry = _entry("sess-1", memory_flushed=True)
        runner = _make_runner({"agent:main:telegram:dm:123": entry})

        await _run_watcher(runner, stop_after_call=2)

        runner._async_flush_memories.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_expired_entry_is_skipped(self):
        entry = _entry("sess-1", expired=False)
        runner = _make_runner({"agent:main:telegram:dm:123": entry})

        await _run_watcher(runner, stop_after_call=2)

        runner._async_flush_memories.assert_not_awaited()
        assert entry.memory_flushed is False

    @pytest.mark.asyncio
    async def test_flush_failure_retried_then_gives_up_after_three(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})
        runner._async_flush_memories = AsyncMock(side_effect=RuntimeError("boom"))

        # 1 initial sleep + 3 interval sleeps = 3 full passes.
        await _run_watcher(runner, stop_after_call=4)

        assert runner._async_flush_memories.await_count == 3
        # On the third consecutive failure the watcher must give up and
        # mark the entry flushed to avoid an infinite retry loop.
        assert entry.memory_flushed is True
        assert runner.session_store.saved >= 1

    @pytest.mark.asyncio
    async def test_success_clears_failure_counter(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})
        calls = {"n": 0}

        async def flaky_flush(session_id, key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")

        runner._async_flush_memories = AsyncMock(side_effect=flaky_flush)

        # 3 passes: fail (retry recorded), succeed (marked flushed),
        # third pass skips the already-flushed entry.
        await _run_watcher(runner, stop_after_call=4)

        assert calls["n"] == 2
        assert entry.memory_flushed is True

    @pytest.mark.asyncio
    async def test_cached_agent_is_cleaned_up_on_flush(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})
        cached_agent = object()
        # _agent_cache tuples are (agent, metadata): element [0] is the agent
        # whose resources get cleaned up.
        runner._agent_cache["agent:main:telegram:dm:123"] = (cached_agent, "meta")

        await _run_watcher(runner, stop_after_call=2)

        runner._cleanup_agent_resources.assert_called_once_with(cached_agent)

    @pytest.mark.asyncio
    async def test_running_agent_fallback_is_cleaned_up(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})
        running_agent = object()
        runner._running_agents["agent:main:telegram:dm:123"] = running_agent

        await _run_watcher(runner, stop_after_call=2)

        runner._cleanup_agent_resources.assert_called_once_with(running_agent)

    @pytest.mark.asyncio
    async def test_pending_sentinel_agent_is_never_cleaned_up(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})
        from gateway.run import _AGENT_PENDING_SENTINEL

        runner._running_agents["agent:main:telegram:dm:123"] = _AGENT_PENDING_SENTINEL

        await _run_watcher(runner, stop_after_call=2)

        runner._cleanup_agent_resources.assert_not_called()

    @pytest.mark.asyncio
    async def test_watcher_exits_before_any_work_when_stopped_immediately(self):
        entry = _entry("sess-1")
        runner = _make_runner({"agent:main:telegram:dm:123": entry})

        # Stop during the initial 60s startup sleep: no pass may run.
        await _run_watcher(runner, stop_after_call=1)

        runner._async_flush_memories.assert_not_awaited()
        assert entry.memory_flushed is False

    @pytest.mark.asyncio
    async def test_idle_sweep_runs_each_pass(self):
        entry = _entry("sess-1", memory_flushed=True)
        runner = _make_runner({"agent:main:telegram:dm:123": entry})
        runner._sweep_idle_cached_agents = MagicMock(return_value=2)

        await _run_watcher(runner, stop_after_call=2)

        runner._sweep_idle_cached_agents.assert_called_once()
