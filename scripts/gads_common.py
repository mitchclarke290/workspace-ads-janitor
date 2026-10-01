#!/usr/bin/env python3
"""
Shared helpers for the ads-janitor audit/apply scripts.

All scripts read scripts/config.json (account IDs) and scripts/thresholds.json
(audit tuning). Auth = gcloud ADC + GOOGLE_ADS_DEVELOPER_TOKEN env var,
matching the pattern proven in create_test_campaign.py.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPTS = WORKSPACE / "scripts"
MEMORY = WORKSPACE / "memory"
RUNS = WORKSPACE / "runs"

CONFIG = json.loads((SCRIPTS / "config.json").read_text())
THRESHOLDS = json.loads((SCRIPTS / "thresholds.json").read_text())

LOGIN_CID = str(CONFIG["login_customer_id"])
CUSTOMER_IDS = [str(c) for c in CONFIG.get("customer_ids") or [CONFIG["customer_id"]]]
CUSTOMER_NAMES = {str(k): v for k, v in CONFIG.get("customer_names", {}).items()}
_active = {"customer_id": CUSTOMER_IDS[0]}
_benchmarks = {}
_benchmarks_note = ""


def get_customer_id():
    return _active["customer_id"]


def customer_label(cid=None):
    cid = str(cid or get_customer_id())
    name = CUSTOMER_NAMES.get(cid)
    return f"{name} ({cid})" if name else cid


def set_active_customer(cid):
    cid = str(cid)
    if cid not in CUSTOMER_IDS:
        raise ValueError(f"{cid} is not in config customer_ids {CUSTOMER_IDS}")
    _active["customer_id"] = cid


def set_benchmarks(by_cid, note=""):
    global _benchmarks, _benchmarks_note
    _benchmarks = {str(k): v for k, v in (by_cid or {}).items()}
    _benchmarks_note = note or ""


def benchmarks_note():
    return _benchmarks_note


def target_cpl(cid=None):
    """Per-account Target CPL from the benchmarks sheet, else thresholds.json."""
    cid = str(cid or get_customer_id())
    row = _benchmarks.get(cid) or {}
    value = row.get("target_cpl")
    try:
        if value is not None and value != "":
            return float(value)
    except (TypeError, ValueError):
        pass
    return float(THRESHOLDS["target_cpl"])


def build_client():
    from google.ads.googleads.client import GoogleAdsClient
    import google.auth

    dev_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
    if not dev_token:
        # Fall back to ~/.openclaw/.env so cron/isolated sessions work too.
        env_file = Path.home() / ".openclaw" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("GOOGLE_ADS_DEVELOPER_TOKEN="):
                    dev_token = line.split("=", 1)[1].strip().strip('"')
                    break
    if not dev_token:
        sys.exit("GOOGLE_ADS_DEVELOPER_TOKEN not set (env or ~/.openclaw/.env)")

    os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", CONFIG["adc_path"])
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/adwords"])
    return GoogleAdsClient(
        credentials=creds,
        developer_token=dev_token,
        login_customer_id=LOGIN_CID,
        use_proto_plus=True,
    )


def gaql(client, query, customer_id=None):
    customer_id = customer_id or get_customer_id()
    """
    Run a GAQL query, return list of rows.

    Uses search_stream (server-side streaming) rather than paged search:
    one API operation per query regardless of row count, which keeps quota
    usage flat when this runs against large production accounts.
    """
    svc = client.get_service("GoogleAdsService")
    rows = []
    for batch in svc.search_stream(customer_id=customer_id, query=query):
        rows.extend(batch.results)
    return rows


def date_range(days=None):
    """(start, end) ISO dates for the lookback window, excluding today."""
    days = days or THRESHOLDS["lookback_days"]
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def dollars(micros):
    return micros / 1_000_000


def fmt_money(micros):
    return f"${micros / 1_000_000:,.2f}"


def cpa(cost_micros, conversions):
    return (cost_micros / 1_000_000 / conversions) if conversions else None


def load_json(path, default):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return default


def save_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, default=str) + "\n")


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Findings:
    """Collects proposals from one audit; each gets a stable prefixed ID."""

    def __init__(self, prefix):
        self.prefix = prefix
        self.items = []

    def add(self, action, summary, detail):
        """
        action: machine-readable dict consumed by apply_changes.py
                (must include "type"; see apply_changes.py ACTION_TYPES)
        summary: one-line human string for the Slack report
        detail: dict of supporting metrics (kept in the JSON for audit trail)
        """
        item_id = f"{self.prefix}{len(self.items) + 1}"
        self.items.append(
            {"id": item_id, "action": action, "summary": summary, "detail": detail}
        )
        return item_id

    def to_dict(self):
        return self.items
