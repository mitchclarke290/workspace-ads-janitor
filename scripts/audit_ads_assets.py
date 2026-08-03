#!/usr/bin/env python3
"""
Ads & assets audit:
  - Disapproved / limited ads -> pause proposal + fix flag
  - RSA assets with "LOW" performance label -> review flags
"""
from gads_common import CUSTOMER_ID, Findings, THRESHOLDS, gaql


def run(client):
    f = Findings("D")
    flags = Findings("DF")

    # Disapproved or policy-limited ads
    rows = gaql(client, """
        SELECT
          campaign.name, ad_group.id, ad_group.name,
          ad_group_ad.ad.id, ad_group_ad.ad.type,
          ad_group_ad.policy_summary.approval_status,
          ad_group_ad.status
        FROM ad_group_ad
        WHERE ad_group_ad.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND campaign.status = 'ENABLED'
    """)
    for r in rows:
        approval = r.ad_group_ad.policy_summary.approval_status.name
        if approval in ("DISAPPROVED",):
            f.add(
                {
                    "type": "pause_ad",
                    "customer_id": CUSTOMER_ID,
                    "ad_group_id": str(r.ad_group.id),
                    "ad_id": str(r.ad_group_ad.ad.id),
                },
                f"Pause DISAPPROVED ad {r.ad_group_ad.ad.id} "
                f"({r.campaign.name}/{r.ad_group.name}) - then fix & resubmit",
                {"approval_status": approval, "ad_type": r.ad_group_ad.ad.type_.name},
            )
        elif approval in ("AREA_OF_INTEREST_ONLY", "APPROVED_LIMITED"):
            flags.add(
                {"type": "flag_only"},
                f"Ad {r.ad_group_ad.ad.id} is {approval} "
                f"({r.campaign.name}/{r.ad_group.name}) - review policy details",
                {"approval_status": approval},
            )

    # Low-performing RSA assets (review-only; replacement needs new copy)
    t = THRESHOLDS["assets"]
    rows = gaql(client, """
        SELECT
          campaign.name, ad_group.name,
          ad_group_ad.ad.id,
          ad_group_ad_asset_view.field_type,
          ad_group_ad_asset_view.performance_label,
          asset.id, asset.text_asset.text,
          metrics.impressions
        FROM ad_group_ad_asset_view
        WHERE ad_group_ad_asset_view.enabled = TRUE
          AND campaign.status = 'ENABLED'
    """)
    for r in rows:
        label = r.ad_group_ad_asset_view.performance_label.name
        if label == "LOW" and r.metrics.impressions >= t["min_impressions"]:
            text = r.asset.text_asset.text or f"asset {r.asset.id}"
            flags.add(
                {"type": "flag_only"},
                f'RSA {r.ad_group_ad_asset_view.field_type.name} "{text}" rated LOW '
                f"({r.campaign.name}/{r.ad_group.name}, ad {r.ad_group_ad.ad.id}) "
                f"- draft a replacement",
                {"impressions": r.metrics.impressions},
            )

    return f.to_dict(), flags.to_dict()


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, flags = run(build_client())
    print(json.dumps({"proposals": proposals, "flags": flags}, indent=2))
