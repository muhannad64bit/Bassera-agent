# Performance guide

What Bassera Agent does for speed and cost, where the knobs are, and
what was measured. Each lever names its config and where it lives.

## Latency and cost per conversation

| Lever | Status | Where |
|---|---|---|
| **Anthropic prompt caching** (`system_and_3` strategy: system prompt + last 3 messages breakpoint) | On by default for Anthropic-family models | `agent/prompt_caching.py`, wired in `run_agent.py` (`use_prompt_caching`) |
| **Keep-alive HTTP** for provider clients — no TCP+TLS handshake per turn | On | `AIAgent._build_keepalive_http_client()` in `run_agent.py` |
| **Streaming-first responses** (TTFT) | On | `run_agent.py` streaming paths |
| **Per-provider request timeouts** | Configurable per provider/model | `request_timeout_seconds` in config; `get_provider_request_timeout()` |
| **Mid-stream retry** with backoff on transport failure / truncated streams | On | `max_stream_retries` machinery in `run_agent.py` |
| **Context compression** for long conversations instead of hard truncation | On | `agent/context_compressor.py` |
| **Auxiliary fast-path client** (cheap model for approvals/summaries) with cross-loop cache isolation | On | `agent/auxiliary_client.py` |

## Tool execution throughput

| Lever | Status | Where |
|---|---|---|
| **Parallel tool execution** for safe batches: allowlist + path-overlap analysis (`_should_parallelize_tool_batch`) — never parallelizes side-effectful or path-conflicting calls | On | `run_agent.py`, pool bounded by `_MAX_TOOL_WORKERS` |
| **Process-group cleanup on interrupt** (no orphaned tool processes) | On | `tools/environments/*` |

## Event loop (gateway / TUI throughput)

| Lever | Status | Where |
|---|---|---|
| **uvloop** — auto-enabled when installed, POSIX only | NEW (2026-09) | `tools/event_loop_perf.py`; config `performance.event_loop: auto\|uvloop\|default`, env `BASSERA_EVENT_LOOP`; install via `bassera-agent[perf]` |
| uvicorn loop auto-selection for the TUI WS gateway | NEW | `tui_gateway/app.py` (`loop="auto"`) |

Measured on this machine (Apple Silicon, Python 3.12, uvloop 0.22),
gateway-like load — 500 tasks x 200 awaits, and 2,000 TCP round-trips:

```
                default loop    uvloop
task churn      72-73 ms       40-41 ms   (1.77x)
TCP round-trips 133-146 ms     63-67 ms   (2.1x)
```

## CLI responsiveness

| Lever | Status | Where |
|---|---|---|
| `--version` cache-only path (was ~10 s worst case) | On (0.18 s measured) | `cmd_version`, `bassera_cli/banner.py` update-check cache |
| Launch-time import budget | ~42 ms for `bassera_cli.main` | measured with `-X importtime` |
| venv-aware launcher (no accidental 3.9 interpreter) | On | `./bassera` |

## Storage

| Lever | Status | Where |
|---|---|---|
| SQLite WAL + periodic checkpointing for session state (concurrent readers, one writer) | On | `bassera_state.py` |
| Atomic gateway state writes (no torn reads under crash) | On | `gateway/status.py::_atomic_write_text` |
| Machine-local gateway locks (fast, no DB round-trip) | On | `gateway/status.py` scoped locks |
| Skills prompt snapshot caching | On | `agent/prompt_builder.py` (`.skills_prompt_snapshot.json`) |

## What we deliberately did NOT add

- **Response caching of model outputs** — wrong for an agent whose inputs
  are live filesystems and messages; staleness costs more than latency.
- **orjson et al.** in the RPC path — measured as noise next to loop and
  network costs.
- **HTTP/2 multiplexing** on provider clients — provider-side support is
  uneven; keep-alive already removes the handshake cost.
