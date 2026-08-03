#!/usr/bin/env python3
"""
Spend audit - REPORT ONLY. No budget mutations are proposed or supported.

Reports, per enabled campaign over the lookback window:
  - Money losers: real spend with zero conversions or CPA far over target
  - Overspend: spend well above budget capacity (budget x days)
  - Underspend: spend well below budget capacity (wasted headroom)

Budget reallocation stays a human decision; this audit just surfaces the data.
"""
from gads_common import Findings, THRESHOLDS, cpa, date_range, fmt_money, gaql


def run(client):
    t = THRESHOLDS["spend"]
    target_cpa = THRESHOLDS["target_cpa"]
    days = THRESHOLDS["lookback_days"]
    start, end = date_range()
    f = Findings("B")  # all flag_only - nothing executable

    rows = gaql(client, f"""
        SELECT
          campaign.id, campaign.name,
          campaign_budget.amount_micros,
          campaign_budget.explicitly_shared,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status = 'ENABLED'
    """)

    for r in sorted(rows, key=lambda r: -r.metrics.cost_micros):
        cost = r.metrics.cost_micros
        conv = r.metrics.conversions
        budget = r.campaign_budget.amount_micros
        capacity = budget * days
        this_cpa = cpa(cost, conv)
        name = r.campaign.name
        shared = " (shared budget)" if r.campaign_budget.explicitly_shared else ""

        if cost >= t["loser_min_spend"] * 1_000_000 and (
            conv == 0 or (this_cpa and this_cpa > target_cpa * t["loser_cpa_multiplier"])
        ):
            reason = ("0 conv" if conv == 0
                      else f"CPA ${this_cpa:,.2f} vs target ${target_cpa:,.2f}")
            f.add(
                {"type": "flag_only"},
                f"Money loser: {name} spent {fmt_money(cost)} in {days}d | {reason}"
                f"{shared}",
                {"cost_micros": cost, "conversions": conv, "cpa": this_cpa},
            )

        if capacity <= 0:
            continue
        ratio = cost / capacity
        if ratio >= t["overspend_ratio"]:
            f.add(
                {"type": "flag_only"},
                f"Overspend: {name} spent {fmt_money(cost)} vs "
                f"{fmt_money(capacity)} budget capacity ({ratio:.0%}){shared}",
                {"cost_micros": cost, "capacity_micros": capacity, "ratio": ratio},
            )
        elif ratio <= t["underspend_ratio"] and cost > 0:
            f.add(
                {"type": "flag_only"},
                f"Underspend: {name} spent {fmt_money(cost)} of "
                f"{fmt_money(capacity)} budget capacity ({ratio:.0%}) - "
                f"headroom or delivery problem{shared}",
                {"cost_micros": cost, "capacity_micros": capacity, "ratio": ratio},
            )

    return f.to_dict(), []


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, _ = run(build_client())
    print(json.dumps(proposals, indent=2))
