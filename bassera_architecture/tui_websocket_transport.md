# TUI Gateway WebSocket transport — design doc

Status: **documents existing upstream WIP**. `tui_gateway/ws.py` and
`tui_gateway/transport.py` were carried from the fork tree; this doc was
written (per the production-hardening plan) *before* any further
development on them.

## 1. Purpose

The TUI gateway serves JSON-RPC (methods in `tui_gateway/server.py`,
dispatched by `server.dispatch`). Transport is abstracted by a
runtime-checkable `Transport` protocol (`tui_gateway/transport.py`):

- `StdioTransport` — newline-delimited JSON over a file stream. The
  production default.
- `TeeTransport` — fans frames out to a primary plus secondaries;
  the primary's return value wins, secondary failures are swallowed.
- `WSTransport` (`ws.py`, this doc) — one JSON object per WebSocket
  text frame.

The goal of the WS transport is to let a browser-based (or any
WebSocket) client speak the exact same JSON-RPC protocol as the stdio
TUI, with no protocol divergence: frames in, frames out, `dispatch`
shared. The server core does not know which transport a session is on.

## 2. Wire protocol

Same envelope as stdio: `{"jsonrpc": "2.0", ...}` per frame.

- On connect the server sends an unsolicited ready event before
  reading anything: `{"jsonrpc":"2.0","method":"event","params":{
  "type":"gateway.ready","payload":{"skin": <resolved skin>}}}`.
- A malformed (non-JSON) frame yields a single
  `{"jsonrpc":"2.0","error":{"code":-32700,"message":"parse error"},"id":null}`
  and the connection stays open — the server does not kill the
  session on parse errors.
- A crashing handler yields
  `{"jsonrpc":"2.0","id":<req id>,"error":{"code":-32000,"message":"handler error: ..."}}`
  — the exception text is embedded; see §6 on information disclosure.
- `TCP_NODELAY` is forced on the underlying socket (best effort) so
  interactive frames are not Nagle-buffered.

## 3. Transport context and thread-safety

`transport.py` keeps the current transport in a contextvar
(`current_transport()` / `bind_transport()` / `reset_transport()`).
`handle_ws` binds the `WSTransport` around each `dispatch` call and
resets it in a `finally`, so background threads spawned by a request
inherit the correct transport for async events, and a stale transport
never leaks into the next request's context.

`WSTransport.write` is callable from any thread:

- On the loop's thread: schedules `_safe_send` as a task (fire and
  forget).
- Off-loop: `run_coroutine_threadsafe` with a **10 s timeout**
  (`_WS_WRITE_TIMEOUT_S`). On timeout the frame is *left in flight*
  (not dropped from the peer's perspective) and the transport stays
  open — the assumption is a stalled-but-recovering loop. On any other
  exception the transport is marked closed and `write` returns `False`,
  which tells emitters to stop.

`write_async` is the on-loop variant used by the handler itself.

`BASSERA_TUI_GATEWAY_NO_FLUSH` (in transport.py) disables stream
flushing for stdio — a performance knob for hosts where flush is
pathologically slow; it does not affect WS.

## 4. Session lifecycle and detach semantics

Server sessions may record their owning transport. On disconnect,
`_detach_transport_sessions` walks `server._sessions`:

- Sessions with `close_on_disconnect` set are finalized
  (`_finalize_session(end_reason="ws_disconnect")`, falling back to
  closing the session's `slash_worker`) and removed.
- Sessions without it are **re-attached to a detached placeholder
  transport** (`server._detached_ws_transport`), i.e. the session
  survives the socket and can be resumed by a later connection.
  Emits to a detached session go to the placeholder, which is where
  they must be buffered or dropped — see open questions.

The handler's `finally` block always runs: transport closed, sessions
detached, socket closed, and one summary log line with counters
(messages / parse_errors / dispatch_crashes / send_failures and the
disconnect reason) capped to a 240-char preview.

## 5. Current integration state

**MOUNTED (2026-09).** The transport is now reachable in production via
`tui_gateway/app.py` (`bassera tui-gateway`): a Starlette app exposing
`/ws` (the authenticated transport) and `/health`, bound to 127.0.0.1
by default. The auth gate and frame size limit from §6 are implemented
and live-verified; `handle_ws(ws, token=None)` without a token remains
available for process-internal use only.

- `handle_ws` is implemented and unit-tested
  (`tests/test_tui_gateway_ws.py`) but **no production route mounts
  it** — grep finds no caller outside tests. Mounting is the missing
  step: an ASGI app (starlette/uvicorn) route that maps a WebSocket
  to `handle_ws`, plus launch wiring (port selection, loop startup).
- `starlette` is an optional import; without it, `WebSocketDisconnect`
  degrades to bare `Exception` (still functional, less precise
  disconnect reasons).
- The carried WIP also included launch-path changes (see the
  `feat(tui)` commit) — this doc covers the transport only.

## 6. Open questions / gaps (must resolve before mounting)

1. ~~No authentication on the WS handler~~ **CLOSED**: per-launch session
   token (query param or `X-Bassera-Token` header) checked BEFORE the
   handshake is accepted; mismatch closes with 1008. The mounted app
   always enforces a token (explicit, `BASSERA_TUI_GATEWAY_TOKEN`, or
   generated at startup).
2. **Error text disclosure**: `-32000` embeds raw exception text into
   frames sent to the peer. For a localhost TUI this is fine; for a
   remotely reachable socket it leaks internals.
3. **Detached-session buffering**: what happens to events emitted
   between disconnect and resume is unspecified in the placeholder
   transport.
4. ~~No frame size limit~~ **CLOSED**: frames over 1 MiB receive one
   `-32701` ("request too large") error and the connection closes.
5. **Backpressure**: off-loop `write` blocks the calling thread up to
   10 s per frame. Emitters in hot loops should treat `False` as
   stop-signaling (they do today), but a slow client can still stall
   one thread per frame for up to 10 s.

## 7. Next steps, in order

1. Decide the auth model (§6.1) — blocking.
2. Add a frame size limit + a `-32701`-style "request too large" error.
3. Mount `handle_ws` behind that auth on an ASGI route; add an e2e
   test that starts the server and drives a real WebSocket.
4. Specify detached-session event buffering.
5. Only then: further feature work on the WS path.

## 8. Tests that pin current behavior

- `tests/test_tui_gateway_ws.py` — handler lifecycle, ready event,
  parse-error recovery, dispatch-crash response, disconnect counters.
- `tests/test_tui_gateway_server.py` and `tests/tui_gateway/` —
  dispatch semantics shared by both transports.
