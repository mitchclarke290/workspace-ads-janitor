#!/usr/bin/env python3
"""
Dayparting + device audit:
  - Hours of day with meaningful spend and zero conversions -> ad-schedule
    bid-down suggestions
  - Devices with CPL >= X% worse than the account -> device bid-down suggestions

Both are "other adjustments": suggestions only, double-confirm gated.
"""
from collections import defaultdict

from gads_common import Findings, THRESHOLDS, cpa, date_range, fmt_money, gaql

DEVICE_NAMES = {"MOBILE": "Mobile", "DESKTOP": "Desktop", "TABLET": "Tablet"}


def run(client):
    start, end = date_range()
    f = Findings("H")

    # --- Dayparting: per-campaign hour buckets --------------------------------
    # Bucketed per campaign so one campaign's bad 2am hour never flags the
    # same hour for every other campaign.
    ts = THRESHOLDS["schedule"]
    rows = gaql(client, f"""
        SELECT campaign.id, campaign.name, segments.hour,
               metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status = 'ENABLED'
    """)
    hours = defaultdict(lambda: {"cost": 0, "conv": 0.0})
    for r in rows:
        h = hours[(str(r.campaign.id), r.campaign.name, r.segments.hour)]
        h["cost"] += r.metrics.cost_micros
        h["conv"] += r.metrics.conversions

    for (campaign_id, campaign_name, hour), h in sorted(hours.items()):
        if h["conv"] > 0 or h["cost"] < ts["min_spend_per_hour"] * 1_000_000:
            continue
        f.add(
            {
                "type": "flag_only",
                "suggestion": {
                    "kind": "ad_schedule_bid_down",
                    "hour": hour,
                    "bid_down_pct": ts["bid_down_pct"],
                    "campaign_id": campaign_id,
                },
            },
            f"{campaign_name} @ {hour:02d}:00-{hour + 1:02d}:00: "
            f"{fmt_money(h['cost'])} spend, 0 conv - consider "
            f"-{ts['bid_down_pct']}% ad-schedule bid mod on this campaign",
            {"cost_micros": h["cost"]},
        )

    # --- Devices --------------------------------------------------------------
    td = THRESHOLDS["devices"]
    rows = gaql(client, f"""
        SELECT segments.device, metrics.cost_micros, metrics.conversions
        FROM customer
        WHERE segments.date BETWEEN '{start}' AND '{end}'
    """)
    devices = defaultdict(lambda: {"cost": 0, "conv": 0.0})
    total_cost, total_conv = 0, 0.0
    for r in rows:
        d = devices[r.segments.device.name]
        d["cost"] += r.metrics.cost_micros
        d["conv"] += r.metrics.conversions
        total_cost += r.metrics.cost_micros
        total_conv += r.metrics.conversions

    account_cpa = cpa(total_cost, total_conv)
    for device, d in devices.items():
        if device not in DEVICE_NAMES or d["cost"] < td["min_spend"] * 1_000_000:
            continue
        dev_cpa = cpa(d["cost"], d["conv"])
        bad = (d["conv"] == 0) or (
            dev_cpa and account_cpa and dev_cpa >= account_cpa * (1 + td["cpa_worse_pct"] / 100)
        )
        if not bad:
            continue
        reason = (
            f"{fmt_money(d['cost'])}, 0 conv" if d["conv"] == 0
            else f"CPL ${dev_cpa:,.2f} vs account ${account_cpa:,.2f}"
        )
        f.add(
            {
                "type": "flag_only",
                "suggestion": {
                    "kind": "device_bid_down",
                    "device": device,
                    "bid_down_pct": td["bid_down_pct"],
                },
            },
            f"{DEVICE_NAMES[device]}: {reason} - consider -{td['bid_down_pct']}% "
            f"device bid adjustment on affected campaigns",
            {"cost_micros": d["cost"], "conversions": d["conv"], "cpa": dev_cpa},
        )

    return f.to_dict(), []


if __name__ == "__main__":
    from gads_common import build_client
    import json
    proposals, _ = run(build_client())
    print(json.dumps(proposals, indent=2))
