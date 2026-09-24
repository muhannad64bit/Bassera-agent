"""ASGI application that mounts the TUI gateway WebSocket transport.

This is the production mount point for ``tui_gateway.ws.handle_ws``
(previously implemented and tested but reachable by no route — see
bassera_architecture/tui_websocket_transport.md §5).

Security model (§6.1): the WebSocket is ALWAYS authenticated. The
per-launch session token comes from, in order:

1. the ``token`` argument to :func:`create_app` (programmatic launchers);
2. the ``BASSERA_TUI_GATEWAY_TOKEN`` env var (scripts);
3. an ephemeral token generated at startup and printed to stderr
   together with the connection URL.

The default bind is ``127.0.0.1`` — the TUI gateway is a local
interface, not a network service. Bind to another host only behind
additional transport-layer protection.
"""

from __future__ import annotations

import secrets
import sys
from typing import Any, Optional


def _resolve_token(explicit: Optional[str] = None) -> str:
    """Return the session token to enforce, generating one if needed."""
    import os

    if explicit:
        return explicit
    env_token = os.environ.get("BASSERA_TUI_GATEWAY_TOKEN", "").strip()
    if env_token:
        return env_token
    return secrets.token_urlsafe(32)


def create_app(token: Optional[str] = None) -> Any:
    """Build the Starlette app with the authenticated WS route."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route, WebSocketRoute

    from tui_gateway.ws import handle_ws

    effective_token = _resolve_token(token)

    async def _ws_endpoint(websocket: Any) -> None:
        await handle_ws(websocket, token=effective_token)

    async def _health(_request: Any) -> Any:
        return JSONResponse({"ok": True, "service": "bassera-tui-gateway"})

    app = Starlette(
        routes=[
            Route("/health", _health),
            WebSocketRoute("/ws", _ws_endpoint),
        ],
    )
    app.state.tui_gateway_token = effective_token
    return app


def run_ws_gateway(
    host: str = "127.0.0.1",
    port: Optional[int] = None,
    token: Optional[str] = None,
    *,
    print_url_to: Any = None,
) -> None:
    """Launch the WS gateway (blocking).

    Picks a free port when *port* is None, prints the authenticated
    connection URL (``ws://host:port/ws?token=...``) to *print_url_to*
    (default: stderr) so interactive clients can connect, then serves
    until interrupted.
    """
    import socket

    import uvicorn

    app = create_app(token)

    if port is None or port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            port = sock.getsockname()[1]

    out = print_url_to if print_url_to is not None else sys.stderr
    print(f"ws://{host}:{port}/ws?token={app.state.tui_gateway_token}", file=out)

    uvicorn.run(app, host=host, port=port, log_level="warning")
