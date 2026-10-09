#!/usr/bin/env python3
"""
Durable record of mutations made through apply_changes.py.

Each successful change stores:
  - what changed (keyword, search-term negative, audience bid, or ad)
  - 7-day performance of the campaign, and of the ad group / ad / keyword
    when that level exists
  - a follow-up date one week later

The daily audit prints due follow-ups (campaign week-after vs the baseline
week). Mitchell can reply `SNOOZE F1` to push that check out another 7 days
and compare the next week against the prior week.

    .venv/bin/python scripts/change_ledger.py snooze F1
"""
import sys
from datetime import date, datetime, timedelta

from gads_common import (
    MEMORY, cpa, customer_label, fmt_money, gaql, load_json, now_iso, save_json,
    set_active_customer,
)

LEDGER_PATH = MEMORY / "change-ledger.json"
FOLLOWUP_DAYS = 7


def empty_ledger():
    return {"next_id": 1, "entries": []}


def load_ledger(path=None):
    data = load_json(path or LEDGER_PATH, empty_ledger())
    data.setdefault("next_id", 1)
    data.setdefault("entries", [])
    return data


def save_ledger(data, path=None):
    save_json(path or LEDGER_PATH, data)


def _parse_day(value):
    return date.fromisoformat(str(value)[:10])


def week_ending(end):
    end = end if isinstance(end, date) else _parse_day(end)
    return end - timedelta(days=6), end


def schedule_for(changed_on):
    """Baseline is the 7 complete days before the change. First check is the
    morning after the 7 days that start on the change date."""
    changed_on = changed_on if isinstance(changed_on, date) else _parse_day(changed_on)
    base_start, base_end = week_ending(changed_on - timedelta(days=1))
    after_end = changed_on + timedelta(days=FOLLOWUP_DAYS - 1)
    return {
        "changed_on": changed_on.isoformat(),
        "baseline": {"start": base_start.isoformat(), "end": base_end.isoformat()},
        "next_check": (after_end + timedelta(days=1)).isoformat(),
    }


def _metric_pack(cost, clicks, impressions, conversions):
    cpl = cpa(cost, conversions)
    return {
        "cost_micros": int(cost or 0),
        "clicks": int(clicks or 0),
        "impressions": int(impressions or 0),
        "conversions": float(conversions or 0),
        "cpl": None if cpl is None else round(cpl, 2),
    }


def _sum_rows(rows):
    cost = clicks = impressions = 0
    conversions = 0.0
    for r in rows:
        cost += r.metrics.cost_micros
        clicks += r.metrics.clicks
        impressions += r.metrics.impressions
        conversions += r.metrics.conversions
    return _metric_pack(cost, clicks, impressions, conversions)


def _gid(value):
    text = str(value or "")
    if not text.isdigit():
        raise ValueError(f"refusing non-numeric Google Ads id {text!r}")
    return text


def _query_metrics(client, cid, resource, start, end, where):
    rows = gaql(client, f"""
        SELECT metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.conversions
        FROM {resource}
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND {where}
    """, customer_id=cid)
    return _sum_rows(rows)


def resolve_scope(client, action):
    """Return campaign / ad group / asset ids for a mutation action."""
    cid = str(action["customer_id"])
    set_active_customer(cid)
    scope = {
        "customer_id": cid,
        "account": customer_label(cid),
        "campaign_id": str(action.get("campaign_id") or ""),
        "campaign_name": action.get("campaign_name") or "",
        "ad_group_id": str(action.get("ad_group_id") or ""),
        "ad_group_name": action.get("ad_group_name") or "",
        "ad_id": str(action.get("ad_id") or ""),
        "criterion_id": str(action.get("criterion_id") or ""),
    }
    if scope["ad_group_id"] and not scope["campaign_id"]:
        rows = gaql(client, f"""
            SELECT campaign.id, campaign.name, ad_group.id, ad_group.name
            FROM ad_group
            WHERE ad_group.id = {_gid(scope["ad_group_id"])}
        """, customer_id=cid)
        if rows:
            scope["campaign_id"] = str(rows[0].campaign.id)
            scope["campaign_name"] = rows[0].campaign.name
            scope["ad_group_name"] = scope["ad_group_name"] or rows[0].ad_group.name
    elif scope["campaign_id"] and not scope["campaign_name"]:
        rows = gaql(client, f"""
            SELECT campaign.id, campaign.name
            FROM campaign
            WHERE campaign.id = {_gid(scope["campaign_id"])}
        """, customer_id=cid)
        if rows:
            scope["campaign_name"] = rows[0].campaign.name
    return scope


def snapshot_performance(client, scope, start, end):
    cid = scope["customer_id"]
    set_active_customer(cid)
    levels = {}
    if scope.get("campaign_id"):
        levels["campaign"] = _query_metrics(
            client, cid, "campaign", start, end,
            f"campaign.id = {_gid(scope['campaign_id'])}",
        )
    if scope.get("ad_group_id"):
        levels["ad_group"] = _query_metrics(
            client, cid, "ad_group", start, end,
            f"ad_group.id = {_gid(scope['ad_group_id'])}",
        )
    if scope.get("criterion_id") and scope.get("ad_group_id"):
        levels["keyword"] = _query_metrics(
            client, cid, "keyword_view", start, end,
            "ad_group_criterion.criterion_id = "
            f"{_gid(scope['criterion_id'])} AND ad_group.id = {_gid(scope['ad_group_id'])}",
        )
    if scope.get("ad_id") and scope.get("ad_group_id"):
        levels["ad"] = _query_metrics(
            client, cid, "ad_group_ad", start, end,
            f"ad_group_ad.ad.id = {_gid(scope['ad_id'])} "
            f"AND ad_group.id = {_gid(scope['ad_group_id'])}",
        )
    return levels


def record_change(client, item, path=None):
    """Append a ledger row for one successful mutation. Snapshot failures are
    stored on the row and do not raise — the mutation already happened."""
    action = item["action"]
    ledger = load_ledger(path)
    fid = f"F{ledger['next_id']}"
    ledger["next_id"] += 1
    changed_on = date.today()
    sched = schedule_for(changed_on)
    entry = {
        "id": fid,
        "proposal_id": item.get("id"),
        "status": "open",
        "type": action.get("type"),
        "summary": item.get("summary") or "",
        "changed_at": now_iso(),
        "changed_on": sched["changed_on"],
        "next_check": sched["next_check"],
        "last_reported": None,
        "scope": {},
        "baseline_window": sched["baseline"],
        "baseline": {},
        "baseline_error": None,
        "checks": [],
    }
    try:
        scope = resolve_scope(client, action)
        entry["scope"] = scope
        entry["baseline"] = snapshot_performance(
            client, scope, sched["baseline"]["start"], sched["baseline"]["end"]
        )
    except Exception as e:
        entry["baseline_error"] = f"{type(e).__name__}: {e}"
    ledger["entries"].append(entry)
    save_ledger(ledger, path)
    return entry


def _is_due(entry, today):
    if entry.get("status") != "open":
        return False
    if _parse_day(entry["next_check"]) > today:
        return False
    reported = entry.get("last_reported")
    return not reported or _parse_day(reported) < _parse_day(entry["next_check"])


def _followup_window(entry, today):
    """First check uses the 7 days starting on the change date. Later checks
    (after a snooze) use the 7 complete days ending yesterday."""
    if not entry.get("checks"):
        start = _parse_day(entry["changed_on"])
        return start, start + timedelta(days=FOLLOWUP_DAYS - 1)
    end = today - timedelta(days=1)
    return week_ending(end)


def _fmt_level(stats):
    if not stats:
        return "no data"
    cpl = stats.get("cpl")
    cpl_s = "n/a" if cpl is None else f"${cpl:,.2f}"
    conv = stats.get("conversions") or 0
    conv_s = f"{conv:.0f}" if float(conv).is_integer() else f"{conv:.1f}"
    return (
        f"{fmt_money(stats.get('cost_micros') or 0)} spend, "
        f"{stats.get('clicks', 0)} clicks, {conv_s} conv, CPL {cpl_s}"
    )


def _window_label(window):
    return f"{window['start']}–{window['end']}"


def snooze(fid, path=None, today=None):
    today = today or date.today()
    ledger = load_ledger(path)
    fid = fid.strip().upper()
    match = next((e for e in ledger["entries"] if e["id"].upper() == fid), None)
    if not match:
        raise SystemExit(f"No change follow-up {fid}.")
    if match.get("status") != "open":
        raise SystemExit(f"{fid} is {match.get('status')}; nothing to snooze.")
    match["next_check"] = (today + timedelta(days=FOLLOWUP_DAYS)).isoformat()
    match["last_reported"] = None
    match.setdefault("snoozes", []).append({
        "at": now_iso(),
        "next_check": match["next_check"],
    })
    save_ledger(ledger, path)
    return match


def collect_due(client, path=None, today=None):
    """Query due follow-ups, mark them reported, return Slack lines.
    A failed lookup stays due for the next audit."""
    today = today or date.today()
    ledger = load_ledger(path)
    lines = []
    changed = False
    for entry in ledger["entries"]:
        if not _is_due(entry, today):
            continue
        start, end = _followup_window(entry, today)
        window = {"start": start.isoformat(), "end": end.isoformat()}
        try:
            levels = snapshot_performance(client, entry.get("scope") or {}, start, end)
        except Exception as e:
            entry["last_error"] = f"{type(e).__name__}: {e}"
            changed = True
            name = (entry.get("scope") or {}).get("campaign_name") or entry["id"]
            lines.append(
                f"`{entry['id']}` {entry.get('summary') or name} — "
                f"follow-up lookup failed ({entry['last_error']}). Will retry tomorrow."
            )
            continue
        check = {"checked_at": now_iso(), "window": window, "levels": levels}
        entry.setdefault("checks", []).append(check)
        entry["last_reported"] = today.isoformat()
        entry["last_error"] = None
        changed = True
        lines.extend(_format_entry(entry, check))
    if changed:
        save_ledger(ledger, path)
    return lines


def _format_entry(entry, check):
    scope = entry.get("scope") or {}
    campaign = scope.get("campaign_name") or scope.get("campaign_id") or "campaign unknown"
    account = scope.get("account") or scope.get("customer_id") or ""
    base = (entry.get("baseline") or {}).get("campaign")
    after = (check.get("levels") or {}).get("campaign")
    prior = base
    prior_window = entry.get("baseline_window") or {}
    if len(entry.get("checks") or []) > 1:
        previous = entry["checks"][-2]
        prior = (previous.get("levels") or {}).get("campaign")
        prior_window = previous.get("window") or {}
    lines = [
        f"`{entry['id']}` *{account}* — {campaign}",
        f"Change {entry['changed_on']}: {entry.get('summary') or entry.get('type')}",
        f"Before ({_window_label(prior_window)}): {_fmt_level(prior)}",
        f"After ({_window_label(check['window'])}): {_fmt_level(after)}",
    ]
    if len(entry.get("checks") or []) > 1 and entry.get("baseline_window"):
        lines.append(
            "At the change ("
            f"{_window_label(entry['baseline_window'])}): "
            f"{_fmt_level((entry.get('baseline') or {}).get('campaign'))}"
        )
    return lines


def render_followups(lines):
    if not lines:
        return ""
    body = ["*Change follow-ups*"]
    body.extend(lines)
    body.append("Reply `SNOOZE F1` (use the id shown) to check that campaign again in 7 days.")
    return "\n".join(body)


def main():
    if len(sys.argv) == 3 and sys.argv[1].lower() == "snooze":
        entry = snooze(sys.argv[2])
        scope = entry.get("scope") or {}
        campaign = scope.get("campaign_name") or scope.get("campaign_id") or entry["id"]
        print(
            f"Snoozed `{entry['id']}` ({campaign}). "
            f"Next performance check {entry['next_check']}."
        )
        return
    sys.exit("Usage: change_ledger.py snooze F1")


if __name__ == "__main__":
    main()
