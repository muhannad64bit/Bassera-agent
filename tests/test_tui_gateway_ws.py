import asyncio
import threading

import pytest
import time

from tui_gateway import server
from tui_gateway import ws as ws_mod


def _run_disconnect(monkeypatch, seed):
    def _fake_finalize(session, end_reason="tui_close"):
        worker = session.get("slash_worker")
        if worker:
            worker.close()

    monkeypatch.setattr(server, "_finalize_session", _fake_finalize, raising=False)

    created = []
    real_transport = ws_mod.WSTransport
    monkeypatch.setattr(
        ws_mod,
        "WSTransport",
        lambda ws, loop, **kw: created.append(real_transport(ws, loop, **kw)) or created[-1],
    )

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, line):
            pass

        async def receive_text(self):
            seed(created[0])
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    asyncio.run(ws_mod.handle_ws(FakeWS()))


def test_ws_disconnect_reaps_flagged_session_and_closes_worker(monkeypatch):
    closed = []

    class FakeWorker:
        def close(self):
            closed.append(True)

    server._sessions.clear()
    try:
        _run_disconnect(
            monkeypatch,
            lambda transport: server._sessions.update(
                flagged={
                    "transport": transport,
                    "close_on_disconnect": True,
                    "slash_worker": FakeWorker(),
                    "session_key": "k",
                }
            ),
        )
        assert "flagged" not in server._sessions
        assert closed == [True]
    finally:
        server._sessions.clear()


def test_ws_disconnect_preserves_reconnectable_session(monkeypatch):
    server._sessions.clear()
    try:
        _run_disconnect(
            monkeypatch,
            lambda transport: server._sessions.update(
                plain={"transport": transport, "close_on_disconnect": False, "session_key": "k"}
            ),
        )
        # The session survives with its OWN bounded buffer (not the shared
        # placeholder, not dropped): events emitted while detached are
        # retained for the next connection.
        buf = server._sessions["plain"]["transport"]
        assert isinstance(buf, server._BufferingTransport), (
            "reconnectable session must hold a buffering transport"
        )
        buf.write({"jsonrpc": "2.0", "method": "event", "params": {"type": "note"}})
        assert len(buf.drain()) == 1
        assert buf.drain() == []
    finally:
        server._sessions.clear()


def test_ws_write_loop_stall_does_not_latch_transport(monkeypatch):
    monkeypatch.setattr(ws_mod, "_WS_WRITE_TIMEOUT_S", 0.05)
    sent = []

    class FakeWS:
        async def send_text(self, line):
            sent.append(line)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        transport = ws_mod.WSTransport(FakeWS(), loop, peer="stall-test")
        loop.call_soon_threadsafe(time.sleep, 0.3)
        assert transport.write({"a": 1}) is True
        assert transport._closed is False

        assert transport.write({"b": 2}) is True
        deadline = time.time() + 2
        while len(sent) < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert len(sent) == 2
        assert transport._closed is False
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()


# ---------------------------------------------------------------------------
# Auth gate + frame size limit (bassera_architecture/tui_websocket_transport.md)
# ---------------------------------------------------------------------------

def _run_auth_probe(scope_query: bytes, token, closed):
    class FakeWS:
        def __init__(self):
            self.scope = {"query_string": scope_query, "headers": []}

        async def accept(self):
            raise AssertionError("auth-rejected handshake must never accept")

        async def send_text(self, line):
            pass

        async def receive_text(self):
            raise AssertionError("auth-rejected handshake must never read frames")

        async def close(self, code=None):
            closed.append(code)

    asyncio.run(ws_mod.handle_ws(FakeWS(), token=token))


def test_ws_bad_token_rejected_before_accept():
    closed = []
    _run_auth_probe(b"token=wrong", "secret123", closed)
    assert closed == [1008], "mismatched token must close with policy code 1008"


def test_ws_missing_token_rejected():
    closed = []
    _run_auth_probe(b"", "secret123", closed)
    assert closed == [1008]


def test_ws_good_token_accepted_and_served(monkeypatch):
    """A matching token proceeds to the normal ready/dispatch flow."""
    sent = []

    class FakeWS:
        def __init__(self):
            self.scope = {"query_string": b"token=secret123", "headers": []}

        async def accept(self):
            sent.append("accepted")

        async def send_text(self, line):
            sent.append(line)

        async def receive_text(self):
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    asyncio.run(ws_mod.handle_ws(FakeWS(), token="secret123"))
    assert "accepted" in sent
    ready = [s for s in sent if isinstance(s, str) and "gateway.ready" in s]
    assert ready, "ready event must be sent after a valid token"


def test_ws_header_token_accepted():
    class FakeWS:
        def __init__(self):
            self.scope = {
                "query_string": b"",
                "headers": [(b"x-bassera-token", b"secret123")],
            }

        async def accept(self):
            pass

        async def send_text(self, line):
            pass

        async def receive_text(self):
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    asyncio.run(ws_mod.handle_ws(FakeWS(), token="secret123"))  # must not raise


def test_ws_oversized_frame_rejected_and_closed():
    """A frame over the limit gets one -32701 error, then the socket closes."""
    events = []

    class FakeWS:
        def __init__(self):
            self.scope = {"query_string": b"", "headers": []}

        async def accept(self):
            events.append("accept")

        async def send_text(self, line):
            events.append(line)

        async def receive_text(self):
            if len([e for e in events if e == "recv"]) == 0:
                events.append("recv")
                return "x" * (ws_mod._MAX_FRAME_BYTES + 1)
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            events.append("close")

    asyncio.run(ws_mod.handle_ws(FakeWS()))
    oversized_errors = [e for e in events if isinstance(e, str) and "-32701" in e]
    assert oversized_errors, "oversized frame must receive a -32701 error"
    assert "close" in events, "connection must close after an oversized frame"


def test_ws_app_factory_enforces_token():
    """The production mount always has a token set."""
    starlette = pytest.importorskip("starlette")
    from tui_gateway.app import create_app

    app = create_app(token=None)
    assert app.state.tui_gateway_token, "app must generate a token when none given"

    app2 = create_app(token="explicit-token")
    assert app2.state.tui_gateway_token == "explicit-token"

    # Routes mounted: /ws websocket + /health
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/ws" in paths and "/health" in paths


def test_ws_app_token_from_env(monkeypatch):
    pytest.importorskip("starlette")
    from tui_gateway import app as app_mod

    monkeypatch.setenv("BASSERA_TUI_GATEWAY_TOKEN", "env-token-42")
    assert app_mod._resolve_token(None) == "env-token-42"
    assert app_mod._resolve_token("explicit") == "explicit"
    monkeypatch.delenv("BASSERA_TUI_GATEWAY_TOKEN")
    generated = app_mod._resolve_token(None)
    assert generated and len(generated) >= 32, "generated tokens must be unguessable"


# ---------------------------------------------------------------------------
# Detached-session event buffering (design doc §6.3) + crash-ref sanitizing
# ---------------------------------------------------------------------------

def test_detached_buffer_is_bounded():
    from tui_gateway.server import _new_detached_transport

    buf = _new_detached_transport()
    for i in range(300):
        buf.write({"i": i})
    frames = buf.drain()
    assert len(frames) == _BufferingTransport_MAX if False else True
    # The buffer keeps only the newest _MAX_FRAMES entries
    assert len(frames) == server._BufferingTransport._MAX_FRAMES
    assert frames[0]["i"] == 300 - server._BufferingTransport._MAX_FRAMES
    # Drain empties it
    assert buf.drain() == []


def test_request_reattaches_session_and_flushes_backlog():
    """A reconnecting client receives the detached-window backlog."""
    from tui_gateway.transport import bind_transport, reset_transport

    server._sessions.clear()
    buf = server._new_detached_transport()
    buf.write({"jsonrpc": "2.0", "method": "event", "params": {"type": "backlog-1"}})
    buf.write({"jsonrpc": "2.0", "method": "event", "params": {"type": "backlog-2"}})
    server._sessions["sess-1"] = {
        "transport": buf,
        "close_on_disconnect": False,
        "session_key": "k",
    }
    sent = []

    class LiveTransport:
        def write(self, obj):
            sent.append(obj)
            return True

        def close(self):
            pass

    rebound_class = None
    try:
        token = bind_transport(LiveTransport())
        # A request for the session rebinds the transport and flushes.
        server.handle_request({
            "jsonrpc": "2.0",
            "id": "r1",
            "method": "session.list",  # any method; reattach runs first
            "params": {"session_id": "sess-1"},
        })
        rebound_class = server._sessions["sess-1"]["transport"].__class__.__name__
        reset_transport(token)
    finally:
        server._sessions.clear()

    flushed = [f for f in sent if f.get("params", {}).get("type", "").startswith("backlog")]
    assert flushed and len(flushed) == 2, "backlog must flush to the reconnecting client"
    assert rebound_class == "LiveTransport", "session must rebind to the live transport"


def test_crash_detail_sanitized_for_remote_peers(caplog):
    """A remote peer gets a ref id, not raw exception internals."""
    import json as _json

    sent = []
    _n = {"calls": 0}

    class FakeWS:
        def __init__(self, peer_host):
            self.client = type("C", (), {"host": peer_host, "port": 5555})()

        async def accept(self):
            sent.append("accept")

        async def send_text(self, line):
            sent.append(line)

        async def receive_text(self):
            if _n["calls"] == 0:
                _n["calls"] += 1
                return '{"jsonrpc": "2.0", "id": "r1", "method": "ping", "params": {}}'
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    # Remote peer: crash detail must NOT include the raw exception
    import tui_gateway.server as srv
    original = srv.dispatch
    srv.dispatch = lambda req: (_ for _ in ()).throw(RuntimeError("SECRET_INTERNALS /etc/passwd"))
    try:
        asyncio.run(ws_mod.handle_ws(FakeWS("203.0.113.9")))
    finally:
        srv.dispatch = original
    remote_errs = [
        _json.loads(s) for s in sent if isinstance(s, str) and "-32000" in s
    ]
    assert remote_errs, "crash must still produce a -32000 response"
    msg = remote_errs[0]["error"]["message"]
    assert "SECRET_INTERNALS" not in msg, "raw internals must not reach a remote peer"
    assert "ref=" in msg


def test_crash_detail_kept_for_loopback_peers():
    """Loopback debugging keeps the raw exception text."""
    import json as _json

    sent = []
    _n = {"calls": 0}

    class FakeWS:
        def __init__(self):
            self.client = type("C", (), {"host": "127.0.0.1", "port": 5555})()

        async def accept(self):
            sent.append("accept")

        async def send_text(self, line):
            sent.append(line)

        async def receive_text(self):
            if _n["calls"] == 0:
                _n["calls"] += 1
                return '{"jsonrpc": "2.0", "id": "r1", "method": "ping", "params": {}}'
            raise ws_mod._WebSocketDisconnect()

        async def close(self):
            pass

    import tui_gateway.server as srv
    original = srv.dispatch
    srv.dispatch = lambda req: (_ for _ in ()).throw(RuntimeError("kaboom-local"))
    try:
        asyncio.run(ws_mod.handle_ws(FakeWS()))
    finally:
        srv.dispatch = original
    errs = [_json.loads(s) for s in sent if isinstance(s, str) and "-32000" in s]
    assert errs and "kaboom-local" in errs[0]["error"]["message"]
