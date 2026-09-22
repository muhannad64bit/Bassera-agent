"""Per-thread interrupt signaling for all tools.

Provides thread-scoped interrupt tracking so that interrupting one agent
session does not kill tools running in other sessions.  This is critical
in the gateway where multiple agents run concurrently in the same process.

The agent stores its execution thread ID at the start of run_conversation()
and passes it to set_interrupt()/clear_interrupt().  Tools call
is_interrupted() which checks the CURRENT thread — no argument needed.

Usage in tools:
    from tools.interrupt import is_interrupted
    if is_interrupted():
        return {"output": "[interrupted]", "returncode": 130}
"""

import logging
import os
import threading
import weakref

logger = logging.getLogger(__name__)

# Opt-in debug tracing — pairs with HERMES_DEBUG_INTERRUPT in
# tools/environments/base.py.  Enables per-call logging of set/check so the
# caller thread, target thread, and current state are visible when
# diagnosing "interrupt signaled but tool never saw it" reports.
_DEBUG_INTERRUPT = bool(os.getenv("HERMES_DEBUG_INTERRUPT"))

if _DEBUG_INTERRUPT:
    # AIAgent's quiet_mode path forces `tools` logger to ERROR on CLI startup.
    # Force our own logger back to INFO so the trace is visible in agent.log.
    logger.setLevel(logging.INFO)

# Set of thread idents that have been interrupted.
_interrupted_threads: set[int] = set()
# ident → weakref to the Thread object that was signaled. Thread idents
# can be REUSED by the OS once a thread dies, so a bare ident flag from a
# dead interrupted thread can land on an unrelated new thread in a
# long-lived gateway — silently turning every tool it runs into a
# "[interrupted]" no-op. Recording the signaled Thread object lets
# is_interrupted() detect (and discard) stale flags whose thread is gone.
_interrupt_targets: dict[int, "weakref.ref | None"] = {}
_lock = threading.Lock()


def _find_thread_by_ident(tid: int | None):
    """Return the live Thread object for *tid*, or None."""
    for thread in threading.enumerate():
        if thread.ident == tid:
            return thread
    return None


def set_interrupt(active: bool, thread_id: int | None = None) -> None:
    """Set or clear interrupt for a specific thread.

    Args:
        active: True to signal interrupt, False to clear it.
        thread_id: Target thread ident.  When None, targets the
                   current thread (backward compat for CLI/tests).
    """
    tid = thread_id if thread_id is not None else threading.current_thread().ident
    with _lock:
        if active:
            _interrupted_threads.add(tid)
            target = _find_thread_by_ident(tid)
            # None (no resolvable Thread object) keeps the legacy bare-ident
            # semantics for synthetic idents used by tests/stubs.
            _interrupt_targets[tid] = weakref.ref(target) if target is not None else None
        else:
            _interrupted_threads.discard(tid)
            _interrupt_targets.pop(tid, None)
        _snapshot = set(_interrupted_threads) if _DEBUG_INTERRUPT else None
    if _DEBUG_INTERRUPT:
        logger.info(
            "[interrupt-debug] set_interrupt(active=%s, target_tid=%s) "
            "called_from_tid=%s current_set=%s",
            active, tid, threading.current_thread().ident, _snapshot,
        )


def prune_dead_interrupts() -> int:
    """Discard interrupt flags whose signaled thread has died.

    Returns the number of stale flags removed. Called opportunistically by
    is_interrupted() for the current thread; exposed for long-lived
    gateways (and tests) that want to sweep all stale flags.
    """
    with _lock:
        stale = [
            tid for tid, ref in _interrupt_targets.items()
            if ref is not None and ref() is None
        ]
        for tid in stale:
            _interrupted_threads.discard(tid)
            _interrupt_targets.pop(tid, None)
        return len(stale)


def is_interrupted() -> bool:
    """Check if an interrupt has been requested for the current thread.

    Safe to call from any thread — each thread only sees its own
    interrupt state.  A flag whose signaled thread has died is treated as
    stale (ident reuse) and discarded rather than honored.
    """
    tid = threading.current_thread().ident
    with _lock:
        if tid not in _interrupted_threads:
            return False
        ref = _interrupt_targets.get(tid)
        if ref is not None and ref() is None:
            # The thread that was signaled has exited; its ident may have
            # been reused by this unrelated thread. Never propagate the
            # stale interrupt to it.
            _interrupted_threads.discard(tid)
            _interrupt_targets.pop(tid, None)
            if _DEBUG_INTERRUPT:
                logger.info(
                    "[interrupt-debug] discarded stale interrupt for dead tid=%s (ident reuse guard)",
                    tid,
                )
            return False
        return True


# ---------------------------------------------------------------------------
# Backward-compatible _interrupt_event proxy
# ---------------------------------------------------------------------------
# Some legacy call sites (code_execution_tool, process_registry, tests)
# import _interrupt_event directly and call .is_set() / .set() / .clear().
# This shim maps those calls to the per-thread functions above so existing
# code keeps working while the underlying mechanism is thread-scoped.

class _ThreadAwareEventProxy:
    """Drop-in proxy that maps threading.Event methods to per-thread state."""

    def is_set(self) -> bool:
        return is_interrupted()

    def set(self) -> None:  # noqa: A003
        set_interrupt(True)

    def clear(self) -> None:
        set_interrupt(False)

    def wait(self, timeout: float | None = None) -> bool:
        """Not truly supported — returns current state immediately."""
        return self.is_set()


_interrupt_event = _ThreadAwareEventProxy()
