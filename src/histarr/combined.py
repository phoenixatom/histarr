# SPDX-License-Identifier: GPL-3.0-only
"""Combined event enrichment and stable analytical output writers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .analytics import add_sequence_metrics, build_insights_markdown, build_summary
from .core import tabular_value, utc_now
from .matching import enrich_with_show_metadata, history_matches, make_combined_event
from .plex import fetch_plex_metadata
from .tautulli import fetch_tautulli_metadata, metadata_miss_is_recent


def build_combined_events(
    plex_results: list[dict[str, Any]],
    tautulli_history: list[dict[str, Any]],
    tautulli_server_info: dict[str, Any],
    client_id: str,
    *,
    metadata_cache: dict[str, Any] | None = None,
    tautulli_api_url_value: str | None = None,
    tautulli_api_key: str | None = None,
    metadata_workers: int = 4,
    refresh_metadata: bool = False,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], dict[str, Any]]:
    tautulli_server_id = str(tautulli_server_info.get("pms_identifier") or "")
    plex_entries: list[dict[str, Any]] = []
    for result in plex_results:
        for record in result.get("records", []):
            plex_entries.append(
                {
                    "server": result["server"],
                    "server_id": str(result["server"].get("clientIdentifier") or ""),
                    "record": record,
                }
            )

    cache = metadata_cache if isinstance(metadata_cache, dict) else {}
    metadata_by_server: dict[str, dict[str, dict[str, Any]]] = {}
    plex_unavailable_by_server: dict[str, set[str]] = {}
    metadata_errors: list[str] = []
    metadata_stats: dict[str, Any] = {
        "requested_items": 0,
        "cached_items_reused": 0,
        "plex_fetched_items": 0,
        "plex_unavailable_items": 0,
        "show_metadata_requested": 0,
        "show_metadata_fetched": 0,
        "show_metadata_unavailable": 0,
        "events_enriched_from_show_metadata": 0,
        "tautulli_attempted_items": 0,
        "tautulli_recovered_items": 0,
        "unresolved_items": 0,
    }
    t_keys = [str(row.get("rating_key")) for row in tautulli_history if row.get("rating_key")]
    first_server_id = str(
        (plex_results[0].get("server") or {}).get("clientIdentifier") or ""
    ) if plex_results else ""
    effective_tautulli_server_id = tautulli_server_id or first_server_id
    for result in plex_results:
        server_id = str(result["server"].get("clientIdentifier") or "")
        keys = [str(row.get("ratingKey")) for row in result.get("records", []) if row.get("ratingKey")]
        if server_id == effective_tautulli_server_id:
            keys.extend(t_keys)
        keys = list(dict.fromkeys(keys))
        metadata_stats["requested_items"] += len(keys)
        server_cache = cache.setdefault(server_id, {})
        if not isinstance(server_cache, dict):
            server_cache = {}
            cache[server_id] = server_cache
        reusable = {
            key
            for key in keys
            if isinstance(server_cache.get(key), dict)
            and server_cache[key].get("_exporter_source") != "not_found"
        }
        metadata_stats["cached_items_reused"] += 0 if refresh_metadata else len(reusable)
        missing = keys if refresh_metadata else [
            key
            for key in keys
            if key not in reusable
            and not (
                isinstance(server_cache.get(key), dict)
                and metadata_miss_is_recent(server_cache[key])
            )
        ]
        if missing:
            print(
                f"Fetching current Plex metadata for {result['server'].get('name') or 'server'} "
                f"({len(missing)} uncached item(s))..."
            )
        metadata, errors, plex_unavailable = fetch_plex_metadata(result, client_id, missing)
        for value in metadata.values():
            if isinstance(value, dict):
                value.setdefault("_exporter_source", "plex")
                value.setdefault("_exporter_checked_at", utc_now())
        server_cache.update(metadata)
        metadata_stats["plex_fetched_items"] += len(metadata)
        plex_unavailable_by_server.setdefault(server_id, set()).update(plex_unavailable)
        metadata_errors.extend(f"{result['server'].get('name')}: {error}" for error in errors)

        show_keys = {
            str(value.get("grandparentRatingKey"))
            for value in server_cache.values()
            if isinstance(value, dict)
            and value.get("type") == "episode"
            and value.get("grandparentRatingKey")
        }
        if server_id == effective_tautulli_server_id:
            show_keys.update(
                str(row.get("grandparent_rating_key"))
                for row in tautulli_history
                if row.get("media_type") == "episode" and row.get("grandparent_rating_key")
            )
        show_keys.discard("")
        metadata_stats["show_metadata_requested"] += len(show_keys)
        known_unavailable_shows = {
            key
            for key in show_keys
            if isinstance(server_cache.get(key), dict)
            and server_cache[key].get("_exporter_source") == "not_found"
            and server_cache[key].get("_exporter_kind") == "show"
        }
        show_missing = [
            key
            for key in sorted(show_keys)
            if refresh_metadata
            or (
                not (
                    isinstance(server_cache.get(key), dict)
                    and server_cache[key].get("_exporter_source") != "not_found"
                )
                and not (
                    isinstance(server_cache.get(key), dict)
                    and metadata_miss_is_recent(server_cache[key])
                )
            )
        ]
        if show_missing:
            print(
                f"Fetching show-level metadata for {result['server'].get('name') or 'server'} "
                f"({len(show_missing)} show(s))..."
            )
            show_metadata, show_errors, show_unavailable = fetch_plex_metadata(
                result, client_id, show_missing
            )
            server_cache.update(show_metadata)
            metadata_stats["show_metadata_fetched"] += len(show_metadata)
            known_unavailable_shows.update(show_unavailable)
            for key in show_unavailable:
                server_cache[key] = {
                    "_exporter_source": "not_found",
                    "_exporter_checked_at": utc_now(),
                    "_exporter_plex_status": "not_found",
                    "_exporter_kind": "show",
                }
            metadata_errors.extend(
                f"{result['server'].get('name')}: show {error}" for error in show_errors
            )
        metadata_stats["show_metadata_unavailable"] += len(known_unavailable_shows)

    fallback_server_id = effective_tautulli_server_id or next(iter(cache), "")
    fallback_cache = cache.setdefault(fallback_server_id, {}) if fallback_server_id else {}
    unresolved_for_tautulli = [
        key
        for key in dict.fromkeys(t_keys)
        if not (
            isinstance(fallback_cache.get(key), dict)
            and fallback_cache[key].get("_exporter_source") != "not_found"
        )
        and not (
            isinstance(fallback_cache.get(key), dict)
            and metadata_miss_is_recent(fallback_cache[key])
        )
    ]
    if unresolved_for_tautulli and tautulli_api_url_value and tautulli_api_key:
        print(f"Recovering {len(unresolved_for_tautulli)} missing metadata item(s) from Tautulli...")
        metadata_stats["tautulli_attempted_items"] = len(unresolved_for_tautulli)
        recovered, recovery_errors = fetch_tautulli_metadata(
            tautulli_api_url_value,
            tautulli_api_key,
            unresolved_for_tautulli,
            workers=metadata_workers,
        )
        for key, value in recovered.items():
            if key in plex_unavailable_by_server.get(fallback_server_id, set()):
                value["_exporter_plex_status"] = "not_found"
        fallback_cache.update(recovered)
        metadata_stats["tautulli_recovered_items"] = len(recovered)
        metadata_errors.extend(recovery_errors)
        for key in unresolved_for_tautulli:
            if key not in recovered:
                fallback_cache[key] = {
                    "_exporter_source": "not_found",
                    "_exporter_checked_at": utc_now(),
                    "_exporter_plex_status": (
                        "not_found"
                        if key in plex_unavailable_by_server.get(fallback_server_id, set())
                        else "unknown"
                    ),
                    "_exporter_tautulli_status": "not_found",
                }

    all_requested: set[tuple[str, str]] = set()
    for result in plex_results:
        server_id = str(result["server"].get("clientIdentifier") or "")
        keys = [str(row.get("ratingKey")) for row in result.get("records", []) if row.get("ratingKey")]
        if server_id == effective_tautulli_server_id:
            keys.extend(t_keys)
        for key in keys:
            all_requested.add((server_id, key))
        server_cache = cache.get(server_id, {})
        metadata_by_server[server_id] = {
            key: value
            for key, value in server_cache.items()
            if isinstance(value, dict) and value.get("_exporter_source") != "not_found"
        }
    if fallback_server_id and fallback_server_id not in metadata_by_server:
        metadata_by_server[fallback_server_id] = {
            key: value
            for key, value in fallback_cache.items()
            if isinstance(value, dict) and value.get("_exporter_source") != "not_found"
        }
    metadata_stats["unresolved_items"] = sum(
        1
        for server_id, key in all_requested
        if key not in metadata_by_server.get(server_id, {})
    )
    metadata_stats["plex_unavailable_items"] = sum(
        1
        for server_id, key in all_requested
        if isinstance(cache.get(server_id, {}).get(key), dict)
        and (
            cache[server_id][key].get("_exporter_plex_status") == "not_found"
            or (
                cache[server_id][key].get("_exporter_source") == "not_found"
                and "_exporter_plex_status" not in cache[server_id][key]
            )
        )
    )

    matches, _matched_t, matched_p = history_matches(plex_entries, tautulli_history, tautulli_server_id)
    events: list[dict[str, Any]] = []
    for t_index, t_row in enumerate(tautulli_history):
        match_info = matches.get(t_index)
        plex_entry = plex_entries[match_info[0]] if match_info else None
        server_id = str(
            (plex_entry or {}).get("server_id")
            or effective_tautulli_server_id
            or t_row.get("server_id")
            or ""
        )
        key = str(t_row.get("rating_key") or ((plex_entry or {}).get("record") or {}).get("ratingKey") or "")
        item_metadata = metadata_by_server.get(server_id, {}).get(key, {})
        show_key = str(
            t_row.get("grandparent_rating_key")
            or ((plex_entry or {}).get("record") or {}).get("grandparentRatingKey")
            or item_metadata.get("grandparentRatingKey")
            or ""
        )
        show_metadata = metadata_by_server.get(server_id, {}).get(show_key, {}) if show_key else {}
        metadata = enrich_with_show_metadata(item_metadata, show_metadata)
        if metadata.get("_genre_source") == "show_metadata":
            metadata_stats["events_enriched_from_show_metadata"] += 1
        match_tuple = (match_info[1], match_info[2], match_info[3]) if match_info else None
        events.append(make_combined_event(t_row, plex_entry, metadata, match_tuple, effective_tautulli_server_id))
    for p_index, plex_entry in enumerate(plex_entries):
        if p_index in matched_p:
            continue
        server_id = plex_entry["server_id"]
        key = str(plex_entry["record"].get("ratingKey") or "")
        item_metadata = metadata_by_server.get(server_id, {}).get(key, {})
        show_key = str(
            plex_entry["record"].get("grandparentRatingKey")
            or item_metadata.get("grandparentRatingKey")
            or ""
        )
        show_metadata = metadata_by_server.get(server_id, {}).get(show_key, {}) if show_key else {}
        metadata = enrich_with_show_metadata(item_metadata, show_metadata)
        if metadata.get("_genre_source") == "show_metadata":
            metadata_stats["events_enriched_from_show_metadata"] += 1
        events.append(make_combined_event(None, plex_entry, metadata, None, effective_tautulli_server_id))
    add_sequence_metrics(events)
    return events, metadata_errors, cache, metadata_stats


def write_combined_exports(
    output_dir: Path,
    user: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    metadata_errors: list[str],
    metadata_stats: dict[str, Any] | None = None,
    incremental_info: dict[str, Any] | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "combined_history.json"
    jsonl_path = output_dir / "combined_history.jsonl"
    csv_path = output_dir / "combined_history.csv"
    summary_path = output_dir / "summary.json"
    insights_path = output_dir / "insights.md"
    schema = {
        "completion_percent": "Tautulli percent_complete, or view_offset/runtime when percent_complete is unavailable.",
        "duration_seconds": "Media runtime from item metadata; never Tautulli session duration.",
        "progress_seconds": "Exact view_offset where available, otherwise estimated from completion percent and known runtime.",
        "active_play_seconds": "Tautulli play duration excluding recorded pauses.",
        "pause_share_percent": "Paused seconds divided by session wall-clock seconds.",
        "active_play_vs_runtime_percent": "Active session time as a share of the media runtime; seeking can make this differ from completion.",
        "completion_band": "Fixed analytical bands; completed means at least 90%.",
        "source": "plex+tautulli, tautulli_only, or plex_only.",
        "metadata_source": "plex, plex_show, tautulli, or history_only; identifies enrichment provenance.",
        "genre_source": "Whether genres came from item metadata, inherited show metadata, or are unavailable.",
        "tautulli_watched_status": "Original fractional Tautulli status (0, 0.25, 0.5, 0.75, or 1), preserved without boolean coercion.",
        "analysis_included": "False for stale/implausibly long sessions excluded from clean watch-time statistics.",
        "anomaly_reasons": "Machine-readable reasons a session was excluded from clean time analysis.",
        "match_confidence": "Confidence in the one-to-one Plex/Tautulli event join.",
        "is_rewatch": "Completed play after an earlier completed play of the same media in this dataset.",
        "binge_session_id": "Assigned to runs containing 2+ distinct episodes of one show within four hours; resumes and bulk history updates do not count.",
        "is_bulk_history_cluster": "True for near-simultaneous Plex-only episode rows produced by a likely mark-watched/import operation.",
        "binge_exclusion_reason": "Reason an event was retained but excluded from binge analysis.",
    }
    payload = {
        "schema_version": "1.3",
        "generated_at": utc_now(),
        "account": {"id": user.get("id"), "username": user.get("username")},
        "privacy_notice": "Events may contain usernames, IP addresses, player names, and viewing behavior.",
        "field_definitions": schema,
        "incremental_update": incremental_info or {},
        "event_count": len(events),
        "events": events,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    preferred = [
        "event_id", "source", "metadata_source", "genre_source", "match_method", "match_confidence", "match_time_delta_seconds",
        "event_at_local", "event_at_utc", "started_at_utc", "stopped_at_utc", "plex_viewed_at_utc",
        "local_date", "local_month", "local_weekday", "local_hour", "time_of_day",
        "media_type", "full_title", "title", "show_title", "season_number", "episode_number",
        "year", "originally_available_at", "library", "genres", "content_rating",
        "duration_seconds", "runtime_source", "progress_seconds", "progress_source", "progress_is_estimated",
        "completion_percent", "completion_band", "completed_90_percent", "tautulli_watched_status",
        "tautulli_watched", "remaining_seconds", "active_play_seconds", "active_play_source",
        "tautulli_session_duration_seconds", "paused_seconds", "wall_clock_seconds", "pause_share_percent",
        "active_play_vs_runtime_percent", "is_anomalous", "anomaly_reasons", "analysis_included",
        "play_number_for_item", "completed_watch_number_for_item", "is_rewatch",
        "minutes_since_previous_event", "binge_session_id", "binge_episode_count", "binge_event_count",
        "is_bulk_history_cluster", "bulk_history_cluster_id", "binge_exclusion_reason",
        "platform", "product", "player", "location", "transcode_decision", "video_decision",
        "audio_decision", "video_resolution", "video_codec", "audio_codec", "container",
        "bitrate_kbps", "audio_channels", "user", "user_id", "ip_address", "secure", "relayed",
        "server_name", "server_id", "rating_key", "guid", "external_guids", "plex_history_key",
        "tautulli_row_id", "summary", "studio", "directors", "writers", "actors", "countries",
    ]
    available = {key for event in events for key in event}
    fields = [key for key in preferred if key in available]
    fields.extend(sorted(available - set(fields)))
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for event in events:
            writer.writerow({key: tabular_value(event.get(key)) for key in fields})

    summary = build_summary(
        events,
        metadata_errors=metadata_errors,
        metadata_stats=metadata_stats,
        incremental_info=incremental_info,
    )
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    insights_path.write_text(build_insights_markdown(summary), encoding="utf-8")
    return csv_path, json_path, jsonl_path, summary_path, insights_path
