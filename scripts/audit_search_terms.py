#!/usr/bin/env python3
"""
Search-term audit: wasteful queries -> campaign-level PHRASE negative proposals.

Policy (AGENTS.md rule 4): phrase-match negatives only.
Skips terms already covered by an existing campaign negative.
"""
from gads_common import Findings, THRESHOLDS, date_range, fmt_money, gaql, get_customer_id


def phrase_covers(negative, term):
    """
    True if an existing phrase negative would already block this search term.
    Phrase-negative semantics: the negative's words must appear in the term
    as a contiguous, in-order word sequence (not a raw substring - "free"
    must NOT match "freedom").
    """
    neg_tokens = negative.split()
    term_tokens = term.split()
    n = len(neg_tokens)
    if n == 0 or n > len(term_tokens):
        return False
    return any(
        term_tokens[i:i + n] == neg_tokens
        for i in range(len(term_tokens) - n + 1)
    )


def existing_negatives(client):
    """campaign_id -> set of negative keyword texts (lowercased)."""
    rows = gaql(client, """
        SELECT campaign.id, campaign_criterion.keyword.text
        FROM campaign_criterion
        WHERE campaign_criterion.negative = TRUE
          AND campaign_criterion.type = 'KEYWORD'
          AND campaign_criterion.status != 'REMOVED'
    """)
    negs = {}
    for r in rows:
        negs.setdefault(str(r.campaign.id), set()).add(
            r.campaign_criterion.keyword.text.lower().strip()
        )
    return negs


def run(client):
    t = THRESHOLDS["search_terms"]
    start, end = date_range()
    f = Findings("S")
    negs = existing_negatives(client)

    rows = gaql(client, f"""
        SELECT
          campaign.id, campaign.name,
          ad_group.name,
          search_term_view.search_term,
          metrics.cost_micros, metrics.clicks, metrics.conversions
        FROM search_term_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status = 'ENABLED'
    """)

    # Aggregate per (campaign, term) - a term can appear under several ad groups
    agg = {}
    for r in rows:
        key = (str(r.campaign.id), r.search_term_view.search_term.lower().strip())
        a = agg.setdefault(
            key, {"campaign_name": r.campaign.name, "cost": 0, "clicks": 0, "conv": 0.0}
        )
        a["cost"] += r.metrics.cost_micros
        a["clicks"] += r.metrics.clicks
        a["conv"] += r.metrics.conversions

    for (campaign_id, term), a in sorted(agg.items(), key=lambda kv: -kv[1]["cost"]):
        if a["conv"] > 0:
            continue
        if a["cost"] < t["min_spend"] * 1_000_000 and a["clicks"] < t["min_clicks_no_conv"]:
            continue
        covered = any(phrase_covers(neg, term) for neg in negs.get(campaign_id, ()))
        if covered:
            continue
        f.add(
            {
                "type": "negate_search_term",
                "customer_id": get_customer_id(),
                "campaign_id": campaign_id,
                "campaign_name": a["campaign_name"],
                "term": term,
                "match_type": "PHRASE",
            },
            f'Negate "{term}" (phrase) in {a["campaign_name"]} '
            f'| {fmt_money(a["cost"])}, {a["clicks"]} clicks, 0 conv',
            {"cost_micros": a["cost"], "clicks": a["clicks"]},
        )

    return f.to_dict(), []


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, _ = run(build_client())
    print(json.dumps(proposals, indent=2))
