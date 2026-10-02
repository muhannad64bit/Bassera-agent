# Incident log

## 2026-09: silent working-tree reverts (external process)

**What happened.** Twice during the 2026-09 hardening sessions, the
tracked file `agent/prompt_builder.py` was found reverted to a
PRE-REBRAND copy in the working tree — old imports
(`from wafi_constants import ...`), the old "Wafi Agent" persona, old
constants. The content matched no recent git state on the branch.

**Forensics** (all negative — the mechanism is not git):
- no stash residue (`git stash list` empty; reflog has no stash/checkout
  that could restore that content)
- no `skip-worktree` / `assume-unchanged` index flags
  (`git ls-files -v` all `H`)
- no fsmonitor daemon
- no repo process writes that file (no test, no script)
- processes holding the repo open at investigation time: the session CLI
  and an IDE ("Antigravity IDE", a VS Code fork) — the plausible vector
  is an editor buffer/local-history restore or an AI-assistant plugin
  writing stale content into the checkout.

**Impact.** The first revert broke imports loudly (caught by the test
suite immediately). A revert in a less-imported file — docs, a single
test, config — would have been silent.

**Mitigation (deployed).**
- `scripts/check_worktree_integrity.sh` — lists tracked files whose
  content differs from HEAD without being staged; exit 2 (or
  `--warn-only`) so it can gate automation.
- The pre-commit hook installed by `scripts/install-hooks.sh` runs the
  check as a visible preflight on every commit.

**What to do if it happens again.** Run
`scripts/check_worktree_integrity.sh`; for any file you did not edit,
`git diff <file>` to see the drift and `git checkout HEAD -- <file>` to
restore. Check the IDE's local-history and any AI-assistant plugins with
file-write access before reopening the repo.

## 2026-10-01/02: strikes 4 and 5 — run.py revert + untracked file deletion

**Strike 4 (run.py).** The verified working-tree edits to
`gateway/run.py` (delegating stubs + `self_update` import for the
item-6 extraction) were found reverted to HEAD between two tool calls,
minutes after they had passed the 82-test update characterization
suite. The untracked `gateway/self_update.py` survived; only the
tracked file was reverted.

**Strike 5 (self_update.py).** During the re-verification run,
`gateway/self_update.py` was deleted mid-session — first observed as
13 `NameError` test failures (a partially-mangled copy was in place at
collection time), then confirmed gone by a direct `grep` seconds
later. This is the first observed strike against an *untracked* file,
which `git status` cannot flag (it only ever showed `??`).

**Response.** The extraction was rebuilt deterministically from
`HEAD:gateway/run.py` by a transformation script
(`/tmp/rebuild_self_update.py`) applying the documented verbatim-move
rules, re-verified (all 82 update tests green), and committed in the
same session step rather than left in the working tree.

**Mitigation update (now deployed).**
- Commit immediately after each verified step — never leave a green
  state sitting in the working tree across a long test run.
- For untracked files, keep a `/tmp` copy (`cp <file> /tmp/<file>.bak`)
  so a deletion is a one-command restore.
- Prefer regenerating extracted files from a script over hand-restoring:
  a script is idempotent, auditable, and immune to buffer restore.

## 2026-10-02: strike 7 — the mutator now makes commits

**What happened.** Two newly written security-coverage test files were
staged with `git add` (per the standing defense protocol). Seconds
later, and before the author's own `git commit` ran, a commit appeared
on the branch: message "Initial commit", author/committer = the user's
git identity, tree = exactly the two staged files. The author's
subsequent `git commit` then failed with "nothing to commit".

**Significance.** This is the first observed strike that COMMITS rather
than reverts or deletes. It means the external process is running
`git commit` (or equivalent plumbing) against the repo. Nothing was
lost — the committed tree was exactly the author's staged content —
but the junk message destroyed the audit trail for the change, and a
future strike could just as easily commit a half-finished or wrong
state, or race a real commit mid-hook.

**Response.** The junk commit was not pushed; `git commit --amend`
replaced only the message (identical tree), and the corrected commit
(f8452eaa) carries a note about the incident.

**Mitigation (on top of the existing protocol).**
- Check `git log` for commits you did not make before every push.
- Never push immediately after an unexpected "nothing to commit" —
  inspect `git log -1 --stat` first; it may be a mutator commit.
- Prefer amending an unpushed mutator commit over reverting/recreating
  when the tree is provably your own staged content.
- The definitive fix remains: close the IDE (or disable its AI
  plugin's write/commit access) during agent sessions in this repo.
