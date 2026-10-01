# ads-janitor maintenance scripts

Deterministic Python scripts the janitor runs via the workspace venv:

```bash
VENV=~/.openclaw/workspaces/ads-janitor/.venv/bin/python
cd ~/.openclaw/workspaces/ads-janitor
```

All account IDs live in `scripts/config.json`. Shared floors live in
`scripts/thresholds.json`. Per-account Target CPL lives in the Google Sheet
`Ads Janitor KPI Benchmarks` (`benchmarks_spreadsheet_id` in config). Currently
pointed at production MCC `5671434588` and the daily CIDs in `customer_ids`.

## The loop (daily, 9:30 AM ET)

### 1. Audit (read-only, scheduled)

```bash
$VENV scripts/run_audit.py
```

- Preflight: conversion-tracking health check. If tracking looks broken
  (spend but no enabled conversion actions, or zero recorded conversions on
  real spend), all conversion-dependent audits are SKIPPED for the run and a
  warning is posted instead - a dead tag must never trigger mass demotions.
- Then runs the audits (keywords, search terms, audiences, geos, ads/assets,
  spend report, dayparting/devices).
- Prints a short Slack index -> post it to `#ads-janitor` verbatim.
- Writes `runs/<ts>-audit.json`, markdown tables at `runs/<ts>-audit.md`, a formatted workbook at `runs/<ts>-audit.xlsx`, and `memory/pending-approvals.json`.
- Proposals carry IDs: `K*` keywords, `S*` search terms, `A*` audiences,
  `G*` geos, `D*` disapproved ads. `[suggestion]` and `[review]` lines
  (including all `B*` spend and `C*` conversion-health items) have no
  executable action — they are for Mitchell to consider.
- Scheduled runs STOP HERE. No mutation on cron, ever.

Run a single section while debugging: `$VENV scripts/run_audit.py keywords`

### 2. Confirmation 1 — Mitchell selects

Mitchell replies in Slack: `SELECT 1173238911-K1,3442012546-S2` (or `SELECT ALL`).

### 3. Echo the exact change set (dry run)

```bash
$VENV scripts/apply_changes.py --ids K1,S2,A1
```

Mutates nothing. Prints the verbatim change set (current -> new, resource
IDs). Post it to Slack and ask Mitchell to reply `CONFIRM` (Confirmation 2).

### 4. Confirmation 2 — execute

Only after Mitchell's `CONFIRM`:

```bash
$VENV scripts/apply_changes.py --ids K1,S2,A1 --execute
```

- Refuses if the ids differ from step 3, if proposals expired (48h), or if
  more than 25 actions are selected.
- Applies changes, writes `runs/<ts>-changes.md`, updates
  `memory/audience-bid-state.json` for audience bid-downs.
- Post the printed change log to Slack.

Anything else from Mitchell (`cancel`, a new SELECT, silence) = do not
execute; restart from step 2 or drop the batch.

## What each action does

| Type | Mechanics |
|---|---|
| `demote_keyword` | BROAD->PHRASE or PHRASE->EXACT: creates the tighter-match keyword (same text/bid), pauses the old criterion. EXACT: pauses the keyword. One step per cycle, never skips. |
| `negate_search_term` | Adds a campaign-level PHRASE negative keyword. |
| `audience_bid_down` | Sets criterion bid_modifier to current x 0.95, once per segment per 21 days (gated by `memory/audience-bid-state.json`). |
| `pause_ad` | Pauses a disapproved ad (fix + resubmit is manual). |

Geo exclusions are not recommended and `apply_changes.py` refuses `exclude_geo`.

**Budgets are report-only.** `audit_spend.py` surfaces money losers and
over/underspend vs budget capacity; `apply_changes.py` has no budget
executor, so a budget mutation is impossible through this pipeline.
Reallocation is a human decision made in the Google Ads UI.

Dayparting and device bid adjustments are emitted as `[suggestion]` lines
only in v1 — if Mitchell wants one applied, implement it as a proposal type
first; never freelance a mutation.

## Files

- `gads_common.py` — auth (ADC + dev token), streaming GAQL helper, config loading
- `audit_conversion_health.py` — preflight; gates all conv-dependent audits
- `audit_*.py` — one audit per asset class; importable or standalone
- `audit_spend.py` — report-only over/underspend + money losers (no mutations)
- `run_audit.py` — orchestrator; the only thing cron should invoke
- `apply_changes.py` — the only script allowed to mutate; double-confirm gated
- `create_test_campaign.py` — test-only scaffolding; hardcoded allowlist refuses
  to run against anything but the test account
- `config.json` / `thresholds.json` — accounts and tuning
