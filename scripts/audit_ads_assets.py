#!/usr/bin/env python3
"""
Ads audit:
  - Disapproved ads -> pause proposal
  - Policy-limited ads -> review flags

Low RSA headlines and descriptions are audited by audit_ad_copy.py.
"""
from gads_common import Findings, gaql, get_customer_id


def run(client):
    f = Findings("D")
    flags = Findings("DF")

    # Disapproved or policy-limited ads
    rows = gaql(client, """
        SELECT
          campaign.id, campaign.name, ad_group.id, ad_group.name,
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
                    "customer_id": get_customer_id(),
                    "campaign_id": str(r.campaign.id),
                    "campaign_name": r.campaign.name,
                    "ad_group_id": str(r.ad_group.id),
                    "ad_group_name": r.ad_group.name,
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

    return f.to_dict(), flags.to_dict()


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, flags = run(build_client())
    print(json.dumps({"proposals": proposals, "flags": flags}, indent=2))
