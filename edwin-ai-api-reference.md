# Edwin AI (DEXDA) API — Internal Reference

> **Status:** Unofficial / reverse-engineered  
> **Tenant:** `<your-account>`  
> **Base URL:** `https://<your-account>.dexda.ai`  
> **Last verified:** 2025 (confirmed via live network captures against production tenant)  

---

## ⚠️ Disclaimer

This API is not formally documented by the vendor. All endpoints, request schemas, and field names were captured from live browser network traffic against the Edwin AI dashboard and confirmed against real tenant data. Endpoint paths, filter schemas, and field names may change without notice. If something breaks, check the browser network tab in the Edwin UI first — that's the source of truth.

---

## Authentication

Edwin AI uses **OAuth 2.0 Client Credentials** flow, consistent with LogicMonitor's published Edwin AI / Datadog integration documentation.

### Credentials

Store credentials in environment variables — never hardcode in scripts:

```bash
export EDWIN_CLIENT_ID="<your client ID>"
export EDWIN_CLIENT_SECRET="<your client secret>"
export EDWIN_ACCOUNT="<your-account>"
```

**Scopes granted:** `query_record`, `sdt_read`, `query_records`, `query_aggregate` *(update to reflect your credential)*  

### Token Request

```
POST https://<your-account>.dexda.ai/auth/token
Content-Type: application/x-www-form-urlencoded
```

**Request body:**

```
grant_type=client_credentials&client_id=<CLIENT_ID>&client_secret=<CLIENT_SECRET>
```

**Response:**

```json
{
  "access_token": "<bearer token>",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

**Notes:**
- Token path `/auth/token` taken from LogicMonitor's published Datadog integration doc
- If the tenant returns 401, the most likely fix is adjusting the token path — check the response body for details
- Use the returned `access_token` as a `Bearer` token on all subsequent requests

---

## Endpoints

### 1. Query Records (Bulk)

The primary query endpoint. Used for both **alert** and **insight** record lookups.

```
POST https://<your-account>.dexda.ai/ui/query/records
Authorization: Bearer <token>
Content-Type: application/json
```

**Confirmed via:** live network capture of Edwin AI dashboard.

---

#### 1a. Query Alerts by Source ID (`cf.eventSourceId`)

Looks up Edwin alert records by LogicMonitor source alert ID (DS/ES prefix IDs from LM alert exports).

**Request body:**

```json
{
  "env": { "timezone": "UTC" },
  "source": "alerts",
  "filter": {
    "schemaName": "filterCondition",
    "schemaVersion": 4,
    "expression": {
      "IN": [
        { "field": "cf.eventSourceId", "type": "string" },
        ["DS9710672", "DS9710670", "DS9710674"]
      ]
    }
  },
  "elasticQuery": null,
  "fields": null,
  "order": [],
  "size": 200
}
```

**Key fields in response records:**

| Field | Description | Notes |
|---|---|---|
| `cf.eventSourceId` | LM source alert ID (e.g. `DS9710672`) | **Flat key** — contains literal dots. Do NOT treat as nested dict. |
| `alertDetails.incidentId` | Linked ServiceNow incident number (e.g. `INC1576368`) | Flat key |
| `alertDetails.incidentUrl` | Direct ServiceNow URL to the incident | Flat key |
| `alertDetails.workflowState` | Edwin's workflow state (e.g. `incident-active`, `incident-resolution-stopped`) | Flat key |
| `meta.insightKeyList` | Bare correlation insight key (e.g. `correlation_<uuid>`) | Missing trailing `_<timestamp>` — cannot be used directly with the GET insight endpoint without resolving the full ID first |
| `cf.eventCI` | Configuration item (device name) | Stable across alert re-firings |
| `cf.eventObject` | Datasource instance (e.g. `WinVolumeUsage-C:\`) | Stable across alert re-firings |
| `cf.eventName` | Alert name / datapoint name | Stable across alert re-firings |
| `raw.sourceRecord.internalId` | LMD-style internal ID (e.g. `LMD48049686`) | **Unconfirmed filterable** — stored as JSON string, may not be reachable by filter engine |

> ⚠️ **Important:** All field names above are **flat keys containing literal dots**. They are top-level keys in the JSON response object. Do NOT attempt to walk them as nested dicts (e.g. `record.get("cf", {}).get("eventSourceId")` will fail).

**Pagination / chunking:**
- `size` controls max records returned per request
- For large ID lists, chunk into batches of ~100 IDs per request — behaviour with very large IN arrays is unconfirmed
- The dashboard UI's default query includes a `meta.firstEventTimestamp WITHIN` time-window clause — this is intentionally omitted in targeted ID lookups to avoid silently dropping older alerts

**Response shape:**

```json
{
  "meta": { "count": 3, "recordType": "alerts" },
  "results": [
    {
      "cf.eventSourceId": "DS9710672",
      "alertDetails.incidentId": "INC1576368",
      "alertDetails.incidentUrl": "https://<snow-tenant>/incident/<id>",
      "alertDetails.workflowState": "incident-active",
      "meta.insightKeyList": "correlation_8c4f77f5-c1c5-429c-b47a-b88bcd478660",
      "cf.eventCI": "vmelapp07",
      "cf.eventObject": "WinVolumeUsage-C:\\"
    }
  ]
}
```

---

#### 1b. Query Alerts by CI + Object (Fallback)

Used when the original `cf.eventSourceId` was a superseded intermediate firing — the source ID gets overwritten each time the same alert condition re-fires, but `cf.eventCI` and `cf.eventObject` remain stable across the alert's full lifetime.

**When to use:** source ID lookup returns no results ("Needs Manual Check" cases).

**Request body:**

```json
{
  "env": { "timezone": "UTC" },
  "source": "alerts",
  "filter": {
    "schemaName": "filterCondition",
    "schemaVersion": 4,
    "expression": {
      "AND": [
        { "EQUALS": [{ "field": "cf.eventCI", "type": "string" }, "vmelapp07"] },
        { "EQUALS": [{ "field": "cf.eventObject", "type": "string" }, "WinVolumeUsage-C:\\" ] },
        { "EQUALS": [{ "field": "cf.eventName", "type": "string" }, "PercentUsed"] }
      ]
    }
  },
  "elasticQuery": null,
  "fields": null,
  "order": [],
  "size": 50
}
```

**Notes:**
- `cf.eventName` is optional but narrows results further
- A CI+Object pair can match multiple historical alert records — prefer the record with an `alertDetails.incidentId` present
- Confirmed via live alert showing the same CI/Object/Name across 15 events spanning 9 months while `cf.eventSourceId` changed multiple times

---

#### 1c. Query Insights by Bare Key

Looks up insight (correlation) records using the bare key value seen in an alert's `meta.insightKeyList` field.

**Request body:**

```json
{
  "env": { "timezone": "UTC" },
  "source": "insights",
  "filter": {
    "schemaName": "filterCondition",
    "schemaVersion": 4,
    "expression": {
      "EQUALS": [
        { "field": "meta.rowKey", "type": "string" },
        "correlation_8c4f77f5-c1c5-429c-b47a-b88bcd478660"
      ]
    }
  },
  "elasticQuery": null,
  "fields": null,
  "order": [],
  "size": 10
}
```

**Key fields in response records:**

| Field | Description |
|---|---|
| `insightDetails.incidentId` | Linked ServiceNow incident number |
| `insightDetails.incidentUrl` | Direct ServiceNow URL to the incident |
| `insightDetails.workflowState` | Edwin's workflow state for this insight |

**Notes:**
- Accepts the **bare key** format from `meta.insightKeyList` (e.g. `correlation_<uuid>`) — confirmed that an insight record's `meta.rowKey` matches this bare format exactly
- The insight record's own `_id` has an extra `_<timestamp>` suffix, but `meta.rowKey` matches the bare key — use this endpoint when you only have the bare key
- Use this as a fallback for alerts that have no direct `alertDetails.incidentId` but do have a `meta.insightKeyList` value

---

### 2. Get Single Insight by Full ID

Direct single-record lookup for a known insight, confirmed via live network capture of the Edwin Explore page.

```
GET https://<your-account>.dexda.ai/ui/query/record/insights/{full_insight_id}
Authorization: Bearer <token>
```

**Example:**

```
GET https://<your-account>.dexda.ai/ui/query/record/insights/correlation_8c4f77f5-c1c5-429c-b47a-b88bcd478660_1781746816456
```

**Requires the FULL ID** including the trailing `_<timestamp>` suffix. If you only have the bare key from `meta.insightKeyList`, use the bulk query endpoint (1c) instead.

**Response shape:** single insight record with `insightDetails.*` fields as above.

---

## Filter Schema Reference

All queries to `/ui/query/records` use `filterCondition` schema version 4. Supported expression types:

| Operator | Structure | Example use |
|---|---|---|
| `EQUALS` | `{ "EQUALS": [{ "field": "...", "type": "string" }, "value"] }` | Single field match |
| `IN` | `{ "IN": [{ "field": "...", "type": "string" }, ["v1", "v2"]] }` | Batch ID lookup |
| `AND` | `{ "AND": [expr1, expr2, ...] }` | Multiple conditions |

Field type is always `"string"` for the confirmed fields above.

---

## Known Behaviours & Quirks

### Alert Record Deduplication
Edwin may return multiple alert records for the same source ID (e.g. recurring alerts on the same DS/ES number at different times), sometimes linked to different incidents. When grouping by source ID, join all distinct non-empty values with ` | ` — multiple incident numbers joined together indicates the same source ID had different incident outcomes across occurrences and warrants manual review.


### Source ID Instability
`cf.eventSourceId` is overwritten each time the same alert condition re-fires with a new DS/ES number. For long-lived recurring alerts, the ID visible in a current LM export may not match any Edwin alert record (it's already been superseded). Use the CI+Object fallback query (1b) in these cases.

### Insight Key Format
`meta.insightKeyList` contains the bare correlation key without the trailing timestamp. The insight record's own `_id` has the full key with timestamp. `meta.rowKey` on the insight record matches the bare format — so the bulk query by `meta.rowKey` (1c) works from bare keys, but the direct GET endpoint (2) requires the full ID.

### Time Window
The Edwin dashboard UI applies a default time-window filter (`meta.firstEventTimestamp WITHIN`) on all queries. Scripts that omit this clause will return results across Edwin's full queryable history — this is intentional for reconciliation work, but be aware results may span a longer period than the UI shows.

### raw.sourceRecord.internalId
The LMD-style stable internal ID (e.g. `LMD48049686`) is stored inside a JSON-encoded string at `raw.sourceRecord`. It is likely not filterable via the API's filter engine since it's not a flat top-level key. Use CI+Object fallback instead for stable lookups.

---

## Tooling Built Against This API

| Script | Purpose |
|---|---|
| `edwin_incident_lookup.py` | Batch lookup of Edwin incidents by LM source alert IDs, with CI+Object fallback, insight resolution, and ServiceNow state cross-reference |
| `Edwin.py` | Core Edwin API interaction and reconciliation logic |
| `lm_active_alerts.py` | Exports active LM alerts with CI/Object metadata for use as input to Edwin lookups |

---

## Incident Field Summary (Quick Reference)

```
Alert record fields (all flat keys with literal dots):

  cf.eventSourceId            → LM alert ID (e.g. DS9710672)
  cf.eventCI                  → Device/CI name
  cf.eventObject              → Datasource instance
  cf.eventName                → Alert/datapoint name
  alertDetails.incidentId     → ServiceNow INC number
  alertDetails.incidentUrl    → ServiceNow direct URL
  alertDetails.workflowState  → Edwin state (e.g. incident-active)
  meta.insightKeyList         → Bare correlation key (e.g. correlation_<uuid>)
  raw.sourceRecord.internalId → LMD stable ID — likely not filterable

Insight record fields:

  meta.rowKey                         → Bare key (matches alert's meta.insightKeyList)
  insightDetails.incidentId           → ServiceNow INC number
  insightDetails.incidentUrl          → ServiceNow direct URL
  insightDetails.workflowState        → Edwin state
```

---

*This document was produced from live API captures and script development against your Edwin AI tenant. Review against live network traffic if behaviour changes unexpectedly.*
