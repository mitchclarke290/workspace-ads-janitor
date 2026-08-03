#!/usr/bin/env python3
"""
Apply approved changes from memory/pending-approvals.json.

Two-phase, matching the double-confirmation rule in AGENTS.md:

  Phase 1 (Confirmation 1 received - "SELECT K1,S2,..."):
    .venv/bin/python scripts/apply_changes.py --ids K1,S2
    -> DRY RUN. Prints the exact change set verbatim (IDs, current -> new).
       The agent posts this to Slack and asks for Confirmation 2.
       Nothing is mutated. Selection is recorded in pending-approvals.json.

  Phase 2 (Confirmation 2 received - "CONFIRM"):
    .venv/bin/python scripts/apply_changes.py --ids K1,S2 --execute
    -> Applies ONLY those ids, appends the change log to runs/ and prints a
       Slack-ready change log. Updates memory/audience-bid-state.json for any
       audience bid changes.

Guardrails:
  - refuses if proposals are expired or ids don't match the pending set
  - refuses if --ids differs from the phase-1 selection (re-run phase 1)
  - hard cap: max_mutations_per_run (config.json)
  - keyword demotion is one step only; new keyword is created, old is paused
    (Google Ads does not allow editing match type in place)
"""
import argparse
import sys
from datetime import datetime

from gads_common import (
    CONFIG, CUSTOMER_ID, MEMORY, RUNS, build_client, load_json, now_iso, save_json,
)

PENDING = MEMORY / "pending-approvals.json"
AUDIENCE_STATE = MEMORY / "audience-bid-state.json"


# --------------------------------------------------------------------------- #
# Describers (phase 1) and executors (phase 2), keyed by action type
# --------------------------------------------------------------------------- #

def describe(item):
    a = item["action"]
    t = a["type"]
    if t == "demote_keyword":
        if a["new_match_type"] == "PAUSED":
            return (f'{item["id"]}: PAUSE keyword "{a["keyword_text"]}" (EXACT), '
                    f'ad group {a["ad_group_id"]}, criterion {a["criterion_id"]}')
        return (f'{item["id"]}: keyword "{a["keyword_text"]}" '
                f'{a["current_match_type"]} -> {a["new_match_type"]} '
                f'(create new {a["new_match_type"]} criterion, pause old '
                f'{a["criterion_id"]}) in ad group {a["ad_group_id"]}')
    if t == "negate_search_term":
        return (f'{item["id"]}: add campaign-level PHRASE negative "{a["term"]}" '
                f'to campaign {a["campaign_id"]}')
    if t == "audience_bid_down":
        return (f'{item["id"]}: audience {a["segment_key"]} bid modifier '
                f'{a["current_bid_modifier"]:.2f} -> {a["new_bid_modifier"]:.2f} (-5%)')
    if t == "exclude_geo":
        return (f'{item["id"]}: exclude geoTargetConstants/{a["geo_target_constant_id"]} '
                f'from campaign {a["campaign_id"]}')
    if t == "pause_ad":
        return f'{item["id"]}: pause ad {a["ad_id"]} in ad group {a["ad_group_id"]}'
    return f'{item["id"]}: UNSUPPORTED action type "{t}"'


def exec_demote_keyword(client, a):
    from google.protobuf.field_mask_pb2 import FieldMask
    svc = client.get_service("AdGroupCriterionService")
    agc_path = svc.ad_group_criterion_path(CUSTOMER_ID, a["ad_group_id"], a["criterion_id"])
    ops = []

    if a["new_match_type"] != "PAUSED":
        create = client.get_type("AdGroupCriterionOperation")
        c = create.create
        c.ad_group = client.get_service("AdGroupService").ad_group_path(
            CUSTOMER_ID, a["ad_group_id"])
        c.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
        c.keyword.text = a["keyword_text"]
        c.keyword.match_type = getattr(
            client.enums.KeywordMatchTypeEnum, a["new_match_type"])
        if a.get("cpc_bid_micros"):
            c.cpc_bid_micros = a["cpc_bid_micros"]
        ops.append(create)

    pause = client.get_type("AdGroupCriterionOperation")
    p = pause.update
    p.resource_name = agc_path
    p.status = client.enums.AdGroupCriterionStatusEnum.PAUSED
    client.copy_from(pause.update_mask, FieldMask(paths=["status"]))
    ops.append(pause)

    res = svc.mutate_ad_group_criteria(customer_id=CUSTOMER_ID, operations=ops)
    return [r.resource_name for r in res.results]


def exec_negate_search_term(client, a):
    svc = client.get_service("CampaignCriterionService")
    op = client.get_type("CampaignCriterionOperation")
    c = op.create
    c.campaign = client.get_service("CampaignService").campaign_path(
        CUSTOMER_ID, a["campaign_id"])
    c.negative = True
    c.keyword.text = a["term"]
    c.keyword.match_type = client.enums.KeywordMatchTypeEnum.PHRASE
    res = svc.mutate_campaign_criteria(customer_id=CUSTOMER_ID, operations=[op])
    return [r.resource_name for r in res.results]


def exec_audience_bid_down(client, a):
    from google.protobuf.field_mask_pb2 import FieldMask
    svc = client.get_service("AdGroupCriterionService")
    op = client.get_type("AdGroupCriterionOperation")
    u = op.update
    u.resource_name = a["resource_name"]
    u.bid_modifier = a["new_bid_modifier"]
    client.copy_from(op.update_mask, FieldMask(paths=["bid_modifier"]))
    res = svc.mutate_ad_group_criteria(customer_id=CUSTOMER_ID, operations=[op])

    state = load_json(AUDIENCE_STATE, {"segments": {}})
    state["segments"][a["segment_key"]] = datetime.now().date().isoformat()
    save_json(AUDIENCE_STATE, state)
    return [r.resource_name for r in res.results]


def exec_exclude_geo(client, a):
    svc = client.get_service("CampaignCriterionService")
    op = client.get_type("CampaignCriterionOperation")
    c = op.create
    c.campaign = client.get_service("CampaignService").campaign_path(
        CUSTOMER_ID, a["campaign_id"])
    c.negative = True
    c.location.geo_target_constant = (
        f"geoTargetConstants/{a['geo_target_constant_id']}")
    res = svc.mutate_campaign_criteria(customer_id=CUSTOMER_ID, operations=[op])
    return [r.resource_name for r in res.results]


def exec_pause_ad(client, a):
    from google.protobuf.field_mask_pb2 import FieldMask
    svc = client.get_service("AdGroupAdService")
    op = client.get_type("AdGroupAdOperation")
    u = op.update
    u.resource_name = svc.ad_group_ad_path(CUSTOMER_ID, a["ad_group_id"], a["ad_id"])
    u.status = client.enums.AdGroupAdStatusEnum.PAUSED
    client.copy_from(op.update_mask, FieldMask(paths=["status"]))
    res = svc.mutate_ad_group_ads(customer_id=CUSTOMER_ID, operations=[op])
    return [r.resource_name for r in res.results]


# NOTE: budget mutations are intentionally NOT supported. Budgets are
# report-only (audit_spend.py); reallocation is a human decision in the UI.
EXECUTORS = {
    "demote_keyword": exec_demote_keyword,
    "negate_search_term": exec_negate_search_term,
    "audience_bid_down": exec_audience_bid_down,
    "exclude_geo": exec_exclude_geo,
    "pause_ad": exec_pause_ad,
}


# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", required=True,
                    help="Comma-separated proposal ids (e.g. K1,S2,A1) or ALL")
    ap.add_argument("--execute", action="store_true",
                    help="Actually mutate. Without this: dry-run echo only.")
    args = ap.parse_args()

    pending = load_json(PENDING, None)
    if not pending or not pending.get("proposals"):
        sys.exit("No pending proposals. Run run_audit.py first.")

    if datetime.fromisoformat(pending["expires_at"]) < datetime.now().astimezone():
        sys.exit(f"Pending proposals expired at {pending['expires_at']}. "
                 "Run a fresh audit - do not apply stale changes.")

    if pending["customer_id"] != CUSTOMER_ID:
        sys.exit("Pending proposals were generated for a different customer id. Aborting.")

    by_id = {p["id"]: p for p in pending["proposals"]}
    if args.ids.strip().upper() == "ALL":
        ids = list(by_id)
    else:
        ids = [i.strip().upper() for i in args.ids.split(",") if i.strip()]
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        sys.exit(f"Unknown proposal id(s): {', '.join(unknown)}. "
                 f"Valid: {', '.join(by_id)}")

    cap = CONFIG["max_mutations_per_run"]
    if len(ids) > cap:
        sys.exit(f"{len(ids)} actions requested; cap is {cap} per run. "
                 "Split into batches.")

    selected = [by_id[i] for i in ids]

    if not args.execute:
        # Phase 1: verbatim echo for Confirmation 2
        pending["status"] = "awaiting_confirmation_2"
        pending["selected_ids"] = ids
        pending["selected_at"] = now_iso()
        save_json(PENDING, pending)
        print(f"DRY RUN - exact change set for account {CUSTOMER_ID} "
              f"({len(selected)} action(s)):\n")
        for item in selected:
            print("  " + describe(item))
        print("\nNothing has been changed. Post the above to Slack verbatim and "
              "ask Mitchell to reply CONFIRM (Confirmation 2).")
        print("On CONFIRM, re-run with the same --ids plus --execute.")
        return

    # Phase 2: enforce that phase 1 happened with the exact same set
    if pending.get("status") != "awaiting_confirmation_2":
        sys.exit("Phase order violation: run phase 1 (no --execute) first "
                 "so the change set is echoed for Confirmation 2.")
    if sorted(pending.get("selected_ids", [])) != sorted(ids):
        sys.exit("--ids differs from the set echoed in phase 1. "
                 "Re-run phase 1 with the new selection.")

    client = build_client()
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    log = [f"# Change log - {ts} - account {CUSTOMER_ID}", ""]
    ok, failed = [], []

    for item in selected:
        a = item["action"]
        try:
            resources = EXECUTORS[a["type"]](client, a)
            ok.append(item["id"])
            log.append(f"- DONE {describe(item)}")
            for rn in resources:
                log.append(f"    - {rn}")
        except Exception as e:
            failed.append(item["id"])
            log.append(f"- FAILED {item['id']}: {type(e).__name__}: {e}")

    log += ["", f"Executed {len(ok)}/{len(selected)}; failed: "
                f"{', '.join(failed) if failed else 'none'}",
            f"Approved by: Mitchell (double-confirmed over Slack)",
            f"Timestamp: {now_iso()}"]

    logtext = "\n".join(log)
    (RUNS / f"{ts}-changes.md").write_text(logtext + "\n")

    pending["status"] = "executed"
    pending["executed_at"] = now_iso()
    pending["executed_ids"] = ok
    pending["failed_ids"] = failed
    save_json(PENDING, pending)

    print(logtext)
    print("\nPost this change log to Slack.")


if __name__ == "__main__":
    main()
