# Torn Foreign Stock Collector

Collects watched travel stock on the existing five-minute GitHub Actions schedule
(`2-59/5 * * * *`). Uses Python 3.12 and only the standard library.

## Providers

YATA is always polled. Prometheus (`https://api.prombot.co.uk/api/travel`) is an
optional second provider. The scheduled workflow enables it by default; set the
repository Actions variable `ENABLE_PROMETHEUS` to `false` to disable it. For local
runs, set the environment variable `ENABLE_PROMETHEUS=true` to enable it, then run
`python collector.py`. Local runs default to YATA alone.

For each country, the collector selects the usable provider snapshot with the
largest valid country `update` timestamp; YATA wins ties. Each request has a
30-second timeout. Failure or an unusable response from either provider does not
block the other. Missing countries/items and invalid quantities never become
zero. Missing items retain their last known quantity; they are not filled from
an older country snapshot. Older fallback snapshots cannot roll state backward.
Partial valid responses are processed per item instead of the old global
minimum-eight-items guard. If all providers fail, the collector exits with an
error and preserves the latest snapshot and confirmation state. The workflow
still commits polling-health logs from failed runs.

China watches **Blank Casino Chips**, **Panda Plushie**, **Pangolin Scales**, and
**Peony**. Hawaii watches **Shark Fin**. UAE also watches **Natural Pearls** in
addition to **Tribulus Omanense** and **Camel Plushie**. An item absent from both
providers stays unknown (`null`) until actually observed.

## Compatibility and logs

- `data/history.csv` keeps its existing columns and appends the selected fresh
  observations. Unchanged or older source timestamps do not duplicate history.
- `data/transitions.csv` keeps its existing columns and appends confirmed events.
  Existing rows in both CSV files are never rewritten.
- `data/latest.json` keeps all existing fields and types. `stock` remains the
  latest observed quantities, including unconfirmed changes. Added `sources`
  maps countries to their selected provider and `source_urls` lists endpoints.
  The top-level `source` is `YATA`, `Prometheus`, or `mixed`; for mixed snapshots,
  the legacy `source_url` remains YATA and the country map is authoritative.
- `data/provider_observations.jsonl` appends one record per received JSON response,
  including repeated responses: provider `source`, `collection_timestamp`, and
  only watched item `observations` (`country`, `item`, `source_update`, `quantity`,
  and `nextRestock` when supplied). No full payload or unrelated metadata is saved.
  Absent items have no observation entry; missing/null quantities and source
  timestamps remain `null`, never zero. An omitted `nextRestock` remains absent,
  while an explicit `null` is preserved. An empty list records no watched items.
- `data/source_health.csv` records every enabled-provider poll, its collection
  timestamp, success, parsed item count, fresh country count, and error. Success
  means a usable response; `fresh_countries=0` identifies unchanged/stale data.
- `data/collector_state.json` persists confirmed quantities, pending candidates,
  per-item timestamps, and provider timestamp watermarks between scheduled runs.
  The first upgraded run initializes it from the existing latest snapshot.

## Transition confirmation

Only **0 to positive** is a restock, and only **positive to 0** is a stockout.
Positive-to-higher-positive quantities are ordinary observations. A first-ever
quantity establishes a baseline without emitting an event.

A single-source state change becomes a candidate. It is confirmed by the next
strictly newer selected item observation in the same zero/positive state, or
immediately by two providers agreeing in a poll, each with an advancing timestamp
newer than the item's last processed observation and its own provider watermark.
Agreement concerns zero versus positive, not exact quantities. A fresh return to
the confirmed state cancels the candidate. Missing or cached observations neither
confirm nor cancel it. Distinct providers count as independent sources here;
the collector cannot verify whether their underlying contributors overlap.

Confirmed events retain the candidate's first observation as the upper bound
and the last confirmed item observation as the lower bound; confirmation delay
does not change the estimated transition interval. The source and quantity in
the transition row describe that first candidate observation.

## Checks

Run `python -m py_compile collector.py test_collector.py` and
`python -m unittest -v`. Tests isolate their output from repository data and cover
confirmation, stale/cached responses, provider failures, parsing, compatibility,
and country selection. No dependencies or API credentials are required.
