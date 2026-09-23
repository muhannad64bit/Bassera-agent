# Logging and audit trail

Bassera Agent uses Python's `logging` module throughout, configured by
`wafi_logging.setup_logging()`. This document defines the conventions a
contributor must follow and the exact audit-log format.

## 1. Where logs live

| Destination | Contents | Handler |
|---|---|---|
| `<home>/logs/agent.log` | main agent runtime log | `RotatingFileHandler` |
| `<home>/logs/gateway.log` | messaging gateway log | `RotatingFileHandler` |
| `<home>/logs/audit.log` | approval-decision audit trail (JSON lines) | plain append |
| stderr | console copy; `setup_verbose_logging()` swaps in a timestamped `HH:MM:SS` format | `StreamHandler` |

`<home>` is the Bassera home (`~/.wafi` by default, `BASSERA_HOME` /
`HERMES_HOME` overrides). All file handlers use rotation; handler classes
in `wafi_logging.py` (`_ManagedRotatingFileHandler`) enforce permissions
in managed mode.

## 2. Format and levels

Record format: `%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s`
(verbose: `asctime - name - levelname[session] - message`).

Every record passes through `RedactingFormatter` — secrets matched by
`agent/redact.py` patterns are masked **before** they reach any handler.
A log line must therefore never be assumed safe to contain a raw
credential, and code must never attempt to "pre-redact" by hand: log the
real value and let the formatter mask it (double redaction corrupts the
message).

Session context: `set_session_context(session_id)` /
`clear_session_context()` install a record factory that tags every
record with the active session, so interleaved gateway sessions can be
untangled when reading a shared log file.

Level conventions:

- `DEBUG` — internal state, per-frame transport traffic, cache hits.
  Never enabled by default.
- `INFO` — lifecycle events a support engineer would want in a bug
  report: startup, connection open/close with counters, session
  create/finalize, skill load.
- `WARNING` — degraded-but-operating: retries, slow writes, peer gone,
  fail-open paths (e.g. tirith runtime missing). Every accepted
  residual risk that trips at runtime logs at WARNING, not silently.
- `ERROR` — an operation failed and a user-visible consequence
  follows.
- Never use `print()` in library code; never log at INFO in a
  per-frame/per-token hot loop.

## 3. Audit trail (`<home>/logs/audit.log`)

One JSON object per line, appended by
`tools/approval._audit_approval_decision` for **every** decision made by
`check_all_command_guards` where a dangerous-command finding existed —
approved or denied, interactive or unattended. Benign commands are not
recorded. Auditing is best-effort: a failure to audit must never break
the approval flow (errors log a WARNING and swallow).

Schema:

| Field | Type | Meaning |
|---|---|---|
| `ts` | str | UTC `YYYY-MM-DDTHH:MM:SS` |
| `decision` | str | `"approved"` or `"denied"` |
| `session` | str/null | current session key (`get_current_session_key`) |
| `env_type` | str | approval environment: `cli`, `gateway`, `cron`, `yolo`, ... |
| `pattern_key` | str/null | dangerous-command pattern that fired |
| `description` | str/null | human explanation of the finding |
| `smart` | bool | decision came from smart approvals |
| `command` | str | the command, **redacted** via `agent/redact.redact_sensitive_text`, truncated to 2000 chars |
| `unchecked` | bool | only present when the command ran in an unattended context (yolo/cron auto-allow) without interactive detection — dangerous commands that ran with no human in the loop are always visible in the trail |

Reading the trail:

```bash
tail -n 200 ~/.wafi/logs/audit.log | jq .
jq 'select(.decision=="approved" and .unchecked==true)' ~/.wafi/logs/audit.log
```

The audit trail is a security artifact: tests that touch approval code
must not assert against a developer's real home (the suite is
hermetic — see CONTRIBUTING), and new decision points must extend
`_audit_approval_decision` rather than writing a parallel log.

## 4. Component filters

`_ComponentFilter` (in `wafi_logging.py`) routes records by logger-name
prefix to per-component files (agent vs gateway). New top-level
components that deserve their own log file register a filter there,
rather than writing bespoke log setup code.
