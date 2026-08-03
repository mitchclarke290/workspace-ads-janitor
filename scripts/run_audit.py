#!/usr/bin/env python3
"""
Orchestrator: run every audit, build the Slack summary, persist proposals.

READ-ONLY. Never mutates Google Ads. Outputs:
  - stdout ................ Slack-ready summary (agent posts this to #ads-janitor)
  - runs/<ts>-audit.json .. full structured findings (audit trail)
  - runs/<ts>-audit.md .... same summary, archived
  - memory/pending-approvals.json .. actionable proposals awaiting approval
                                     (expires after pending_approval_ttl_hours)

Usage:
  .venv/bin/python scripts/run_audit.py           # all audits
  .venv/bin/python scripts/run_audit.py keywords  # one section only
"""
import sys
from datetime import datetime, timedelta

from gads_common import (
    CONFIG, CUSTOMER_ID, MEMORY, RUNS, THRESHOLDS, build_client, now_iso, save_json,
)

import audit_keywords
import audit_search_terms
import audit_audiences
import audit_geos
import audit_ads_assets
import audit_spend
import audit_schedule_devices
import audit_conversion_health

SECTIONS = [
    # (key, title, module, conv_dependent)
    # conv_dependent=True -> skipped when conversion tracking looks broken,
    # because their "worst performer" logic leans on zero-conversion signals.
    ("conversion_health", "Conversion tracking", audit_conversion_health, False),
    ("keywords", "Keywords (demotion ladder)", audit_keywords, True),
    ("search_terms", "Search terms (phrase negatives)", audit_search_terms, True),
    ("audiences", "Audiences (-5% / 21-day cadence)", audit_audiences, True),
    ("geos", "Geos (exclusions)", audit_geos, True),
    ("ads_assets", "Ads & assets (disapprovals, low assets)", audit_ads_assets, False),
    ("spend", "Spend report (over/underspend - report only)", audit_spend, True),
    ("schedule_devices", "Dayparting & devices (suggestions)", audit_schedule_devices, True),
]


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    client = build_client()
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")

    # Preflight: if conversion tracking looks broken, zero-conv signals are
    # meaningless - skip conv-dependent audits instead of proposing false cuts.
    try:
        health = audit_conversion_health.check(client)
    except Exception as e:
        health = {"healthy": False,
                  "reason": f"health check failed ({type(e).__name__}: {e})"}

    results = {}
    for key, title, module, conv_dependent in SECTIONS:
        if only and key != only:
            continue
        if conv_dependent and not health["healthy"]:
            results[key] = {"title": title, "proposals": [], "flags": [],
                            "skipped": f"conversion tracking suspect - {health['reason']}"}
            continue
        try:
            proposals, flags = module.run(client)
            results[key] = {"title": title, "proposals": proposals, "flags": flags}
        except Exception as e:  # keep the run alive; report the failure
            results[key] = {"title": title, "proposals": [], "flags": [],
                            "error": f"{type(e).__name__}: {e}"}

    # ---- pending approvals (only real actions, not flag_only) ----------------
    pending = []
    for key, r in results.items():
        for item in r["proposals"]:
            if item["action"].get("type") != "flag_only":
                pending.append({**item, "section": key})

    expires = (datetime.now().astimezone()
               + timedelta(hours=CONFIG["pending_approval_ttl_hours"]))
    save_json(MEMORY / "pending-approvals.json", {
        "created_at": now_iso(),
        "expires_at": expires.isoformat(timespec="seconds"),
        "customer_id": CUSTOMER_ID,
        "status": "awaiting_confirmation_1",
        "proposals": pending,
    })

    # ---- Slack summary --------------------------------------------------------
    lines = [
        f"*GAds audit - account {CUSTOMER_ID} - last "
        f"{THRESHOLDS['lookback_days']}d - {ts}*",
        "",
    ]
    total_actions = 0
    for key, r in results.items():
        if r.get("error"):
            lines.append(f"*{r['title']}*: audit failed - {r['error']}")
            lines.append("")
            continue
        if r.get("skipped"):
            lines.append(f"*{r['title']}*: SKIPPED - {r['skipped']}")
            lines.append("")
            continue
        actionable = [i for i in r["proposals"] if i["action"].get("type") != "flag_only"]
        suggestions = [i for i in r["proposals"] if i["action"].get("type") == "flag_only"]
        flags = r["flags"]
        if not (actionable or suggestions or flags):
            continue
        lines.append(f"*{r['title']}*")
        for item in actionable:
            lines.append(f"  `{item['id']}`  {item['summary']}")
            total_actions += 1
        for item in suggestions:
            lines.append(f"  [suggestion] {item['summary']}")
        for item in flags:
            lines.append(f"  [review] {item['summary']}")
        lines.append("")

    if total_actions:
        lines += [
            f"*{total_actions} proposed action(s).* Reply with e.g. "
            "`SELECT K1,S2,A1` or `SELECT ALL` (Confirmation 1).",
            "I will echo back the exact change set for Confirmation 2 before "
            "anything is executed.",
            f"Proposals expire {CONFIG['pending_approval_ttl_hours']}h from now.",
        ]
    else:
        lines.append("No actionable worst performers this run. "
                     "Suggestions/review items above, if any.")

    summary = "\n".join(lines)

    save_json(RUNS / f"{ts}-audit.json", {"generated_at": now_iso(), "results": results})
    (RUNS / f"{ts}-audit.md").write_text(summary + "\n")
    print(summary)


if __name__ == "__main__":
    main()
