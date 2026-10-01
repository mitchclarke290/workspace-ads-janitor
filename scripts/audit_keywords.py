#!/usr/bin/env python3
"""
Keyword audit: worst performers, demotion ladder proposals, low-QS flags,
duplicate/conflicting keywords.

Demotion policy (AGENTS.md rule 3): one step per cycle, never skip.
  BROAD -> PHRASE -> EXACT -> PAUSED
"""
from collections import defaultdict

from gads_common import Findings, THRESHOLDS, cpa, date_range, fmt_money, gaql, get_customer_id, target_cpl

NEXT_STEP = {"BROAD": "PHRASE", "PHRASE": "EXACT", "EXACT": "PAUSED"}


def run(client):
    t = THRESHOLDS["keywords"]
    target = target_cpl()
    start, end = date_range()
    f = Findings("K")
    flags = Findings("KF")  # review-only flags (QS, duplicates) - no mutation

    rows = gaql(client, f"""
        SELECT
          campaign.id, campaign.name,
          ad_group.id, ad_group.name,
          ad_group_criterion.criterion_id,
          ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type,
          ad_group_criterion.quality_info.quality_score,
          ad_group_criterion.effective_cpc_bid_micros,
          metrics.cost_micros, metrics.clicks, metrics.impressions,
          metrics.conversions, metrics.conversions_value
        FROM keyword_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND ad_group_criterion.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND campaign.status = 'ENABLED'
          AND ad_group_criterion.negative = FALSE
    """)

    by_text = defaultdict(list)

    for r in rows:
        kw = r.ad_group_criterion
        match_type = kw.keyword.match_type.name
        cost = r.metrics.cost_micros
        conv = r.metrics.conversions
        clicks = r.metrics.clicks
        qs = kw.quality_info.quality_score
        this_cpa = cpa(cost, conv)

        by_text[kw.keyword.text.lower().strip()].append(
            {"campaign": r.campaign.name, "ad_group": r.ad_group.name,
             "match_type": match_type, "cost": cost}
        )

        # Low QS -> flag for review only (fix ad/LP before demoting blindly)
        if 0 < qs <= t["low_quality_score_max"]:
            flags.add(
                {"type": "flag_only"},
                f'QS {qs} on "{kw.keyword.text}" ({match_type}) in {r.ad_group.name} '
                f'| {fmt_money(cost)} spend - review ad relevance / landing page',
                {"quality_score": qs, "campaign": r.campaign.name},
            )

        # Worst performer? spend floor + (zero conv w/ enough clicks, or CPL blowout)
        wasteful = (
            cost >= t["min_spend"] * 1_000_000
            and (
                (conv == 0 and clicks >= t["min_clicks_no_conv"])
                or (this_cpa is not None and this_cpa > target * t["cpa_multiplier"])
            )
        )
        if not wasteful or match_type not in NEXT_STEP:
            continue

        step = NEXT_STEP[match_type]
        reason = (
            f"{fmt_money(cost)} spend, {clicks} clicks, 0 conv"
            if conv == 0
            else f"CPL ${this_cpa:,.2f} vs target ${target:,.2f}"
        )
        f.add(
            {
                "type": "demote_keyword",
                "customer_id": get_customer_id(),
                "ad_group_id": str(r.ad_group.id),
                "criterion_id": str(kw.criterion_id),
                "keyword_text": kw.keyword.text,
                "current_match_type": match_type,
                "new_match_type": step,
                "cpc_bid_micros": kw.effective_cpc_bid_micros,
            },
            f'Keyword "{kw.keyword.text}" {match_type}->{step} '
            f'({r.campaign.name} / {r.ad_group.name}) | {reason}',
            {"cost_micros": cost, "clicks": clicks, "conversions": conv,
             "cpa": this_cpa, "quality_score": qs},
        )

    # Duplicates: same keyword text in 2+ ad groups (cross-matching risk)
    for text, entries in by_text.items():
        groups = {(e["campaign"], e["ad_group"]) for e in entries}
        if len(groups) > 1:
            places = "; ".join(f"{c}/{g}" for c, g in sorted(groups))
            flags.add(
                {"type": "flag_only"},
                f'Duplicate keyword "{text}" in {len(groups)} ad groups: {places}',
                {"entries": entries},
            )

    return f.to_dict(), flags.to_dict()


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, flags = run(build_client())
    print(json.dumps({"proposals": proposals, "flags": flags}, indent=2))
