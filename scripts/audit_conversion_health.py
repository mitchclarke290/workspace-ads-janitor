#!/usr/bin/env python3
"""
Conversion-tracking health preflight.

Zero conversions is only a valid "wasteful" signal if tracking works. A broken
tag (or no conversion actions at all) would make every zero-conv audit propose
false demotions/negations account-wide. run_audit.py calls check() FIRST and
suppresses conversion-dependent proposals when tracking looks broken.

Unhealthy when account spend in the window exceeds min_spend AND either:
  - no ENABLED conversion actions exist, or
  - account-wide conversions are zero despite that spend
Low/no spend = "unknown" and treated as healthy (nothing to conclude).
"""
from gads_common import Findings, THRESHOLDS, date_range, fmt_money, gaql


def check(client):
    t = THRESHOLDS["conversion_health"]
    start, end = date_range()

    actions = gaql(client, """
        SELECT conversion_action.id, conversion_action.name,
               conversion_action.status, conversion_action.type
        FROM conversion_action
        WHERE conversion_action.status = 'ENABLED'
    """)
    enabled_actions = [
        {"id": str(r.conversion_action.id), "name": r.conversion_action.name,
         "type": r.conversion_action.type_.name}
        for r in actions
    ]

    totals = gaql(client, f"""
        SELECT metrics.cost_micros, metrics.conversions, metrics.all_conversions
        FROM customer
        WHERE segments.date BETWEEN '{start}' AND '{end}'
    """)
    cost = sum(r.metrics.cost_micros for r in totals)
    conv = sum(r.metrics.conversions for r in totals)
    all_conv = sum(r.metrics.all_conversions for r in totals)

    spend_floor = t["min_spend"] * 1_000_000
    if cost < spend_floor:
        return {"healthy": True, "reason": "insufficient spend to judge",
                "cost_micros": cost, "conversions": conv,
                "enabled_actions": len(enabled_actions)}

    if not enabled_actions:
        return {"healthy": False,
                "reason": f"{fmt_money(cost)} spend but NO enabled conversion "
                          "actions configured",
                "cost_micros": cost, "conversions": conv, "enabled_actions": 0}

    if conv == 0:
        hint = (" (all_conversions > 0 - check primary/secondary action settings)"
                if all_conv > 0 else "")
        return {"healthy": False,
                "reason": f"{fmt_money(cost)} spend, {len(enabled_actions)} enabled "
                          f"conversion action(s), but 0 conversions recorded - "
                          f"possible broken tag{hint}",
                "cost_micros": cost, "conversions": conv,
                "enabled_actions": len(enabled_actions)}

    return {"healthy": True, "reason": "tracking alive",
            "cost_micros": cost, "conversions": conv,
            "enabled_actions": len(enabled_actions)}


def run(client):
    """Report-only findings for the summary."""
    f = Findings("C")
    status = check(client)
    if not status["healthy"]:
        f.add(
            {"type": "flag_only"},
            f"CONVERSION TRACKING SUSPECT: {status['reason']}. "
            "Conversion-dependent proposals suppressed this run - fix tracking "
            "before acting on zero-conv signals.",
            status,
        )
    return f.to_dict(), []


if __name__ == "__main__":
    from gads_common import build_client
    import json
    print(json.dumps(check(build_client()), indent=2))
