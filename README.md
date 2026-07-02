# Edwin AI – LogicMonitor Alert & ServiceNow Incident Reconciliation

This toolset cross-references active LogicMonitor alerts against Edwin AI and ServiceNow to identify gaps — alerts with no linked incident, incidents in the wrong state, or alerts that Edwin has no record of.

---

## Contents

| File | Purpose |
|---|---|
| `lm_active_alerts.py` | Pulls all active alerts from LogicMonitor REST API and exports to CSV |
| `edwin_incident_lookup.py` | Looks up Edwin AI incident records for each alert and cross-references ServiceNow state |
| `edwin-ai-api-reference.md` | Internal API reference for the Edwin AI (DEXDA) endpoints used by these scripts |

---

## Prerequisites

### Python version
Python 3.10 or later is required.

### Install dependencies
```bash
pip install requests openpyxl
```

### Environment variables
All credentials are read from environment variables. **Never hardcode credentials in scripts.**

Set the following before running:

```bash
# LogicMonitor credentials (for lm_active_alerts.py)
export LM_ACCOUNT="<your-account>"
export LM_ACCESS_ID="<your-access-id>"
export LM_ACCESS_KEY="<your-access-key>"

# Edwin AI credentials (for edwin_incident_lookup.py)
export EDWIN_ACCOUNT="<your-account>"
export EDWIN_CLIENT_ID="<your-client-id>"
export EDWIN_CLIENT_SECRET="<your-client-secret>"
```

> On Windows, use `set` instead of `export`, or set them via System Properties → Environment Variables.

---

## Step-by-Step Workflow

### Step 1 — Export active alerts from LogicMonitor

Run `lm_active_alerts.py` to pull all currently active, uncleared alerts from LogicMonitor. By default this excludes alerts in Scheduled Downtime (SDT) and Warning-severity alerts, as these do not generate ServiceNow incidents.

```bash
python3 lm_active_alerts.py --output active_alerts.csv
```

This produces `active_alerts.csv` containing one row per active alert, with columns including:

- `id` — the LM source alert ID (e.g. `DS0000000`, `ES0000000`)
- `monitorObjectName` — the monitored device / CI name
- `instanceName` — the datasource instance
- `dataPointName` — the datapoint that breached threshold
- `severity`, `startEpoch`, `acked`, `sdted`

**Optional flags:**

| Flag | Effect |
|---|---|
| `--include-sdt` | Include alerts currently in Scheduled Downtime |
| `--include-warning` | Include Warning-severity alerts |
| `--filter 'monitorObjectName:"prod*"'` | Narrow to a subset of devices |
| `--size 500` | Change page size (default 300) |
| `--raw-sample` | Print one raw alert object to explore available fields |

---

### Step 2 — Export ServiceNow incidents (optional but recommended)

Export an incident list from ServiceNow as an `.xlsx` file. This is used to cross-reference Edwin's view of incident state against what ServiceNow actually shows.

The export must include at minimum:

| Column | Used for |
|---|---|
| `Number` | Incident number (e.g. `INC0000000`) |
| `State` | Current incident state (e.g. New, In Progress, Resolved) |
| `Description(description)` | *(Optional)* Used to match alerts via embedded LM alert URLs |
| `Configuration item` | *(Optional)* Used for CI-based fallback matching |
| `Short description` | *(Optional)* Used for CI-based fallback matching |

Save the export as a `.xlsx` file, e.g. `snow_export.xlsx`.

> If you skip this step, the output will still show Edwin state — you just won't see the ServiceNow state column.

---

### Step 3 — Run the Edwin incident lookup

Run `edwin_incident_lookup.py`, pointing it at the active alerts CSV from Step 1 and the ServiceNow export from Step 2.

```bash
python3 edwin_incident_lookup.py \
    --active-alerts-csv active_alerts.csv \
    --snow-export snow_export.xlsx \
    --resolve-insights \
    --excel results.xlsx
```

This will:

1. Look up every alert ID from `active_alerts.csv` in Edwin AI
2. For alerts with no direct incident link but a correlated insight key, resolve the incident via the insight record (`--resolve-insights`)
3. For alerts Edwin found but has no incident linked, attempt a fallback match against the ServiceNow export using CI + datasource
4. Cross-reference every found incident number against the ServiceNow export to show current SNOW state
5. Write results to `results.xlsx`

**Output file — `results.xlsx`:**

The workbook contains two sheets:

**Sheet 1: Incident Lookup**

| Column | Description |
|---|---|
| Source ID | LM alert ID |
| Incident | Linked ServiceNow incident number |
| Edwin State | Edwin's workflow state (e.g. `incident-active`) |
| ServiceNow State | Actual current state from SNOW export |
| Link | Direct URL to the ServiceNow incident |
| Insight Key | Correlation insight key if present |
| Status | `Linked`, `Linked via insight`, `Linked via SNOW match`, `No incident linked`, `Multiple incidents seen` |

**Sheet 2: Needs Manual Check** *(only present if some IDs were not found)*

Lists any alert IDs that Edwin had no record of at all, with a direct LogicMonitor search link for each. These are typically alerts that have already cleared, or recurring alerts where the source ID was superseded by a later re-firing.

---

### Step 4 — Resolve "Needs Manual Check" items (if required)

If Sheet 2 contains entries, these alerts were not found in Edwin via their source ID. This usually means the alert re-fired and the source ID changed — Edwin's record has a different ID for the same underlying condition.

To resolve these:

1. Open the LogicMonitor search link for each ID in Sheet 2
2. Note the **device name (CI)** and **datasource instance (Object)** shown for that alert
3. Create a CSV file (e.g. `manual_check.csv`) with the following format:

```
Source ID,CI,Object
DS0000000,<device-name>,<datasource-instance>
```

4. Re-run with the `--ci-object-file` flag:

```bash
python3 edwin_incident_lookup.py \
    --ci-object-file manual_check.csv \
    --snow-export snow_export.xlsx \
    --excel results_resolved.xlsx
```

Results from the manual lookup are merged into the output alongside the automatically resolved rows.

---

### Step 5 — Review results

Open `results.xlsx` and review:

- **`No incident linked`** — Edwin has an alert record but no ServiceNow incident is attached. Investigate whether an incident should have been raised.
- **`Not in SNOW export`** — Edwin shows an incident number but it wasn't in the ServiceNow export. Usually means the incident was closed/resolved before the export was taken.
- **`Multiple incidents seen`** — the same source ID was linked to more than one incident across its history. Worth a manual check.
- **Edwin State vs ServiceNow State mismatch** — Edwin shows `incident-active` but ServiceNow shows `Resolved` (or vice versa). These state mismatches are the primary reconciliation finding.

---

## Running a Quick Lookup (without a full CSV export)

You can look up individual alert IDs directly without running Step 1 first:

```bash
python3 edwin_incident_lookup.py DS0000001 DS0000002 DS0000003
```

Or from a text file (one ID per line):

```bash
python3 edwin_incident_lookup.py --file source_ids.txt
```

---

## Troubleshooting

**Token request returns 401**
Check that `EDWIN_CLIENT_ID` and `EDWIN_CLIENT_SECRET` are set correctly. If the error response body mentions a different token path, update `TOKEN_PATH` in `edwin_incident_lookup.py`.

**LM API returns 401**
Verify `LM_ACCESS_ID` and `LM_ACCESS_KEY` are set. If using a Bearer token instead, set `USE_BEARER_TOKEN = True` in `lm_active_alerts.py` and set `LM_BEARER_TOKEN`.

**No alerts returned from LM**
Try `--include-sdt` and `--include-warning` to confirm the API is responding. If still empty, check the account name and credentials.

**Large number of "Needs Manual Check" IDs**
This is expected if looking up alerts that have been active for a long time and have re-fired multiple times. Use `--active-alerts-csv` as the primary input — it provides the CI/Object metadata needed for automatic fallback resolution without a manual CSV.

**Excel file not written**
Ensure `openpyxl` is installed (`pip install openpyxl`) and that the output path is writable.

---

## Notes

- Edwin AI's API is not formally documented by the vendor. Endpoints and field names were confirmed via live network capture. See `edwin-ai-api-reference.md` for full details.
- All field names in Edwin API responses are **flat keys containing literal dots** (e.g. `alertDetails.incidentId`) — they are not nested objects.
- The Edwin API credential used by these scripts requires scopes: `query_record`, `sdt_read`, `query_records`, `query_aggregate`.
- Credentials should be rotated and expiry tracked — check with your administrator for the current credential expiry date.
