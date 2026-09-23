# Bassera Agent — Threat Model

Status: **living document**. Every entry names the enforcement point, its
status, and the tests that pin it. Update this file in the same PR as any
change to a security-relevant surface.

## 1. Trust boundaries

```
                         ┌──────────────────────────────────────┐
   Untrusted             │  Bassera Agent                      │
   ┌──────────┐          │                                      │
   │ LLM      │──────────┤  cognition proposes, never         │
   │ output   │          │  authorizes (ADR-001/003)           │
   └──────────┘          │                                      │
   ┌──────────┐          │  ┌──────────────┐   ┌────────────┐  │
   │ messaging│──────────┼─►│ pairing /    │──►│ approval   │  │
   │ users    │          │  │ allowlist    │   │ system     │  │
   └──────────┘          │  └──────────────┘   └─────┬──────┘  │
   ┌──────────┐          │                           ▼         │
   │ web      │──────────┼─►  URL safety ──► tool execution      │
   │ content  │          │                           │         │
   └──────────┘          │                           ▼         │
   ┌──────────┐          │                    file write deny   │
   │ skills / │──────────┼─►  skills guard ──► filesystem        │
   │ MCP svrs │          │                                      │
   └──────────┘          └──────────────────────────────────────┘
```

- **LLM output is untrusted** (prompt injection): every proposal it makes
  passes policy before execution (ADR-003).
- **Messaging users are untrusted** until paired/allowlisted.
- **Web content is untrusted**: never injected into the system prompt
  unfiltered; fetched URLs are policy-checked.
- **The agent home (`~/.wafi`) is trusted state** — writes into it are
  guarded; its credential file (`.env`) is doubly protected.

## 2. Sensitive entry points

### 2.1 Environment variables (credential ingestion)

| Entry | Protection | Status | Pinned by |
|---|---|---|---|
| Provider API keys (`.env`, shell) | `tools/credential_files.py` restrictive perms; env-scan on test suite; never echoed by tools | Protected | `tests/tools/test_credential_files.py` |
| `HERMES_HOME` / `BASSERA_HOME` | path indirection; per-profile isolation | Protected | `tests/test_bassera_home_alias.py` |
| `-p <value>` argv (profile pre-parser) | shape-check before claim — foreign programs' `-p` flags (pytest) can no longer hijack the home at import | Protected | `stability` commit 6e6125c5 (regression: import smoke under `-p no:cacheprovider`) |
| Secrets in tool results/logs | `agent/redact.py` redaction; audit log redacts before write | Protected | `tests/agent/test_redact.py`, `tests/tools/test_approval_audit.py` (secret-in-command test) |
| Subprocess env leakage | HOME isolation for subprocesses; blocklist of credential vars for `execute_code` children | Partial | `tests/tools/test_local_env_blocklist.py`, `tests/tools/test_code_execution.py` — blocklist is pattern-based; new exotic credential var names can slip through |

### 2.2 File writes

| Entry | Protection | Status | Pinned by |
|---|---|---|---|
| Credential `.env` (all home spellings) | approval regex (`$HERMES_HOME/.env`, tee/redirect/cp/mv/install/rsync/sed, quotes incl. backtick) **and** `_is_write_denied` check-time resolution for both current+default home | Protected | `tests/tools/test_approval.py`, `test_approval_fuzz.py`, `test_write_deny.py::TestCredentialEnvDeniedUnderAnyHome` |
| `~/.ssh`, `/etc`, block devices (writes via shell) | approval patterns for redirect/tee/cp/mv/sed into `_SENSITIVE_WRITE_TARGET` | Protected | `test_approval_fuzz.py` (copy-tools matrix) |
| Same paths via **file tools** (not shell) | static deny list `WRITE_DENIED_PATHS` + prefixes, checked at write time | Protected | `tests/tools/test_write_deny.py`, `test_file_write_safety.py` |
| macOS user temp dir | exempted from sensitive-path prefix (realpath TMPDIR under `/private/var/`) — the exemption is root-scoped to OS temp dirs only | Protected | `tests/tools/test_file_staleness.py` |
| Memory files (prompt-injected content) | `_scan_memory_content` blocks injection/exfiltration patterns + invisible Unicode before an entry is accepted | Protected | memory tool tests (security-scan paths in `test_memory_tool.py`) |
| Workspace escape (skills, MCP downloads) | `tools/path_security.py` `validate_within_dir` + traversal checks | Partial | applied at the listed call sites (skill manager, cron, credential files); any NEW download path must adopt it — no central choke point | 

### 2.3 Subprocess spawning (terminal)

| Entry | Protection | Status | Pinned by |
|---|---|---|---|
| Dangerous shell commands | `detect_dangerous_command` patterns; fuzz-tested invariants (never-raises, case-insensitive, ReDoS bound) | Protected | `test_approval_fuzz.py` |
| Credential-file writes via cp/mv/rsync/sed (siblings of the original `$HERMES_HOME` hole) | generalized sensitive-target patterns | Protected | `test_approval_fuzz.py` copy-tools matrix (counterfactually failing pre-fix) |
| Semantic command analysis | tirith scanner (auto-install, fail-open config, 24h negative cache) | Partial | `tests/tools/test_tirith_security.py` — fail-open on missing runtime is deliberate UX; a forced install with no network = no semantic layer |
| Approval decision audit | every decision → `<home>/logs/audit.log`, JSON, redacted, includes unattended `"unchecked": true` runs | Protected | `tests/tools/test_approval_audit.py` |
| Interrupt → orphaned process groups | kill on interrupt incl. the spawn→poll race window | Protected | `tests/tools/test_local_interrupt_cleanup.py` |
| Self-termination (agent killing its own gateway/cli) | name-based + pgrep-expansion patterns | Protected | `test_approval.py` self-termination class |

### 2.4 Network calls

| Entry | Protection | Status | Pinned by |
|---|---|---|---|
| SSRF (private/loopback/link-local/metadata) | `tools/url_safety.py` blocks private ranges, `169.254.169.254`, localhost aliases; DNS-rebinding note: re-resolution bounded | Protected | `tests/tools/test_browser_ssrf_local.py`, `url_safety` tests |
| Website blocklist policy | `tools/website_policy.py` config-driven allow/block with metadata | Protected | `tests/tools/test_website_policy.py` |
| Browser automation exfiltration | browser tool blocks secret-shaped strings in console/page payloads | Protected | `tests/tools/test_browser_secret_exfil.py` |
| Web tool downloads | file-type/mime sniffing on fetched images (vision path) | Partial | `tools/vision_tools.py` `_detect_image_mime_type`; other download surfaces rely on extension checks |

### 2.5 Skills, MCP, plugins

| Entry | Protection | Status | Pinned by |
|---|---|---|---|
| Skill installation (prompt injection in skill files) | `tools/skills_guard.py` scanning on install | Protected | `tests/tools/test_skills_guard.py` |
| Cron job prompts (injected content into unattended runs) | cron approval mode (default deny dangerous in cron); prompt-injection scan | Protected | `tests/tools/test_cron_prompt_injection.py`, `test_cron_approval_mode.py` |
| MCP servers | OAuth manager with per-server token storage; server responses treated as untrusted data | Partial | `tests/tools/test_mcp_tool*.py` — surface is large; trust depends on each server's own posture |
| SQL in user text reaching local DBs | parameterized queries; literal-SQL tests | Protected | `tests/test_sql_injection.py` |

### 2.6 Messaging gateway

| Entry | Protection | Status | Pinned by |
|---|---|---|---|
| Unknown users | pairing codes: rate-limited, lockout after failures, persisted with 0600 perms; per-platform allowlists | Protected | `tests/gateway/test_pairing.py` |
| Allow-all misconfiguration startup warning | explicit warning when no allowlist and open access | Protected | `tests/gateway/test_allowlist_startup_check.py` |
| Weak credential guard (obviously-invalid tokens) | startup rejection of placeholder/short tokens | Protected | `tests/gateway/test_weak_credential_guard.py` |
| Session-key confusion (approval scoped to wrong session) | per-thread approval state + contextvar hygiene (stale ident guard, `_UNSET` sentinel reset) | Protected | `tests/tools/test_interrupt.py`, `test_command_guards.py` |

## 3. Residual risks (accepted / open)

1. **Tirith fail-open**: with no network and no cached runtime, the
   semantic command layer is absent; the regex layer remains. Accepted
   (deliberate: fail-closed would brick offline installs).
2. **Credential-env blocklist is pattern-based**: exotic env var names
   for new tools could leak into `execute_code` children until added.
3. **No central choke point for path validation of downloads**: each
   download surface must adopt `validate_within_dir` itself.
4. **Prompt-injection into the LLM itself** can still cause *proposals*
   of dangerous actions — that is accepted by design; ADR-003 requires
   policy-before-execution, the audit trail records what ran, and the
   approval system is the human-in-the-loop backstop.
5. **Upstream history secret**: one real-format Telegram bot token from
   upstream bug #8908 remains in upstream public history (see
   `docs/SECURITY_FINDINGS.md`); fork tip is clean.

## 4. Verification workflow

- The full suite (13.5k+ tests) must stay green: `scripts/run_tests.sh`.
- Security changes ship with a counterfactual test: reverting the fix
  must fail exactly the new tests (all current security commits comply).
- New security-relevant code updates this document in the same PR.
