# Changelog

All notable changes to Bassera Agent are documented here. Format based
on [Keep a Changelog](https://keepachangelog.com/); the project is
pre-1.0, so everything lives under Unreleased until a version is cut.

Bassera Agent is a fork of bassera-agent (itself an evolution of
bassera-agent). Entries below cover the production-hardening effort;
referenced commit hashes are in this repository's history.

## [Unreleased]

### Security

- **Credential-file write bypass closed** (`dc64958e`): the
  dangerous-command detector only matched `$bassera_home`, while the
  runtime reads `BASSERA_HOME` — so `echo x > $BASSERA_HOME/.env` and
  `tee $BASSERA_HOME/.env` sailed past approval. The regex now covers
  all home spellings; the file-tools deny list resolves the `.env`
  target at check time and denies both the active and the default
  home, so profile sessions can no longer clobber the main install.
  Counterfactually proven: reverting fails exactly the named tests.
- **Fuzzing of the dangerous-command detector** (`dc13ea02`): hypothesis
  property tests (never-raises, case-insensitivity, ReDoS bound under
  2 s on 60 KB pathological inputs) found **26 sibling holes** of the
  original `.env` bypass — `cp`/`mv`/`install`/`rsync` into any
  sensitive target (`~/.ssh/authorized_keys`, `/etc/passwd`, block
  devices), in-place `sed` edits, and the backtick quote spelling all
  bypassed approval. All closed with statement-scoped patterns; old
  `/etc/`-only patterns kept for allowlist-key compatibility; ten
  benign-command assertions guard against over-flagging.
- **Audit trail** (`94c0a39d`): every dangerous-operation decision
  (approved/denied, human/smart/cron/unattended) is now a structured
  JSON line in `<home>/logs/audit.log`, with the command redacted and
  unattended dangerous runs marked `"unchecked": true`. Best-effort:
  audit failure can never change an approval outcome.
- **Full-history secret scan** (`175f69d4`, results in
  `docs/SECURITY_FINDINGS.md`): 42,510 blobs scanned across the whole
  fork chain. One real-format Telegram bot token (introduced upstream
  in `e469f3f3` via bug #8908's reproduction file) replaced at the tip
  with an obvious fixture; it remains in upstream public history —
  recommendation recorded to report upstream and revoke via @BotFather.
  All other hits are synthetic fixtures.
- **Threat model** (`59aeb6eb`): formal entry-point inventory (env
  ingestion, file writes, subprocess spawning, network, skills/MCP,
  messaging gateway) with protected/partial status, pinning tests, and
  a residual-risk register (`bassera_architecture/threat_model.md`).

### Fixed

- Subprocess orphan race: an interrupt between spawn and the poll loop
  left the process group alive (`6e6125c5`).
- macOS temp-dir writes were refused because realpath of the OS user
  temp dir lands under `/private/var/` (was in the sensitive-path
  prefix list) — user temp roots are now exempt.
- Interrupt flags keyed by thread ident could land on an unrelated
  thread after ident reuse in long-lived gateways; now weakref-guarded.
- Telegram mentions silently dropped when any non-BMP character (e.g.
  emoji) preceded them: entity offsets are UTF-16 code units but
  detection sliced Python code points. New `utf16_slice()` helper.
- `-p <value>` argv pre-parser claimed any `-p`, including pytest's
  `-p no:cacheprovider`, crashing import; non-profile-shaped values are
  no longer claimed.
- Doctor misdiagnosed a globally-installed agent-browser as missing
  (only checked `node_modules`); now uses the browser tool's own
  resolver so the verdict matches reality.
- Vision tool downloaded remote images into CWD (`./temp_vision_images/`);
  now the OS temp dir.
- Tools disappearing silently when their availability `check_fn` raised;
  now logged at WARNING with traceback.
- Five stale/platform-broken tests failing on a clean checkout
  (`08562e4c`): retired Gemini catalog pins, WSL/systemd test that
  could never reach its code on macOS, a win32 `sys.platform` fake
  crashing POSIX `shutil.which`, bassera-era mention-case assumptions,
  and a macOS-unavailable mautrix crypto dependency (now
  importorskip-gated).

### Performance

- `--version` from ~10 s worst case (synchronous git-fetch update check
  on every invocation) to 0.15 s: cache-only flag path with background
  refresh, and OpenAI SDK version read from package metadata instead of
  importing the SDK (`06fa3f24`). The interactive `version` subcommand
  keeps the full check, bounded by a reduced 3 s timeout.

### Test infrastructure

- **Hermetic suite** (`531d91ad`): full runs no longer leak state
  (state.db, processes.json, pairing/rate-limit entries, feishu dedup,
  document caches) into the developer's real `~/.bassera`. Import-time
  frozen homes are captured at conftest import into a throwaway
  directory; cleared-env tests keep an isolated home; pairing state
  resolves lazily.
- **Machine-local gateway lock isolation** (`722aa96e`): gateway scoped
  locks live outside the agent home by design, so the home isolation
  did not cover them — xdist workers collided on identical test
  identities (the whatsapp connect tests failed "session already in
  use" under 4 workers) and the suite left lock files in the
  developer's real `~/.local/state`. Now isolated per worker.
- **One shared platform-mock decision point per worker** (`8acb5ee0`):
  the e2e and gateway conftests each installed their own
  discord/telegram mocks gated on import state, so whichever ran first
  in an xdist worker decided the binding — a bare MagicMock baked into
  the production modules failed 50 gateway Discord tests in mixed
  workers. The comprehensive shared mocks now install once, in the root
  conftest, before any platform module import; conftest contracts are
  pinned by `tests/test_conftest_hygiene.py`.
- **Hermetic e2e agent-loop coverage** (`ee2fadbb`): drives
  `AIAgent.run_conversation()` end to end with the LLM client as the
  only seam — a real `write_file` tool call executes through the
  registry and lands on disk. `tests/e2e` is part of the canonical run;
  `tests/integration` (live credentials) remains excluded.
- Property-based fuzz suite, audit-trail tests, and the canonical
  runner `scripts/run_tests.sh` (xdist 4 workers, deterministic env,
  credential vars blanked, fd headroom, uv-aware, bash 3.2-safe).
- Security-code coverage baseline recorded: 78% across the seven
  security-critical modules (`docs/COVERAGE_BASELINE.md`).

### Documentation

- Threat model (`bassera_architecture/threat_model.md`), secret-scan
  findings and decisions (`docs/SECURITY_FINDINGS.md`), TUI WebSocket
  transport design doc including its five blocking gaps
  (`bassera_architecture/tui_websocket_transport.md`), logging and
  audit-schema conventions (`docs/LOGGING.md`).

### Branding

- Complete Bassera identity: bassera→bassera rename leftovers finished,
  logo redrawn, SOUL persona, platform payloads, copilot User-Agent
  `BasseraAgent/1.0`, and user-facing strings
  (`cdc6ef3d`, `d335afd5`, `30e2d73e`). A prompt-mask desync in the
  Anthropic system-prompt masking cache found during the sweep was
  fixed alongside.
- Upstream TUI WebSocket/launch WIP carried from the fork tree
  (`cee65b35`); see the design doc before extending it.

### Decisions

- The `optional-skills/security/1password/` skill (arrived deleted in
  the working tree with no rationale) is **restored** — see
  `docs/SECURITY_FINDINGS.md` §3 for the reasoning and the rule that
  deletions must never again be silent.
