"""WebSocket transport for the tui_gateway JSON-RPC server."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import socket
from typing import Any

from tui_gateway import server
from tui_gateway.transport import bind_transport, reset_transport

_log = logging.getLogger(__name__)

_WS_WRITE_TIMEOUT_S = 10.0
_WS_LOG_PAYLOAD_PREVIEW = 240

try:
    from starlette.websockets import WebSocketDisconnect as _WebSocketDisconnect
except ImportError:  # pragma: no cover
    _WebSocketDisconnect = Exception  # type: ignore[assignment]


class WSTransport:
    def __init__(self, ws: Any, loop: asyncio.AbstractEventLoop, *, peer: str = "unknown") -> None:
        self._ws = ws
        self._loop = loop
        self._peer = peer
        self._closed = False

    def write(self, obj: dict) -> bool:
        if self._closed:
            return False
        line = json.dumps(obj, ensure_ascii=False)
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False

        if on_loop:
            self._loop.create_task(self._safe_send(line))
            return True

        try:
            fut = asyncio.run_coroutine_threadsafe(self._safe_send(line), self._loop)
            fut.result(timeout=_WS_WRITE_TIMEOUT_S)
            return not self._closed
        except concurrent.futures.TimeoutError:
            _log.warning(
                "ws write slow (loop stalled >%ss) peer=%s; frame left in flight",
                _WS_WRITE_TIMEOUT_S,
                self._peer,
            )
            return not self._closed
        except Exception as exc:
            self._closed = True
            _log.warning("ws write failed peer=%s error=%s", self._peer, exc)
            return False

    async def write_async(self, obj: dict) -> bool:
        if self._closed:
            return False
        await self._safe_send(json.dumps(obj, ensure_ascii=False))
        return not self._closed

    async def _safe_send(self, line: str) -> None:
        try:
            await self._ws.send_text(line)
        except Exception as exc:
            self._closed = True
            _log.warning("ws send failed peer=%s error=%s", self._peer, exc)

    def close(self) -> None:
        self._closed = True


def _ws_peer_label(ws: Any) -> str:
    client = getattr(ws, "client", None)
    if client is None:
        return "unknown"
    host = getattr(client, "host", None) or "unknown"
    port = getattr(client, "port", None)
    return f"{host}:{port}" if port is not None else host


def _disable_nagle(ws: Any) -> None:
    try:
        scope = getattr(ws, "scope", None) or {}
        transport = (scope.get("extensions") or {}).get("transport") or getattr(ws, "transport", None)
        sock = transport.get_extra_info("socket") if transport is not None else None
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass


# Maximum accepted JSON-RPC frame. A client can send an arbitrarily large
# line otherwise; anything bigger than this gets a -32701 error and the
# connection is closed (tui_websocket_transport.md §6.4).
_MAX_FRAME_BYTES = 1024 * 1024

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "[::1]"})


def _peer_is_loopback(peer: str) -> bool:
    """True for local peers — the only ones allowed crash detail.

    Fail-closed: a peer whose address cannot be determined ("unknown")
    is treated as REMOTE, not loopback — degraded peer info must never
    upgrade a remote connection into receiving raw crash internals.
    """
    host = peer.rsplit(":", 1)[0] if ":" in peer else peer
    return host in _LOOPBACK_HOSTS


def _crash_ref(exc: BaseException) -> str:
    """Stable short reference for a crash, correlated with the server log."""
    import hashlib

    return hashlib.sha256(repr(exc).encode("utf-8", "replace")).hexdigest()[:8]


def _extract_ws_token(ws: Any) -> str | None:
    """Pull the session token from the handshake (query param or header)."""
    scope = getattr(ws, "scope", None) or {}
    query = (scope.get("query_string") or b"").decode("utf-8", "replace")
    for part in query.split("&"):
        if part.startswith("token="):
            from urllib.parse import unquote
            return unquote(part[len("token="):])
    headers = scope.get("headers") or {}
    try:
        for name, value in headers.items() if isinstance(headers, dict) else headers:
            if bytes(name).lower() == b"x-bassera-token":
                return bytes(value).decode("utf-8", "replace")
    except Exception:
        pass
    return None


async def handle_ws(ws: Any, *, token: str | None = None) -> None:
    """Serve one WebSocket client.

    ``token`` enables the auth gate (tui_websocket_transport.md §6.1): a
    per-launch session token that the client must present in the
    handshake (``?token=...`` or the ``X-Bassera-Token`` header). Without
    a token the handler is unauthenticated — acceptable ONLY for
    process-internal use (tests) or when the socket is otherwise
    protected; the ASGI app in tui_gateway/app.py always enforces one.
    A mismatched token rejects the handshake before any JSON-RPC traffic.
    """
    if token is not None:
        presented = _extract_ws_token(ws)
        if presented != token:
            try:
                await ws.close(code=1008)  # policy violation
            except Exception:
                pass
            _log.info("ws auth rejected peer=%s (bad or missing token)", _ws_peer_label(ws))
            return

    peer = _ws_peer_label(ws)
    transport: WSTransport | None = None
    messages = 0
    parse_errors = 0
    dispatch_crashes = 0
    send_failures = 0
    disconnect_reason = "not_connected"

    try:
        await ws.accept()
        disconnect_reason = "connected"
        _disable_nagle(ws)
        transport = WSTransport(ws, asyncio.get_running_loop(), peer=peer)

        ready_ok = await transport.write_async(
            {
                "jsonrpc": "2.0",
                "method": "event",
                "params": {"type": "gateway.ready", "payload": {"skin": server.resolve_skin()}},
            }
        )
        if not ready_ok:
            send_failures += 1
            disconnect_reason = "ready_send_failed"
            return

        while True:
            try:
                raw = await ws.receive_text()
            except _WebSocketDisconnect as exc:
                disconnect_reason = (
                    "client_disconnect("
                    f"code={getattr(exc, 'code', None)},"
                    f"reason={getattr(exc, 'reason', None)})"
                )
                break
            except Exception:
                disconnect_reason = "receive_failed"
                _log.exception("ws receive failed peer=%s", peer)
                break

            messages += 1

            if len(raw) > _MAX_FRAME_BYTES:
                # Oversized frame: answer once with a JSON-RPC error and
                # close — a client must not be able to buffer unbounded
                # input (tui_websocket_transport.md §6.4).
                try:
                    await transport.write_async(
                        {
                            "jsonrpc": "2.0",
                            "error": {"code": -32701, "message": "request too large"},
                            "id": None,
                        }
                    )
                except Exception:
                    pass
                disconnect_reason = "frame_too_large"
                break

            try:
                req = json.loads(raw.strip())
            except json.JSONDecodeError:
                parse_errors += 1
                ok = await transport.write_async(
                    {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}, "id": None}
                )
                if not ok:
                    send_failures += 1
                    disconnect_reason = "parse_error_send_failed"
                    break
                continue

            token = bind_transport(transport)
            try:
                try:
                    resp = server.dispatch(req)
                except Exception as exc:
                    dispatch_crashes += 1
                    # The full traceback always goes to the server log; the
                    # peer only sees the raw exception text when it is a
                    # loopback connection (local debugging). A remote peer
                    # gets a stable reference id instead — raw internals
                    # must not leak to the network (design doc §6.2).
                    _log.warning(
                        "ws dispatch crashed peer=%s ref=%s",
                        peer,
                        _crash_ref(exc),
                        exc_info=True,
                    )
                    if _peer_is_loopback(peer):
                        detail = f"handler error: {exc}"
                    else:
                        detail = f"handler error: ref={_crash_ref(exc)}"
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32000, "message": detail},
                    }
                if resp is not None:
                    ok = await transport.write_async(resp)
                    if not ok:
                        send_failures += 1
                        disconnect_reason = "response_send_failed"
                        break
            finally:
                reset_transport(token)
    finally:
        if transport is not None:
            transport.close()
            _detach_transport_sessions(transport)
        try:
            await ws.close()
        except Exception:
            pass
        _log.info(
            "ws closed peer=%s reason=%s messages=%s parse_errors=%s dispatch_crashes=%s send_failures=%s",
            peer,
            disconnect_reason[:_WS_LOG_PAYLOAD_PREVIEW],
            messages,
            parse_errors,
            dispatch_crashes,
            send_failures,
        )


def _detach_transport_sessions(transport: WSTransport) -> None:
    for sid, session in list(server._sessions.items()):
        if session.get("transport") is not transport:
            continue
        if session.get("close_on_disconnect"):
            _finalize = getattr(server, "_finalize_session", None)
            if callable(_finalize):
                _finalize(session, end_reason="ws_disconnect")
            else:
                worker = session.get("slash_worker")
                if worker:
                    worker.close()
            server._sessions.pop(sid, None)
        else:
            # Detached (reconnectable) session: give it its OWN bounded
            # buffer so events emitted while no socket is attached are
            # retained and flushed to the next connection that talks to
            # the session (server._reattach_session_transport), instead of
            # being silently dropped.
            session["transport"] = server._new_detached_transport()
