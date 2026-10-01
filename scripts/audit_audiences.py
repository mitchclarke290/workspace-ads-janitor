#!/usr/bin/env python3
"""
Audience-segment audit: underperformers -> one-time -5% bid modifier proposals.

Policy (AGENTS.md rule 5): -5% once, only if the segment's last adjustment
was >= 21 days ago. Cadence state lives in memory/audience-bid-state.json.
Segments inside the cadence window are reported as skipped, with the date
they become eligible again.
"""
from datetime import date, datetime, timedelta

from gads_common import (
    Findings, MEMORY, THRESHOLDS, cpa, date_range, fmt_money,
    gaql, get_customer_id, load_json,
)

STATE_PATH = MEMORY / "audience-bid-state.json"


def campaign_cpas(client, start, end):
    rows = gaql(client, f"""
        SELECT campaign.id, metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status = 'ENABLED'
    """)
    return {
        str(r.campaign.id): cpa(r.metrics.cost_micros, r.metrics.conversions)
        for r in rows
    }


def run(client):
    t = THRESHOLDS["audiences"]
    start, end = date_range()
    f = Findings("A")
    skipped = Findings("AS")
    state = load_json(STATE_PATH, {"segments": {}})["segments"]
    camp_cpa = campaign_cpas(client, start, end)

    rows = gaql(client, f"""
        SELECT
          campaign.id, campaign.name,
          ad_group.id, ad_group.name,
          ad_group_criterion.criterion_id,
          ad_group_criterion.display_name,
          ad_group_criterion.bid_modifier,
          ad_group_criterion.resource_name,
          metrics.cost_micros, metrics.clicks, metrics.conversions
        FROM ad_group_audience_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
    """)

    for r in rows:
        crit = r.ad_group_criterion
        cost = r.metrics.cost_micros
        conv = r.metrics.conversions
        seg_cpa = cpa(cost, conv)
        base = camp_cpa.get(str(r.campaign.id))
        seg_key = f"{get_customer_id()}~{r.ad_group.id}~{crit.criterion_id}"
        name = crit.display_name or f"criterion {crit.criterion_id}"

        if cost < t["min_spend"] * 1_000_000:
            continue
        # Underperforming = zero conv on real spend, or CPL >= X% worse than campaign
        bad = (conv == 0) or (
            seg_cpa is not None and base
            and seg_cpa >= base * (1 + t["cpa_worse_pct"] / 100)
        )
        if not bad:
            continue

        last = state.get(seg_key)
        if last:
            last_date = datetime.fromisoformat(last).date()
            eligible_on = last_date + timedelta(days=t["cadence_days"])
            if date.today() < eligible_on:
                skipped.add(
                    {"type": "flag_only"},
                    f'Audience "{name}" ({r.campaign.name}/{r.ad_group.name}) '
                    f'underperforming but bid-adjusted {last} - eligible again {eligible_on}',
                    {"segment_key": seg_key, "last_adjusted": last},
                )
                continue

        current = crit.bid_modifier if crit.bid_modifier else 1.0
        new = round(current * t["bid_down_factor"], 4)
        reason = (
            f"{fmt_money(cost)}, 0 conv" if conv == 0
            else f"CPL ${seg_cpa:,.2f} vs campaign ${base:,.2f}"
        )
        f.add(
            {
                "type": "audience_bid_down",
                "customer_id": get_customer_id(),
                "resource_name": crit.resource_name,
                "segment_key": seg_key,
                "current_bid_modifier": current,
                "new_bid_modifier": new,
            },
            f'Audience "{name}" ({r.campaign.name}/{r.ad_group.name}) '
            f'bid modifier {current:.2f}->{new:.2f} (-5%) | {reason}',
            {"cost_micros": cost, "conversions": conv, "cpa": seg_cpa,
             "campaign_cpa": base},
        )

    return f.to_dict(), skipped.to_dict()


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, skipped = run(build_client())
    print(json.dumps({"proposals": proposals, "skipped": skipped}, indent=2))
