#!/usr/bin/env python3
"""
SAMPLE ONLY - no API calls, no mutations. Generates a populated audit summary
using the exact same formatting logic as run_audit.py, but with synthetic
findings, so Mitch can see the full daily-run format (proposal IDs, sections,
suggestions, review flags, and the SELECT/CONFIRM footer).

Run: .venv/bin/python scripts/_sample_summary.py
"""
from datetime import datetime

from gads_common import CONFIG, CUSTOMER_IDS, THRESHOLDS

# (key, title, actionable-proposals, flag-only suggestions, review flags)
# Mirrors the structure run_audit.py builds from each audit module.
RESULTS = {
    "conversion_health": {"title": "Conversion tracking", "proposals": [], "flags": []},
    "keywords": {
        "title": "Keywords (demotion ladder)",
        "proposals": [
            {"id": "K1", "action": {"type": "demote_keyword"},
             "summary": 'Keyword "cheap running shoes" BROAD->PHRASE '
                        "(Janitor Test Search / Janitor Test AdGroup) | $48.20 spend, 31 clicks, 0 conv"},
            {"id": "K2", "action": {"type": "demote_keyword"},
             "summary": 'Keyword "running shoes near me" PHRASE->EXACT '
                        "(Janitor Test Search / Janitor Test AdGroup) | CPA $131.40 vs target $50.00"},
        ],
        "flags": [
            {"id": "KF1", "action": {"type": "flag_only"},
             "summary": 'QS 2 on "marathon shoes" (BROAD) in Janitor Test AdGroup '
                        "| $22.10 spend - review ad relevance / landing page"},
            {"id": "KF2", "action": {"type": "flag_only"},
             "summary": 'Duplicate keyword "running shoes" in 2 ad groups: '
                        "Janitor Test Search/AdGroup A; Janitor Test Search/AdGroup B"},
        ],
    },
    "search_terms": {
        "title": "Search terms (phrase negatives)",
        "proposals": [
            {"id": "S1", "action": {"type": "negate_search_term"},
             "summary": 'Negate "used running shoes" (phrase) in Janitor Test Search '
                        "| $27.65, 19 clicks, 0 conv"},
            {"id": "S2", "action": {"type": "negate_search_term"},
             "summary": 'Negate "running shoe repair" (phrase) in Janitor Test Search '
                        "| $18.40, 14 clicks, 0 conv"},
        ],
        "flags": [],
    },
    "audiences": {
        "title": "Audiences (-5% / 21-day cadence)",
        "proposals": [
            {"id": "A1", "action": {"type": "audience_bid_down"},
             "summary": 'Audience "In-market: Athletic Apparel" (Janitor Test Search/AdGroup) '
                        "bid modifier 1.15->1.09 (-5%) | CPA $92.30 vs campaign $61.20"},
        ],
        "flags": [
            {"id": "AS1", "action": {"type": "flag_only"},
             "summary": 'Audience "Affinity: Running Enthusiasts" underperforming but '
                        "bid-adjusted 2026-07-25 - eligible again 2026-08-15"},
        ],
    },
    "geos": {
        "title": "Geos (exclusions)",
        "proposals": [
            {"id": "G1", "action": {"type": "exclude_geo"},
             "summary": 'Exclude "Camden,New Jersey,United States" from Janitor Test Search '
                        "| $34.90, 0 conv"},
        ],
        "flags": [],
    },
    "ads_assets": {
        "title": "Ads & assets (disapprovals, low assets)",
        "proposals": [
            {"id": "D1", "action": {"type": "pause_ad"},
             "summary": "Pause DISAPPROVED ad 1122334455 "
                        "(Janitor Test Search/Janitor Test AdGroup) - then fix & resubmit"},
        ],
        "flags": [
            {"id": "DF1", "action": {"type": "flag_only"},
             "summary": 'RSA HEADLINE "Best Sellers 2026" rated LOW '
                        "(Janitor Test Search/AdGroup, ad 9988776655) - draft a replacement"},
        ],
    },
    "spend": {
        "title": "Spend report (over/underspend - report only)",
        "proposals": [],
        "flags": [
            {"id": "SPF1", "action": {"type": "flag_only"},
             "summary": "Janitor Test Search spent $210.40 vs $280.00 budget target "
                        "(75%) over 28d - underspending"},
        ],
    },
    "schedule_devices": {
        "title": "Dayparting & devices (suggestions)",
        "proposals": [
            {"id": "H1", "action": {"type": "flag_only"},
             "summary": "Hour 02:00-03:00: $14.80 spend, 0 conv across 1 campaign(s) "
                        "- consider -20% ad-schedule bid mod"},
            {"id": "H2", "action": {"type": "flag_only"},
             "summary": "Mobile: CPA $88.10 vs account $61.20 - consider -15% device "
                        "bid adjustment on affected campaigns"},
        ],
        "flags": [],
    },
}


def main():
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    lines = [
        "*[SAMPLE - synthetic findings, no live data, nothing pending]*",
        "",
        f"*GAds audit - {len(CUSTOMER_IDS)} accounts - last {THRESHOLDS['lookback_days']}d - {ts}*",
        "",
    ]
    total_actions = 0
    for key, r in RESULTS.items():
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

    print("\n".join(lines))


if __name__ == "__main__":
    main()
