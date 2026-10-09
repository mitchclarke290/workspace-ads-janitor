#!/usr/bin/env python3
"""
Ad copy audit: low-performing RSA headlines and descriptions.

Policy: propose removing one unpinned LOW asset at a time, and only when the
ad would still keep the responsive-search-ad minimum (3 headlines, 2
descriptions). Pinned copy is left in place. Replacement text is not written.
"""
from gads_common import Findings, THRESHOLDS, gaql, get_customer_id

COPY_FIELDS = ("HEADLINE", "DESCRIPTION")
UNPINNED = {"UNSPECIFIED", "UNKNOWN", ""}


def _rules():
    block = THRESHOLDS["assets"]
    return {
        "min_impressions": block["min_impressions"],
        "min_remaining": block.get("min_remaining") or {"HEADLINE": 3, "DESCRIPTION": 2},
    }


def select_copy(assets, rules=None):
    """
    assets: dicts with ad_group_id, ad_id, field_type, text, pinned, label,
            impressions, and the campaign/ad group names used in the summary.
    Returns (proposals, held) where held are LOW assets that cannot be removed
    without breaking the RSA minimum.
    """
    rules = rules or _rules()
    groups = {}
    for asset in assets:
        if asset["field_type"] not in COPY_FIELDS:
            continue
        key = (asset["ad_group_id"], asset["ad_id"], asset["field_type"])
        groups.setdefault(key, []).append(asset)

    proposals, held = [], []
    for key, members in groups.items():
        field = key[2]
        minimum = int(rules["min_remaining"][field])
        room = len(members) - minimum
        candidates = [
            a for a in members
            if a["label"] == "LOW"
            and a.get("pinned") in UNPINNED
            and (a.get("impressions") or 0) >= rules["min_impressions"]
            and (a.get("text") or "").strip()
        ]
        candidates.sort(key=lambda a: (-(a.get("cost_micros") or 0), -(a.get("impressions") or 0)))
        if room <= 0:
            held.extend(candidates)
            continue
        proposals.extend(candidates[:room])
        held.extend(candidates[room:])
    return proposals, held


def _summary(asset):
    label = asset["field_type"].lower()
    return (
        f'Remove LOW {label} "{asset["text"]}" '
        f'({asset["campaign_name"]}/{asset["ad_group_name"]}, ad {asset["ad_id"]}) '
        f'| {asset["impressions"]} impr'
    )


def _action(asset):
    return {
        "type": "pause_ad_copy",
        "customer_id": get_customer_id(),
        "campaign_id": str(asset["campaign_id"]),
        "campaign_name": asset["campaign_name"],
        "ad_group_id": str(asset["ad_group_id"]),
        "ad_group_name": asset["ad_group_name"],
        "ad_id": str(asset["ad_id"]),
        "asset_id": str(asset["asset_id"]),
        "field_type": asset["field_type"],
        "text": asset["text"],
    }


def _detail(asset):
    return {
        "impressions": asset.get("impressions") or 0,
        "clicks": asset.get("clicks") or 0,
        "cost_micros": asset.get("cost_micros") or 0,
        "conversions": asset.get("conversions") or 0,
        "performance_label": asset.get("label"),
    }


def run(client):
    rules = _rules()
    f = Findings("AC")
    held_flags = Findings("ACH")
    field_list = ", ".join(f"'{name}'" for name in COPY_FIELDS)
    rows = gaql(client, f"""
        SELECT
          campaign.id, campaign.name,
          ad_group.id, ad_group.name,
          ad_group_ad.ad.id,
          ad_group_ad_asset_view.field_type,
          ad_group_ad_asset_view.performance_label,
          ad_group_ad_asset_view.pinned_field,
          asset.id, asset.text_asset.text,
          metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM ad_group_ad_asset_view
        WHERE ad_group_ad_asset_view.enabled = TRUE
          AND ad_group_ad_asset_view.field_type IN ({field_list})
          AND campaign.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND ad_group_ad.status = 'ENABLED'
    """)
    assets = []
    for r in rows:
        pinned = r.ad_group_ad_asset_view.pinned_field.name
        assets.append({
            "campaign_id": r.campaign.id,
            "campaign_name": r.campaign.name,
            "ad_group_id": str(r.ad_group.id),
            "ad_group_name": r.ad_group.name,
            "ad_id": str(r.ad_group_ad.ad.id),
            "asset_id": r.asset.id,
            "field_type": r.ad_group_ad_asset_view.field_type.name,
            "label": r.ad_group_ad_asset_view.performance_label.name,
            "pinned": pinned,
            "text": r.asset.text_asset.text or "",
            "impressions": r.metrics.impressions,
            "clicks": r.metrics.clicks,
            "cost_micros": r.metrics.cost_micros,
            "conversions": r.metrics.conversions,
        })

    chosen, held = select_copy(assets, rules)
    for asset in chosen:
        minimum = rules["min_remaining"][asset["field_type"]]
        f.add(
            _action(asset),
            _summary(asset),
            {**_detail(asset), "min_remaining": minimum},
        )
    for asset in held:
        minimum = rules["min_remaining"][asset["field_type"]]
        held_flags.add(
            {"type": "flag_only"},
            f'LOW {asset["field_type"].lower()} "{asset["text"]}" kept on ad {asset["ad_id"]} '
            f'({asset["campaign_name"]}/{asset["ad_group_name"]}) — removing it would '
            f'leave fewer than {minimum}',
            _detail(asset),
        )
    return f.to_dict(), held_flags.to_dict()


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, flags = run(build_client())
    print(json.dumps({"proposals": proposals, "flags": flags}, indent=2))
