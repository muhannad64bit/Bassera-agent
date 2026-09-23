---
sidebar_position: 2
---

# Profiles: Running Multiple Agents

Run multiple independent Bassera agents on the same machine — each with its own config, API keys, memory, sessions, skills, and gateway state.

## What are profiles?

A profile is a separate Bassera home directory. Each profile gets its own directory containing its own `config.yaml`, `.env`, `SOUL.md`, memories, sessions, skills, cron jobs, and state database. Profiles let you run separate agents for different purposes — a coding assistant, a personal bot, a research agent — without mixing up Bassera state.

When you create a profile, it automatically becomes its own command. Create a profile called `coder` and you immediately have `coder chat`, `coder setup`, `coder gateway start`, etc.

## Quick start

```bash
bassera profile create coder       # creates profile + "coder" command alias
coder setup                       # configure API keys and model
coder chat                        # start chatting
```

That's it. `coder` is now its own Bassera profile with its own config, memory, and state.

## Creating a profile

### Blank profile

```bash
bassera profile create mybot
```

Creates a fresh profile with bundled skills seeded. Run `mybot setup` to configure API keys, model, and gateway tokens.

### Clone config only (`--clone`)

```bash
bassera profile create work --clone
```

Copies your current profile's `config.yaml`, `.env`, and `SOUL.md` into the new profile. Same API keys and model, but fresh sessions and memory. Edit `~/.bassera/profiles/work/.env` for different API keys, or `~/.bassera/profiles/work/SOUL.md` for a different personality.

### Clone everything (`--clone-all`)

```bash
bassera profile create backup --clone-all
```

Copies **everything** — config, API keys, personality, all memories, full session history, skills, cron jobs, plugins. A complete snapshot. Useful for backups or forking an agent that already has context.

### Clone from a specific profile

```bash
bassera profile create work --clone --clone-from coder
```

:::tip Honcho memory + profiles
When Honcho is enabled, `--clone` automatically creates a dedicated AI peer for the new profile while sharing the same user workspace. Each profile builds its own observations and identity. See [Honcho -- Multi-agent / Profiles](./features/memory-providers.md#honcho) for details.
:::

## Using profiles

### Command aliases

Every profile automatically gets a command alias at `~/.local/bin/<name>`:

```bash
coder chat                    # chat with the coder agent
coder setup                   # configure coder's settings
coder gateway start           # start coder's gateway
coder doctor                  # check coder's health
coder skills list             # list coder's skills
coder config set model.model anthropic/claude-sonnet-4
```

The alias works with every bassera subcommand — it's just `bassera -p <name>` under the hood.

### The `-p` flag

You can also target a profile explicitly with any command:

```bash
bassera -p coder chat
bassera --profile=coder doctor
bassera chat -p coder -q "hello"    # works in any position
```

### Sticky default (`bassera profile use`)

```bash
bassera profile use coder
bassera chat                   # now targets coder
bassera tools                  # configures coder's tools
bassera profile use default    # switch back
```

Sets a default so plain `bassera` commands target that profile. Like `kubectl config use-context`.

### Knowing where you are

The CLI always shows which profile is active:

- **Prompt**: `coder ❯` instead of `❯`
- **Banner**: Shows `Profile: coder` on startup
- **`bassera profile`**: Shows current profile name, path, model, gateway status

## Profiles vs workspaces vs sandboxing

Profiles are often confused with workspaces or sandboxes, but they are different things:

- A **profile** gives Bassera its own state directory: `config.yaml`, `.env`, `SOUL.md`, sessions, memory, logs, cron jobs, and gateway state.
- A **workspace** or **working directory** is where terminal commands start. That is controlled separately by `terminal.cwd`.
- A **sandbox** is what limits filesystem access. Profiles do **not** sandbox the agent.

On the default `local` terminal backend, the agent still has the same filesystem access as your user account. A profile does not stop it from accessing folders outside the profile directory.

If you want a profile to start in a specific project folder, set an explicit absolute `terminal.cwd` in that profile's `config.yaml`:

```yaml
terminal:
  backend: local
  cwd: /absolute/path/to/project
```

Using `cwd: "."` on the local backend means "the directory Bassera was launched from", not "the profile directory".

Also note:

- `SOUL.md` can guide the model, but it does not enforce a workspace boundary.
- Changes to `SOUL.md` take effect cleanly on a new session. Existing sessions may still be using the old prompt state.
- Asking the model "what directory are you in?" is not a reliable isolation test. If you need a predictable starting directory for tools, set `terminal.cwd` explicitly.

## Running gateways

Each profile runs its own gateway as a separate process with its own bot token:

```bash
coder gateway start           # starts coder's gateway
assistant gateway start       # starts assistant's gateway (separate process)
```

### Different bot tokens

Each profile has its own `.env` file. Configure a different Telegram/Discord/Slack bot token in each:

```bash
# Edit coder's tokens
nano ~/.bassera/profiles/coder/.env

# Edit assistant's tokens
nano ~/.bassera/profiles/assistant/.env
```

### Safety: token locks

If two profiles accidentally use the same bot token, the second gateway will be blocked with a clear error naming the conflicting profile. Supported for Telegram, Discord, Slack, WhatsApp, and Signal.

### Persistent services

```bash
coder gateway install         # creates bassera-gateway-coder systemd/launchd service
assistant gateway install     # creates bassera-gateway-assistant service
```

Each profile gets its own service name. They run independently.

## Configuring profiles

Each profile has its own:

- **`config.yaml`** — model, provider, toolsets, all settings
- **`.env`** — API keys, bot tokens
- **`SOUL.md`** — personality and instructions

```bash
coder config set model.model anthropic/claude-sonnet-4
echo "You are a focused coding assistant." > ~/.bassera/profiles/coder/SOUL.md
```

If you want this profile to work in a specific project by default, also set its own `terminal.cwd`:

```bash
coder config set terminal.cwd /absolute/path/to/project
```

## Updating

`bassera update` pulls code once (shared) and syncs new bundled skills to **all** profiles automatically:

```bash
bassera update
# → Code updated (12 commits)
# → Skills synced: default (up to date), coder (+2 new), assistant (+2 new)
```

User-modified skills are never overwritten.

## Managing profiles

```bash
bassera profile list           # show all profiles with status
bassera profile show coder     # detailed info for one profile
bassera profile rename coder dev-bot   # rename (updates alias + service)
bassera profile export coder   # export to coder.tar.gz
bassera profile import coder.tar.gz   # import from archive
```

## Deleting a profile

```bash
bassera profile delete coder
```

This stops the gateway, removes the systemd/launchd service, removes the command alias, and deletes all profile data. You'll be asked to type the profile name to confirm.

Use `--yes` to skip confirmation: `bassera profile delete coder --yes`

:::note
You cannot delete the default profile (`~/.bassera`). To remove everything, use `bassera uninstall`.
:::

## Tab completion

```bash
# Bash
eval "$(bassera completion bash)"

# Zsh
eval "$(bassera completion zsh)"
```

Add the line to your `~/.bashrc` or `~/.zshrc` for persistent completion. Completes profile names after `-p`, profile subcommands, and top-level commands.

## How it works

Profiles use the `BASSERA_HOME` environment variable. When you run `coder chat`, the wrapper script sets `BASSERA_HOME=~/.bassera/profiles/coder` before launching bassera. Since 119+ files in the codebase resolve paths via `get_bassera_home()`, Bassera state automatically scopes to the profile's directory — config, sessions, memory, skills, state database, gateway PID, logs, and cron jobs.

This is separate from terminal working directory. Tool execution starts from `terminal.cwd` (or the launch directory when `cwd: "."` on the local backend), not automatically from `BASSERA_HOME`.

The default profile is simply `~/.bassera` itself. No migration needed — existing installs work identically.
