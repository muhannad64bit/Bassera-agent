# Security-code coverage baseline

Recorded 2026-09-22, Python 3.12, macOS, at the commit range ending
`341e715f` (Phase 4 of the production-hardening plan, step 12).

## Method

Coverage of the seven security-critical modules, measured by running
the 22 test files that target them (approval suite incl. fuzz + audit,
write-deny, file-operations suites, URL safety, website policy,
credential files, cron approval mode, redaction, pairing):

```bash
TZ=UTC LANG=C.UTF-8 PYTHONHASHSEED=0 \
  ./venv/bin/python -m pytest -o "addopts=" -q \
  tests/tools/test_approval*.py tests/tools/test_write_deny.py \
  tests/tools/test_file_*.py tests/tools/test_url_safety.py \
  tests/tools/test_website_policy.py tests/tools/test_credential_files.py \
  tests/tools/test_cron_approval_mode.py tests/agent/test_redact.py \
  tests/gateway/test_pairing.py \
  --cov=tools.approval --cov=tools.file_operations --cov=tools.url_safety \
  --cov=tools.website_policy --cov=tools.credential_files --cov=agent.redact \
  --cov=gateway.pairing --cov-report=term
```

All 628 tests passed. This is a **targeted subset** run, not the whole
suite; other tests also touch these modules, so full-suite numbers can
only be higher.

## Baseline

| Module | Stmts | Cover | Notes on uncovered regions |
|---|---|---|---|
| `tools/url_safety.py` | 49 | **96%** | two DNS-resolution edge branches |
| `gateway/pairing.py` | 169 | **94%** | a few malformed-input fallbacks |
| `agent/redact.py` | 63 | **92%** | two rare pattern branches |
| `tools/website_policy.py` | 173 | **88%** | config-parse fallbacks, a few metadata paths |
| `tools/credential_files.py` | 188 | **87%** | provider-specific parse fallbacks |
| `tools/file_operations.py` | 561 | **70%** | large live-filesystem helpers (sync back, staleness edge cases) exercised only partially outside this subset |
| `tools/approval.py` | 451 | **71%** | interactive terminal prompt paths (`prompt_dangerous_approval` choice handling: session/always/deny UX branches), container env types, and some gateway pending-approval plumbing |
| **Total** | 1654 | **78%** | |

## Reading the baseline

- The **deny/guard logic itself** (pattern detection, sensitive-target
  matching, fuzz invariants, audit emission) is the well-covered part;
  the gaps are mostly **interactive UI plumbing and live-filesystem
  edge paths**, which need a TTY or real filesystem events and are
  partially covered by the remaining suite.
- `tools/approval.py` at 71% should not be read as "the guard is 29%
  untested": `detect_dangerous_command`, the audit trail, and every
  known attack pattern (including all 26 fuzz-found sibling holes) sit
  in the covered regions. The uncovered lines are the human-approval
  prompt loop and per-env-type dispatch that the subset's tests mock
  past.

## Where uncovered code matters

Priority follow-ups, in order of security relevance:

1. `tools/approval.py` gateway `submit_pending` path (lines ~665-701):
   the messaging-side approval request flow — exercised end-to-end
   only via the e2e suite, not asserted in the unit subset.
2. `tools/credential_files.py` 234-241, 321-333: provider credential
   file parse fallbacks — a crash there means a user's credentials
   cannot load, not a bypass, but it is user-visible.
3. `tools/file_operations.py` live-sync helpers: covered by the
   broader suite; listed only for completeness.

## Reproducing

pytest-cov is in the `dev` extras. Re-run the command above and compare
against this table in the same PR that changes any of these modules —
security-relevant changes should not lower their module's number.
