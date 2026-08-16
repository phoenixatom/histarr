# Data model

Histarr emits one combined event per Tautulli session joined to at most one Plex history row, plus unmatched rows from either source.

## Sources and matching

- `source`: `plex+tautulli`, `tautulli_only`, or `plex_only`.
- `match_method`, `match_confidence`, and `match_time_delta_seconds` describe a one-to-one join.
- `metadata_source` and `genre_source` describe enrichment provenance.

## Progress and time

- `duration_seconds` is media runtime from item metadata, never Tautulli session duration.
- `progress_seconds` uses an exact Tautulli view offset when available; otherwise it can be estimated from completion percentage and known runtime.
- `progress_is_estimated` distinguishes estimates from exact offsets.
- `active_play_seconds` is play time excluding recorded pauses.
- `analysis_included=false` preserves an anomalous event while excluding it from clean time totals.

## Completion and repeats

- `completion_percent` is capped to 0–100; missing remains unknown.
- `completed_90_percent` is Histarr's fixed analytical threshold.
- `tautulli_watched_status` preserves Tautulli's fractional value separately.
- `is_rewatch` requires a completed event after an earlier completed event for the same media identity.

## Binges and bulk history

A binge contains at least two distinct episodes of one show in a consecutive run with no more than four hours between event starts. Resume events do not increase its episode count.

Three or more distinct Plex-only episodes recorded with no more than ten seconds between consecutive rows are labeled as a likely mark-watched/import cluster. They remain in every output but receive `binge_exclusion_reason=bulk_plex_history_timestamp_cluster` and do not count as a binge.

The complete field descriptions are embedded in `combined_history.json` and `summary.json`.
