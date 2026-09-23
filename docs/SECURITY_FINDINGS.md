# Security findings — full-history secret scan and related decisions

This document records the results of the exhaustive secret scan of the
entire git history of Bassera Agent (fork chain: bassera-agent →
bassera-agent → Bassera-agent), plus the explicit decisions attached to
each finding. The threat model (`bassera_architecture/threat_model.md`)
summarizes these as residual risks; this file holds the details.

## 1. Scan method

- **Scope**: every blob in the full history of every branch — 42,510 blobs.
- **Patterns (11)**: Anthropic API key (`sk-ant-...`), OpenAI key
  (`sk-...`), Google API key (`AIza...`), Telegram bot token
  (`\d+:AA...`), Slack token (`xox[baprs]-...`), GitHub token
  (`gh[pousr]_...`), AWS key (`AKIA...`), private key headers
  (`-----BEGIN ... PRIVATE KEY-----`), JWT (`eyJ...`), generic
  `secret =` / `api_key =` assignments, and password assignments.
- **Classifier**: manual review of every hit in context (the scan script
  lives in the session scratch space; re-run any entropy-based scanner
  such as trufflehog against this repo to reproduce).

## 2. Findings

### F-1: Real-format Telegram bot token in a test fixture — FIXED at fork tip

- **Where (history)**: upstream commit `e469f3f3`, introduced via the
  reproduction case of upstream bug #8908.
  Token: `8356550917:AAGGEkzg...` (redacted here; see the commit for the
  full value and upstream references).
- **Problem**: the token has the exact shape of a real Telegram bot
  credential. If it was ever a live token (the bug report suggests it
  came from a real user environment), it grants control of that bot to
  anyone who reads upstream's public history.
- **Fix in this fork**: commit `175f69d4` replaced it with an obvious
  fixture (`1234567890:AAFixtureTokenEnvSanitizeTest__NotReal00000`)
  in `tests/bassera_cli/test_env_sanitize_on_load.py`. The fork tip is
  clean (`git grep` verifies).
- **Status**: **Fixed at tip; still present in upstream public history.**
- **Recommendation**: report upstream (bassera-agent) so they rewrite
  their history and the reporter revokes the token via @BotFather.
  History rewriting is only possible upstream — this fork cannot purge
  the value from objects it inherited; anyone cloning upstream remains
  exposed.

### F-2: Google API key in gif-search skill history — already removed upstream

- **Where (history)**: `skills/media/gif-search/SKILL.md` contained
  `AIzaSyAyimkuYQYF_...` (a widely circulated Google example key).
  Removed at the fork tip by upstream's "secure skill env setup on
  load" change (`ccfbf428`).
- **Status**: **Not present at tip.** The key is the long-public Google
  API tutorial example key, present in thousands of public repositories;
  risk is negligible. No action beyond this record.

### F-3: Telegram documentation example token — intentional fixture

- **Where**: `tests/gateway/test_weak_credential_guard.py`
  (`7123456789:AAHdqTcvCH1vGWJxfSeOfSAs0K5PALDsaw`).
- **Status**: **Intentional.** This is the canonical example token from
  Telegram's own bot API documentation, used to test the weak-credential
  guard. It is not a credential.

### All other hits

Every remaining hit is an obviously synthetic fixture: `sk-ant-abc123...`
in exfiltration tests, redaction-pattern strings in `agent/redact.py`
(the detector itself, not secrets), `xoxb-workspace-token-here` doc
placeholders, `SECRET = "BASSERA_GEMINI_CLIENT_SECRET"` (an env var *name*),
and similar. **No other real credentials were found in 42,510 blobs.**

## 3. Decision D-1: restore the 1password optional skill

The fork tree arrived with the three files of
`optional-skills/security/1password/` deleted in the working tree,
uncommitted, and with no recorded rationale. Decision: **restore**.

Reasons:

1. Upstream history shows the skill is actively maintained
   (`d41a214c` added it; `9667c71d` and `938edc64` fixed env-var
   prompting, auth docs, and broken examples) — deletion would silently
   diverge from fixes that keep landing.
2. The project mandate for this fork is to incorporate and improve all
   existing features, not to shed them without cause.
3. The skill is quarantined under `optional-skills/` and only activated
   by explicit user installation, so it carries no passive attack
   surface.
4. Keeping a deletion *and* its rationale documented would require a
   reason to exist; none was ever recorded.

The deletion was never committed, so restoring the files left no diff
to commit — the files simply remain tracked and intact at the fork
tip. If a future
maintainer wants the skill gone, remove it with `git rm` and a commit
message stating why — the point of this record is that deletions must
never again be silent.

## 4. Re-verification

Re-run the scan after any history-affecting operation:

```bash
git rev-list --all --objects | ...  # or use trufflehog / gitleaks
git grep -n "<pattern>" $(git rev-list --all)
```

A pre-commit fast subset (security tests) is installed via
`scripts/install-hooks.sh`; the full suite gates every push in CI
(`.github/workflows/tests.yml`).
