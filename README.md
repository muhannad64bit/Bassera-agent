<p align="center">
  <img src="assets/banner.png" alt="Bassera Agent" width="100%">
</p>

# Bassera Agent

**A self-improving AI agent, hardened for power, stability, and security.**

Bassera-agent is a fork of [wafi-agent](https://github.com/NousResearch/wafi-agent)
(which itself evolved from hermes-agent), carrying all of its features — the
cognitive loop, persistent memory, skills, the messaging gateway, the cron
scheduler, the terminal UI — plus a focused pass of correctness and security
hardening (see **What Bassera fixes** below).

It is the only agent family with a built-in learning loop: it creates skills
from experience, improves them during use, nudges itself to persist knowledge,
searches its own past conversations, and builds a deepening model of who you
are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless
infrastructure. Talk to it from Telegram while it works on a cloud VM.

Use any model you want — Nous Portal, OpenRouter (200+ models), NVIDIA NIM,
z.ai/GLM, Kimi/Moonshot, MiniMax, Hugging Face, OpenAI, Anthropic, or your own
endpoint. Switch with `bassera model` (the `wafi` entry point also works).

| | |
|---|---|
| **Full browser control** | Default headless Chromium via `agent-browser` — navigate, view pages as an accessibility-tree snapshot, fill fields, click, and log in. Cloud backends (Browserbase, Browser Use) and Camofox stealth mode when configured. |
| **A real terminal interface** | Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output. |
| **Lives where you do** | Telegram, Discord, Slack, WhatsApp, Signal, Matrix, DingTalk, Feishu, Email — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity. |
| **A closed learning loop** | Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization. |
| **Scheduled automations** | Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, unattended. |
| **Delegates and parallelizes** | Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC. |
| **Runs anywhere** | Six terminal backends — local, Docker, SSH, Daytona, Singularity, and Modal. Daytona and Modal offer serverless persistence. |
| **Research-ready** | Batch trajectory generation, Atropos RL environments, trajectory compression for training the next generation of tool-calling models. |

---

## Quick Start

```bash
uv venv venv --python 3.12
source venv/bin/activate
uv pip install -e ".[all,dev]"
bassera setup        # interactive setup wizard
bassera              # start chatting
```

Both entry points are installed:

```bash
bassera              # Bassera identity
wafi                 # identical behavior, Wafi identity
```

`BASSERA_HOME` redirects this installation's home directory when `HERMES_HOME`
is unset (e.g. `BASSERA_HOME=~/.bassera`). Otherwise the standard `~/.wafi`
home is used.

## Common Commands

```bash
bassera              # Interactive CLI
bassera model        # Choose your LLM provider and model
bassera gateway      # Start the messaging gateway
bassera setup        # Full setup wizard
bassera doctor       # Diagnose configuration and dependencies
bassera status       # Show status of all components
bassera profile      # Manage isolated multi-instance profiles
```

Once you're in a conversation: `/new`, `/model`, `/compress`, `/usage`,
`/skills`, `/insights`, `/resume`, `/retry`, `/undo` — the shared
slash-command surface works in the CLI, the TUI, and the gateway.

### Browser automation (optional, one-time setup)

The browser tool drives a default headless Chromium — view pages as text
snapshots, fill fields, click, and log in. Enable it with:

```bash
npm install -g agent-browser
agent-browser install          # downloads Chromium (~180 MB, one time)
# Linux/Docker: agent-browser install --with-deps
```

---

## What Bassera fixes over upstream

Each of these was a real defect found by running the full test suite
(~13,500 tests) on a clean macOS/Linux checkout and tracing every failure:

**Security**
- Dangerous-command approval now catches writes to the agent's credential
  file via the real `HERMES_HOME` env var (`echo x > $HERMES_HOME/.env`
  previously bypassed approval entirely).
- Tests can no longer leak pairing/rate-limit state into the real
  `~/.wafi` home directory.

**Stability**
- File tools no longer refuse writes to the OS-designated user temp
  directory on macOS (`/var/folders/...` realpath'd under `/private/var/`).
- Interrupting a terminal command between subprocess spawn and the poll
  loop no longer orphans the process group.
- The reflective-learning loop no longer starves: structured memory
  records (owner DNA/doctrine, skill candidates) get their own char
  budget instead of competing with curated free-text notes.
- "Avoid destructive" is no longer scored as a *contradiction* of safety
  doctrine in the owner-DNA engine.
- `import wafi_cli.main` no longer crashes when some other program's
  `-p <value>` flag is in `sys.argv` (e.g. pytest plugins).
- The `./wafi` launcher actually works (it imported a module that no
  longer exists).
- `scripts/run_tests.sh` runs on macOS (bash 3.2 empty-array crash),
  uv-created venvs, and low fd limits.

**Dependencies**
- The ACP extra is pinned below `agent-client-protocol` 0.11, which removed
  the `SessionModelState` API the adapter depends on.

---

## Documentation

The upstream documentation remains the most complete reference:

| Section | What's Covered |
|---------|---------------|
| [CLI Usage](https://wafi-agent.nousresearch.com/docs/user-guide/cli) | Commands, keybindings, personalities, sessions |
| [Configuration](https://wafi-agent.nousresearch.com/docs/user-guide/configuration) | Config file, providers, models, all options |
| [Messaging Gateway](https://wafi-agent.nousresearch.com/docs/user-guide/messaging) | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| [Security](https://wafi-agent.nousresearch.com/docs/user-guide/security) | Command approval, DM pairing, container isolation |
| [Tools & Toolsets](https://wafi-agent.nousresearch.com/docs/user-guide/features/tools) | 40+ tools, toolset system, terminal backends |
| [Skills System](https://wafi-agent.nousresearch.com/docs/user-guide/features/skills) | Procedural memory, Skills Hub, creating skills |
| [Memory](https://wafi-agent.nousresearch.com/docs/user-guide/features/memory) | Persistent memory, user profiles, best practices |
| [Cron Scheduling](https://wafi-agent.nousresearch.com/docs/user-guide/features/cron) | Scheduled tasks with platform delivery |

## Development

```bash
source venv/bin/activate
scripts/run_tests.sh                    # full suite, CI-parity (always use this)
scripts/run_tests.sh tests/tools/       # one directory
scripts/run_tests.sh tests/agent/test_memory_tool.py   # one file
```

TUI (TypeScript/Ink):

```bash
cd ui-tui
npm install
npm run type-check
npm test        # vitest
npm run build
```

## Credits & License

Bassera-agent builds directly on the outstanding work of the
[wafi-agent](https://github.com/NousResearch/wafi-agent) and
hermes-agent communities. MIT — see [LICENSE](LICENSE).
