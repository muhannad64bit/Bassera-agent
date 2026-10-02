"""Background watchers extracted from gateway.run.

Behavior-preserving verbatim move (run.py decomposition): the three
periodic background loops of the gateway — session expiry flushing,
failed-platform reconnection, and background-process notification —
moved out of GatewayRunner. gateway.run keeps thin delegating stubs so
startup code and every existing test continue to work unchanged.

The loops reach back into the runner for shared state and helper
methods (session store, agent cache, adapter factory, notification
mode, ...); that coupling is unchanged, only the code location moved.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


def _pending_sentinel():
    """The pending-agent sentinel (deferred import avoids a cycle with run.py)."""
    from gateway.run import _AGENT_PENDING_SENTINEL
    return _AGENT_PENDING_SENTINEL


async def session_expiry_watcher(runner, interval: int = 300):
    """Background task that proactively flushes memories for expired sessions.
    
    Runs every `interval` seconds (default 5 min).  For each session that
    has expired according to its reset policy, flushes memories in a thread
    pool and marks the session so it won't be flushed again.

    This means memories are already saved by the time the user sends their
    next message, so there's no blocking delay.
    """
    await asyncio.sleep(60)  # initial delay — let the gateway fully start
    _flush_failures: dict[str, int] = {}  # session_id -> consecutive failure count
    _MAX_FLUSH_RETRIES = 3
    while runner._running:
        try:
            runner.session_store._ensure_loaded()
            # Collect expired sessions first, then log a single summary.
            _expired_entries = []
            for key, entry in list(runner.session_store._entries.items()):
                if entry.memory_flushed:
                    continue
                if not runner.session_store._is_session_expired(entry):
                    continue
                _expired_entries.append((key, entry))

            if _expired_entries:
                # Extract platform names from session keys for a compact summary.
                # Keys look like "agent:main:telegram:dm:12345" — platform is field [2].
                _platforms: dict[str, int] = {}
                for _k, _e in _expired_entries:
                    _parts = _k.split(":")
                    _plat = _parts[2] if len(_parts) > 2 else "unknown"
                    _platforms[_plat] = _platforms.get(_plat, 0) + 1
                _plat_summary = ", ".join(
                    f"{p}:{c}" for p, c in sorted(_platforms.items())
                )
                logger.info(
                    "Session expiry: %d sessions to flush (%s)",
                    len(_expired_entries), _plat_summary,
                )

            for key, entry in _expired_entries:
                try:
                    await runner._async_flush_memories(entry.session_id, key)
                    # Shut down memory provider and close tool resources
                    # on the cached agent.  Idle agents live in
                    # _agent_cache (not _running_agents), so look there.
                    _cached_agent = None
                    _cache_lock = getattr(runner, "_agent_cache_lock", None)
                    if _cache_lock is not None:
                        with _cache_lock:
                            _cached = runner._agent_cache.get(key)
                            _cached_agent = _cached[0] if isinstance(_cached, tuple) else _cached if _cached else None
                    # Fall back to _running_agents in case the agent is
                    # still mid-turn when the expiry fires.
                    if _cached_agent is None:
                        _cached_agent = runner._running_agents.get(key)
                    if _cached_agent and _cached_agent is not _pending_sentinel():
                        runner._cleanup_agent_resources(_cached_agent)
                    # Drop the cache entry so the AIAgent (and its LLM
                    # clients, tool schemas, memory provider refs) can
                    # be garbage-collected.  Otherwise the cache grows
                    # unbounded across the gateway's lifetime.
                    runner._evict_cached_agent(key)
                    # Mark as flushed and persist to disk so the flag
                    # survives gateway restarts.
                    with runner.session_store._lock:
                        entry.memory_flushed = True
                        runner.session_store._save()
                    logger.debug(
                        "Memory flush completed for session %s",
                        entry.session_id,
                    )
                    _flush_failures.pop(entry.session_id, None)
                except Exception as e:
                    failures = _flush_failures.get(entry.session_id, 0) + 1
                    _flush_failures[entry.session_id] = failures
                    if failures >= _MAX_FLUSH_RETRIES:
                        logger.warning(
                            "Memory flush gave up after %d attempts for %s: %s. "
                            "Marking as flushed to prevent infinite retry loop.",
                            failures, entry.session_id, e,
                        )
                        with runner.session_store._lock:
                            entry.memory_flushed = True
                            runner.session_store._save()
                        _flush_failures.pop(entry.session_id, None)
                    else:
                        logger.debug(
                            "Memory flush failed (%d/%d) for %s: %s",
                            failures, _MAX_FLUSH_RETRIES, entry.session_id, e,
                        )

            if _expired_entries:
                _flushed = sum(
                    1 for _, e in _expired_entries if e.memory_flushed
                )
                _failed = len(_expired_entries) - _flushed
                if _failed:
                    logger.info(
                        "Session expiry done: %d flushed, %d pending retry",
                        _flushed, _failed,
                    )
                else:
                    logger.info(
                        "Session expiry done: %d flushed", _flushed,
                    )

            # Sweep agents that have been idle beyond the TTL regardless
            # of session reset policy.  This catches sessions with very
            # long / "never" reset windows, whose cached AIAgents would
            # otherwise pin memory for the gateway's entire lifetime.
            try:
                _idle_evicted = runner._sweep_idle_cached_agents()
                if _idle_evicted:
                    logger.info(
                        "Agent cache idle sweep: evicted %d agent(s)",
                        _idle_evicted,
                    )
            except Exception as _e:
                logger.debug("Idle agent sweep failed: %s", _e)

            # Periodically prune stale SessionStore entries.  The
            # in-memory dict (and sessions.json) would otherwise grow
            # unbounded in gateways serving many rotating chats /
            # threads / users over long time windows.  Pruning is
            # invisible to users — a resumed session just gets a
            # fresh session_id, exactly as if the reset policy fired.
            _last_prune_ts = getattr(runner, "_last_session_store_prune_ts", 0.0)
            _prune_interval = 3600.0  # once per hour
            if time.time() - _last_prune_ts > _prune_interval:
                try:
                    _max_age = int(
                        getattr(runner.config, "session_store_max_age_days", 0) or 0
                    )
                    if _max_age > 0:
                        _pruned = runner.session_store.prune_old_entries(_max_age)
                        if _pruned:
                            logger.info(
                                "SessionStore prune: dropped %d stale entries",
                                _pruned,
                            )
                except Exception as _e:
                    logger.debug("SessionStore prune failed: %s", _e)
                runner._last_session_store_prune_ts = time.time()
        except Exception as e:
            logger.debug("Session expiry watcher error: %s", e)
        # Sleep in small increments so we can stop quickly
        for _ in range(interval):
            if not runner._running:
                break
            await asyncio.sleep(1)


async def platform_reconnect_watcher(runner) -> None:
    """Background task that periodically retries connecting failed platforms.

    Uses exponential backoff: 30s → 60s → 120s → 240s → 300s (cap).
    Stops retrying a platform after 20 failed attempts or if the error
    is non-retryable (e.g. bad auth token).
    """
    _MAX_ATTEMPTS = 20
    _BACKOFF_CAP = 300  # 5 minutes max between retries

    await asyncio.sleep(10)  # initial delay — let startup finish
    while runner._running:
        if not runner._failed_platforms:
            # Nothing to reconnect — sleep and check again
            for _ in range(30):
                if not runner._running:
                    return
                await asyncio.sleep(1)
            continue

        now = time.monotonic()
        for platform in list(runner._failed_platforms.keys()):
            if not runner._running:
                return
            info = runner._failed_platforms[platform]
            if now < info["next_retry"]:
                continue  # not time yet

            if info["attempts"] >= _MAX_ATTEMPTS:
                logger.warning(
                    "Giving up reconnecting %s after %d attempts",
                    platform.value, info["attempts"],
                )
                del runner._failed_platforms[platform]
                continue

            platform_config = info["config"]
            attempt = info["attempts"] + 1
            logger.info(
                "Reconnecting %s (attempt %d/%d)...",
                platform.value, attempt, _MAX_ATTEMPTS,
            )

            try:
                adapter = runner._create_adapter(platform, platform_config)
                if not adapter:
                    logger.warning(
                        "Reconnect %s: adapter creation returned None, removing from retry queue",
                        platform.value,
                    )
                    del runner._failed_platforms[platform]
                    continue

                adapter.set_message_handler(runner._handle_message)
                adapter.set_fatal_error_handler(runner._handle_adapter_fatal_error)
                adapter.set_session_store(runner.session_store)
                adapter.set_busy_session_handler(runner._handle_active_session_busy_message)

                success = await adapter.connect()
                if success:
                    runner.adapters[platform] = adapter
                    runner._sync_voice_mode_state_to_adapter(adapter)
                    runner.delivery_router.adapters = runner.adapters
                    del runner._failed_platforms[platform]
                    runner._update_platform_runtime_status(
                        platform.value,
                        platform_state="connected",
                        error_code=None,
                        error_message=None,
                    )
                    logger.info("✓ %s reconnected successfully", platform.value)

                    # Rebuild channel directory with the new adapter
                    try:
                        from gateway.channel_directory import build_channel_directory
                        build_channel_directory(runner.adapters)
                    except Exception:
                        pass
                else:
                    # Check if the failure is non-retryable
                    if adapter.has_fatal_error and not adapter.fatal_error_retryable:
                        runner._update_platform_runtime_status(
                            platform.value,
                            platform_state="fatal",
                            error_code=adapter.fatal_error_code,
                            error_message=adapter.fatal_error_message,
                        )
                        logger.warning(
                            "Reconnect %s: non-retryable error (%s), removing from retry queue",
                            platform.value, adapter.fatal_error_message,
                        )
                        del runner._failed_platforms[platform]
                    else:
                        runner._update_platform_runtime_status(
                            platform.value,
                            platform_state="retrying",
                            error_code=adapter.fatal_error_code,
                            error_message=adapter.fatal_error_message or "failed to reconnect",
                        )
                        backoff = min(30 * (2 ** (attempt - 1)), _BACKOFF_CAP)
                        info["attempts"] = attempt
                        info["next_retry"] = time.monotonic() + backoff
                        logger.info(
                            "Reconnect %s failed, next retry in %ds",
                            platform.value, backoff,
                        )
            except Exception as e:
                runner._update_platform_runtime_status(
                    platform.value,
                    platform_state="retrying",
                    error_code=None,
                    error_message=str(e),
                )
                backoff = min(30 * (2 ** (attempt - 1)), _BACKOFF_CAP)
                info["attempts"] = attempt
                info["next_retry"] = time.monotonic() + backoff
                logger.warning(
                    "Reconnect %s error: %s, next retry in %ds",
                    platform.value, e, backoff,
                )

        # Check every 10 seconds for platforms that need reconnection
        for _ in range(10):
            if not runner._running:
                return
            await asyncio.sleep(1)


async def run_process_watcher(runner, watcher: dict) -> None:
    """
    Periodically check a background process and push updates to the user.

    Runs as an asyncio task. Stays silent when nothing changed.
    Auto-removes when the process exits or is killed.

    Notification mode (from ``display.background_process_notifications``):
      - ``all``    — running-output updates + final message
      - ``result`` — final completion message only
      - ``error``  — final message only when exit code != 0
      - ``off``    — no messages at all
    """
    from tools.process_registry import process_registry

    session_id = watcher["session_id"]
    interval = watcher["check_interval"]
    session_key = watcher.get("session_key", "")
    platform_name = watcher.get("platform", "")
    chat_id = watcher.get("chat_id", "")
    thread_id = watcher.get("thread_id", "")
    user_id = watcher.get("user_id", "")
    user_name = watcher.get("user_name", "")
    agent_notify = watcher.get("notify_on_complete", False)
    notify_mode = runner._load_background_notifications_mode()

    logger.debug("Process watcher started: %s (every %ss, notify=%s, agent_notify=%s)",
                  session_id, interval, notify_mode, agent_notify)

    if notify_mode == "off" and not agent_notify:
        # Still wait for the process to exit so we can log it, but don't
        # push any messages to the user.
        while True:
            await asyncio.sleep(interval)
            session = process_registry.get(session_id)
            if session is None or session.exited:
                break
        logger.debug("Process watcher ended (silent): %s", session_id)
        return

    last_output_len = 0
    while True:
        await asyncio.sleep(interval)

        session = process_registry.get(session_id)
        if session is None:
            break

        current_output_len = len(session.output_buffer)
        has_new_output = current_output_len > last_output_len
        last_output_len = current_output_len

        if session.exited:
            # --- Agent-triggered completion: inject synthetic message ---
            # Skip if the agent already consumed the result via wait/poll/log
            from tools.process_registry import process_registry as _pr_check
            if agent_notify and not _pr_check.is_completion_consumed(session_id):
                from tools.ansi_strip import strip_ansi
                _out = strip_ansi(session.output_buffer[-2000:]) if session.output_buffer else ""
                synth_text = (
                    f"[SYSTEM: Background process {session_id} completed "
                    f"(exit code {session.exit_code}).\n"
                    f"Command: {session.command}\n"
                    f"Output:\n{_out}]"
                )
                source = runner._build_process_event_source({
                    "session_id": session_id,
                    "session_key": session_key,
                    "platform": platform_name,
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "user_id": user_id,
                    "user_name": user_name,
                })
                if not source:
                    logger.warning(
                        "Dropping completion notification with no routing metadata for process %s",
                        session_id,
                    )
                    break

                adapter = None
                for p, a in runner.adapters.items():
                    if p == source.platform:
                        adapter = a
                        break
                if adapter and source.chat_id:
                    try:
                        from gateway.platforms.base import MessageEvent, MessageType
                        synth_event = MessageEvent(
                            text=synth_text,
                            message_type=MessageType.TEXT,
                            source=source,
                            internal=True,
                        )
                        logger.info(
                            "Process %s finished — injecting agent notification for session %s chat=%s thread=%s",
                            session_id,
                            session_key,
                            source.chat_id,
                            source.thread_id,
                        )
                        await adapter.handle_message(synth_event)
                    except Exception as e:
                        logger.error("Agent notify injection error: %s", e)
                break

            # --- Normal text-only notification ---
            # Decide whether to notify based on mode
            should_notify = (
                notify_mode in ("all", "result")
                or (notify_mode == "error" and session.exit_code not in (0, None))
            )
            if should_notify:
                new_output = session.output_buffer[-1000:] if session.output_buffer else ""
                message_text = (
                    f"[Background process {session_id} finished with exit code {session.exit_code}~ "
                    f"Here's the final output:\n{new_output}]"
                )
                adapter = None
                for p, a in runner.adapters.items():
                    if p.value == platform_name:
                        adapter = a
                        break
                if adapter and chat_id:
                    try:
                        send_meta = {"thread_id": thread_id} if thread_id else None
                        await adapter.send(chat_id, message_text, metadata=send_meta)
                    except Exception as e:
                        logger.error("Watcher delivery error: %s", e)
            break

        elif has_new_output and notify_mode == "all" and not agent_notify:
            # New output available -- deliver status update (only in "all" mode)
            # Skip periodic updates for agent_notify watchers (they only care about completion)
            new_output = session.output_buffer[-500:] if session.output_buffer else ""
            message_text = (
                f"[Background process {session_id} is still running~ "
                f"New output:\n{new_output}]"
            )
            adapter = None
            for p, a in runner.adapters.items():
                if p.value == platform_name:
                    adapter = a
                    break
            if adapter and chat_id:
                try:
                    send_meta = {"thread_id": thread_id} if thread_id else None
                    await adapter.send(chat_id, message_text, metadata=send_meta)
                except Exception as e:
                    logger.error("Watcher delivery error: %s", e)

    logger.debug("Process watcher ended: %s", session_id)
