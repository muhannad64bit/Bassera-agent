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


async def handle_ws(ws: Any) -> None:
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
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32000, "message": f"handler error: {exc}"},
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
            session["transport"] = server._detached_ws_transport
