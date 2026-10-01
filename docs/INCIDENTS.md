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
