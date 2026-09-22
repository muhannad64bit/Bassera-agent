"""Transport abstraction for the TUI gateway JSON-RPC server."""

from __future__ import annotations

import contextvars
import errno
import json
import logging
import os
import threading
from typing import Any, Callable, Optional, Protocol, runtime_checkable

_PEER_GONE_ERRNOS = frozenset(
    {
        errno.EPIPE,
        errno.ECONNRESET,
        errno.EBADF,
        errno.ESHUTDOWN,
        getattr(errno, "WSAECONNRESET", -1),
        getattr(errno, "WSAESHUTDOWN", -1),
    }
    - {-1}
)

logger = logging.getLogger(__name__)

_DISABLE_FLUSH = (os.environ.get("HERMES_TUI_GATEWAY_NO_FLUSH", "") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@runtime_checkable
class Transport(Protocol):
    def write(self, obj: dict) -> bool:
        """Emit one JSON frame. Return False when the peer is gone."""

    def close(self) -> None:
        """Release owned resources."""


_current_transport: contextvars.ContextVar[Optional[Transport]] = contextvars.ContextVar(
    "wafi_gateway_transport",
    default=None,
)


def current_transport() -> Optional[Transport]:
    return _current_transport.get()


def bind_transport(transport: Optional[Transport]):
    return _current_transport.set(transport)


def reset_transport(token) -> None:
    _current_transport.reset(token)


class StdioTransport:
    __slots__ = ("_stream_getter", "_lock")

    def __init__(self, stream_getter: Callable[[], Any], lock: threading.Lock) -> None:
        self._stream_getter = stream_getter
        self._lock = lock

    def write(self, obj: dict) -> bool:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with self._lock:
            stream = self._stream_getter()
            try:
                stream.write(line)
            except BrokenPipeError:
                return False
            except ValueError as exc:
                if isinstance(exc, UnicodeEncodeError) or "closed file" not in str(exc):
                    raise
                return False
            except OSError as exc:
                if exc.errno not in _PEER_GONE_ERRNOS:
                    raise
                logger.debug("stdio transport write peer gone: %s", exc)
                return False

            if not _DISABLE_FLUSH:
                try:
                    stream.flush()
                except BrokenPipeError:
                    return False
                except ValueError as exc:
                    if isinstance(exc, UnicodeEncodeError) or "closed file" not in str(exc):
                        raise
                    return False
                except OSError as exc:
                    if exc.errno not in _PEER_GONE_ERRNOS:
                        raise
                    logger.debug("stdio transport flush peer gone: %s", exc)
                    return False
        return True

    def close(self) -> None:
        return None


class TeeTransport:
    __slots__ = ("_primary", "_secondaries")

    def __init__(self, primary: Transport, *secondaries: Transport) -> None:
        self._primary = primary
        self._secondaries = secondaries

    def write(self, obj: dict) -> bool:
        ok = self._primary.write(obj)
        for secondary in self._secondaries:
            try:
                secondary.write(obj)
            except Exception:
                pass
        return ok

    def close(self) -> None:
        try:
            self._primary.close()
        finally:
            for secondary in self._secondaries:
                try:
                    secondary.close()
                except Exception:
                    pass
