#!/usr/bin/env python3
"""
lm_active_alerts.py

Pulls all currently active alerts from LogicMonitor's REST API v3
(/alert/alerts), with full alert detail (severity, resource, message,
datasource, ack state, start time, etc.) -- not just the ID.

Auth
----
Uses LMv1 HMAC-signed authentication, which is what an Access ID /
Access Key pair (the classic LM API credential type, distinct from the
Edwin AI OAuth client used in edwin_incident_lookup.py) requires.

Set credentials via environment variables -- never hardcode them:

    export LM_ACCOUNT="<your-account>"
    export LM_ACCESS_ID="your-access-id"
    export LM_ACCESS_KEY="your-access-key"

If you have a Bearer token instead of an Access ID/Key pair, see the
USE_BEARER_TOKEN flag below -- flip it to True and set LM_BEARER_TOKEN
instead; LMv1 signing is skipped entirely in that case.

Usage
-----
    python3 lm_active_alerts.py
    python3 lm_active_alerts.py --output alerts.csv
    python3 lm_active_alerts.py --filter 'monitorObjectName:"prod*"'
    python3 lm_active_alerts.py --size 500
    python3 lm_active_alerts.py --include-sdt       # also include SDT'd alerts
    python3 lm_active_alerts.py --include-warning   # also include Warning severity

By default, pulls active (uncleared) alerts EXCLUDING:
  - alerts on a resource currently in Scheduled Downtime (sdted:"false"),
    since these are expected/suppressed noise, and
  - Warning-severity (2) alerts, since they typically don't generate ServiceNow
    incidents and are excluded by default.
Use --include-sdt / --include-warning to bring either back in. Paginates
automatically until exhausted, and writes a CSV with the same key columns
as a typical LM alert export (id, severity, resource, datasource/instance,
message, start time, acked, sdted).
"""

import argparse
import base64
import csv
import hashlib
import hmac
import json
import os
import sys
import time

import requests

ACCOUNT = os.environ.get("LM_ACCOUNT", "<your-account>")
ACCESS_ID = os.environ.get("LM_ACCESS_ID")
ACCESS_KEY = os.environ.get("LM_ACCESS_KEY")
BEARER_TOKEN = os.environ.get("LM_BEARER_TOKEN")

USE_BEARER_TOKEN = False  # flip to True if you have a Bearer token instead

BASE_URL = f"https://{ACCOUNT}.logicmonitor.com/santaba/rest"
RESOURCE_PATH = "/alert/alerts"

# Columns pulled out of each alert object for the CSV. LM alert objects
# have many more fields than this; add to FIELDS if you need others --
# print one raw record with --raw-sample to see everything available.
FIELDS = [
    "id",
    "severity",
    "monitorObjectName",
    "resourceTemplateName",
    "instanceName",
    "dataPointName",
    "alertValue",
    "startEpoch",
    "acked",
    "ackedBy",
    "sdted",
    "cleared",
    "monitorObjectGroups",
    "type",
    "detailMessage",
]


def lmv1_auth_header(method: str, resource_path: str, body: str = "") -> str:
    if not ACCESS_ID or not ACCESS_KEY:
        sys.exit(
            "Missing LM_ACCESS_ID / LM_ACCESS_KEY environment variables.\n"
            "Set them before running this script -- never paste credentials "
            "directly into this file."
        )
    epoch = str(int(time.time() * 1000))
    request_vars = method + epoch + body + resource_path
    digest = hmac.new(
        ACCESS_KEY.encode("utf-8"), request_vars.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    signature = base64.b64encode(digest.encode("utf-8")).decode("utf-8")
    return f"LMv1 {ACCESS_ID}:{signature}:{epoch}"


def get_auth_headers(method: str, resource_path: str, body: str = "") -> dict:
    if USE_BEARER_TOKEN:
        if not BEARER_TOKEN:
            sys.exit("USE_BEARER_TOKEN is True but LM_BEARER_TOKEN is not set.")
        return {"Authorization": f"Bearer {BEARER_TOKEN}"}
    return {"Authorization": lmv1_auth_header(method, resource_path, body)}


def fetch_active_alerts(
    extra_filter: str | None,
    page_size: int,
    exclude_sdt: bool = True,
    exclude_warning: bool = True,
) -> list[dict]:
    """
    Paginates through /alert/alerts, filtering to active (uncleared) alerts
    by default. Returns the full list of alert dicts.

    exclude_sdt: when True (default), also filters out alerts where
    sdted:"true" -- i.e. alerts on a resource currently in Scheduled
    Downtime. These are suppressed/expected noise rather than alerts
    needing incident reconciliation.

    exclude_warning: when True (default), also filters out severity 2
    (Warning) alerts. LM severity convention: 2=Warning, 3=Error,
    4=Critical. Warnings typically don't generate ServiceNow incidents
    and are excluded from incident-reconciliation pulls by default.
    """
    all_alerts: list[dict] = []
    offset = 0

    base_filter = 'cleared:"false"'
    if exclude_sdt:
        base_filter += ',sdted:"false"'
    if exclude_warning:
        base_filter += ',severity:"3"|"4"'
    filter_str = f"{base_filter},{extra_filter}" if extra_filter else base_filter

    while True:
        query_params = f"?offset={offset}&size={page_size}&filter={filter_str}&sort=startEpoch"
        headers = get_auth_headers("GET", RESOURCE_PATH)
        headers["Content-Type"] = "application/json"
        headers["X-Version"] = "3"

        resp = requests.get(
            f"{BASE_URL}{RESOURCE_PATH}{query_params}", headers=headers, timeout=30
        )

        if resp.status_code != 200:
            sys.exit(f"Request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        items = data.get("data", {}).get("items", data.get("items", []))
        if not items:
            break

        all_alerts.extend(items)

        if len(items) < page_size:
            break
        offset += page_size

    return all_alerts


def write_csv(alerts: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for alert in alerts:
            row = dict(alert)
            # monitorObjectGroups is a list of dicts in the raw response;
            # flatten it to a readable string for CSV.
            groups = row.get("monitorObjectGroups")
            if isinstance(groups, list):
                row["monitorObjectGroups"] = "; ".join(
                    g.get("fullPath", "") for g in groups if isinstance(g, dict)
                )
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch all active alerts from LogicMonitor REST API v3."
    )
    parser.add_argument(
        "--output", default="active_alerts.csv", help="Output CSV path (default: active_alerts.csv)"
    )
    parser.add_argument(
        "--filter",
        help='Additional LM REST API filter clause to AND with the default '
        'cleared/sdted/severity filters, e.g. \'monitorObjectName:"prod*"\'',
    )
    parser.add_argument(
        "--size", type=int, default=300, help="Page size per request (default 300, LM max is typically 1000)"
    )
    parser.add_argument(
        "--include-sdt",
        action="store_true",
        help="Include alerts on resources currently in Scheduled Downtime (excluded by default)",
    )
    parser.add_argument(
        "--include-warning",
        action="store_true",
        help="Include Warning-severity (2) alerts, which don't generate incidents (excluded by default)",
    )
    parser.add_argument(
        "--raw-sample", action="store_true", help="Print one full raw alert object and exit (for exploring fields)"
    )
    args = parser.parse_args()

    alerts = fetch_active_alerts(
        args.filter,
        args.size,
        exclude_sdt=not args.include_sdt,
        exclude_warning=not args.include_warning,
    )

    if args.raw_sample:
        if alerts:
            print(json.dumps(alerts[0], indent=2))
        else:
            print("No alerts returned.")
        return

    write_csv(alerts, args.output)
    print(f"Pulled {len(alerts)} active alerts.")
    print(f"Written to: {args.output}")


if __name__ == "__main__":
    main()