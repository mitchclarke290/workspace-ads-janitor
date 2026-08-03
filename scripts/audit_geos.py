#!/usr/bin/env python3
"""
Geo audit: targeted locations bleeding spend -> exclusion proposals
(proposal-only class; still double-confirm gated like everything else).
"""
import re

from gads_common import CUSTOMER_ID, Findings, THRESHOLDS, cpa, date_range, fmt_money, gaql


def geo_names(client, ids):
    if not ids:
        return {}
    id_list = ", ".join(str(i) for i in ids)
    rows = gaql(client, f"""
        SELECT geo_target_constant.id, geo_target_constant.canonical_name
        FROM geo_target_constant
        WHERE geo_target_constant.id IN ({id_list})
    """)
    return {str(r.geo_target_constant.id): r.geo_target_constant.canonical_name for r in rows}


def run(client):
    t = THRESHOLDS["geos"]
    target_cpa = THRESHOLDS["target_cpa"]
    start, end = date_range()
    f = Findings("G")

    rows = gaql(client, f"""
        SELECT
          campaign.id, campaign.name,
          location_view.resource_name,
          metrics.cost_micros, metrics.clicks, metrics.conversions
        FROM location_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status = 'ENABLED'
    """)

    candidates = []
    for r in rows:
        # resource_name: customers/{cid}/locationViews/{campaign_id}~{criterion_id}
        m = re.search(r"locationViews/(\d+)~(\d+)", r.location_view.resource_name)
        if not m:
            continue
        cost, conv = r.metrics.cost_micros, r.metrics.conversions
        this_cpa = cpa(cost, conv)
        if cost < t["min_spend"] * 1_000_000:
            continue
        if conv > 0 and (this_cpa is None or this_cpa <= target_cpa * t["cpa_multiplier"]):
            continue
        candidates.append((r, m.group(1), m.group(2), cost, conv, this_cpa))

    names = geo_names(client, {c[2] for c in candidates})

    for r, campaign_id, geo_id, cost, conv, this_cpa in sorted(candidates, key=lambda c: -c[3]):
        where = names.get(geo_id, f"geo {geo_id}")
        reason = (
            f"{fmt_money(cost)}, 0 conv" if conv == 0
            else f"CPA ${this_cpa:,.2f} vs target ${THRESHOLDS['target_cpa']:,.2f}"
        )
        f.add(
            {
                "type": "exclude_geo",
                "customer_id": CUSTOMER_ID,
                "campaign_id": campaign_id,
                "geo_target_constant_id": geo_id,
            },
            f'Exclude "{where}" from {r.campaign.name} | {reason}',
            {"cost_micros": cost, "conversions": conv, "cpa": this_cpa},
        )

    return f.to_dict(), []


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, _ = run(build_client())
    print(json.dumps(proposals, indent=2))
