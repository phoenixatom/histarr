# SPDX-License-Identifier: GPL-3.0-only
"""Sequence metrics, statistical summaries, and deterministic insights."""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from .core import (
    BULK_HISTORY_MAX_GAP_SECONDS, BULK_HISTORY_MIN_DISTINCT_EPISODES,
    integer, normalized_text, number, utc_now,
)


def add_sequence_metrics(events: list[dict[str, Any]]) -> None:
    events.sort(key=lambda event: event.get("event_at_utc") or "9999")
    item_plays: dict[str, int] = {}
    completed_plays: dict[str, int] = {}
    previous_epoch: float | None = None
    for event in events:
        event["binge_session_id"] = ""
        event["binge_episode_count"] = None
        event["binge_event_count"] = None
        event["is_bulk_history_cluster"] = False
        event["bulk_history_cluster_id"] = ""
        event["binge_exclusion_reason"] = ""
        identity = str(event.get("media_identity") or "")
        item_plays[identity] = item_plays.get(identity, 0) + 1
        event["play_number_for_item"] = item_plays[identity]
        if event.get("completed_90_percent"):
            completed_plays[identity] = completed_plays.get(identity, 0) + 1
            event["completed_watch_number_for_item"] = completed_plays[identity]
            event["is_rewatch"] = completed_plays[identity] > 1
        current_epoch = None
        if event.get("event_at_utc"):
            try:
                current_epoch = dt.datetime.fromisoformat(str(event["event_at_utc"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        if previous_epoch is not None and current_epoch is not None:
            event["minutes_since_previous_event"] = round((current_epoch - previous_epoch) / 60, 3)
        if current_epoch is not None:
            previous_epoch = current_epoch

    # Plex can create many history rows at effectively the same instant when a
    # season/show is marked watched or history is imported. Three or more
    # distinct Plex-only episodes separated by no more than ten seconds cannot
    # represent normal playback starts, so retain and label them but exclude
    # them from binge analysis.
    bulk_run: list[dict[str, Any]] = []
    bulk_show = ""
    bulk_previous_time: dt.datetime | None = None
    bulk_number = 0

    def finalize_bulk() -> None:
        nonlocal bulk_number
        unique_episodes = {
            str(member.get("media_identity") or member.get("full_title") or "")
            for member in bulk_run
        }
        unique_episodes.discard("")
        if len(unique_episodes) >= BULK_HISTORY_MIN_DISTINCT_EPISODES:
            bulk_number += 1
            cluster_id = f"bulk-{bulk_number:04d}"
            for member in bulk_run:
                member["is_bulk_history_cluster"] = True
                member["bulk_history_cluster_id"] = cluster_id
                member["binge_exclusion_reason"] = "bulk_plex_history_timestamp_cluster"

    for event in events:
        show = normalized_text(event.get("show_title")) if event.get("media_type") == "episode" else ""
        current_time = None
        if event.get("event_at_utc"):
            try:
                current_time = dt.datetime.fromisoformat(
                    str(event["event_at_utc"]).replace("Z", "+00:00")
                )
            except ValueError:
                pass
        gap = (
            (current_time - bulk_previous_time).total_seconds()
            if current_time is not None and bulk_previous_time is not None
            else None
        )
        eligible = bool(show and current_time is not None and event.get("source") == "plex_only")
        continues = bool(
            eligible
            and bulk_run
            and show == bulk_show
            and gap is not None
            and 0 <= gap <= BULK_HISTORY_MAX_GAP_SECONDS
        )
        if continues:
            bulk_run.append(event)
        else:
            finalize_bulk()
            bulk_run = [event] if eligible else []
            bulk_show = show if eligible else ""
        bulk_previous_time = current_time if bulk_run else None
    finalize_bulk()

    run: list[dict[str, Any]] = []
    run_show = ""
    previous_time: dt.datetime | None = None
    binge_number = 0

    def finalize() -> None:
        nonlocal binge_number
        unique_episodes = {
            str(member.get("media_identity") or member.get("full_title") or "")
            for member in run
        }
        unique_episodes.discard("")
        if len(unique_episodes) >= 2:
            binge_number += 1
            binge_id = f"binge-{binge_number:04d}"
            for member in run:
                member["binge_session_id"] = binge_id
                member["binge_episode_count"] = len(unique_episodes)
                member["binge_event_count"] = len(run)

    for event in events:
        show = (
            normalized_text(event.get("show_title"))
            if event.get("media_type") == "episode" and not event.get("is_bulk_history_cluster")
            else ""
        )
        current_time = None
        if event.get("event_at_utc"):
            try:
                current_time = dt.datetime.fromisoformat(str(event["event_at_utc"]).replace("Z", "+00:00"))
            except ValueError:
                pass
        gap_ok = previous_time is not None and current_time is not None and (current_time - previous_time).total_seconds() <= 4 * 3600
        if show and run and show == run_show and gap_ok:
            run.append(event)
        else:
            finalize()
            run = [event] if show else []
            run_show = show
        previous_time = current_time if run else None
    finalize()


def dimension_summary(
    events: list[dict[str, Any]],
    field: str,
    *,
    multi: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        raw_value = event.get(field)
        values = raw_value if multi and isinstance(raw_value, list) else [raw_value]
        for value in values:
            label = str(value) if value not in (None, "") else "Unknown"
            groups.setdefault(label, []).append(event)
    output: list[dict[str, Any]] = []
    for label, rows in groups.items():
        completions = [number(row.get("completion_percent")) for row in rows]
        known = [value for value in completions if value is not None]
        raw_active_seconds = sum(number(row.get("active_play_seconds")) or 0 for row in rows)
        active_seconds = sum(
            number(row.get("active_play_seconds")) or 0
            for row in rows
            if row.get("analysis_included", True)
        )
        output.append(
            {
                "value": label,
                "events": len(rows),
                "event_share_percent": round(len(rows) / len(events) * 100, 2) if events else 0,
                "unique_media": len({str(row.get("media_identity")) for row in rows}),
                "completed_events_90_percent": sum(bool(row.get("completed_90_percent")) for row in rows),
                "average_completion_percent": round(sum(known) / len(known), 2) if known else None,
                "active_play_hours": round(active_seconds / 3600, 3),
                "raw_active_play_hours": round(raw_active_seconds / 3600, 3),
            }
        )
    return sorted(output, key=lambda row: (-row["events"], row["value"]))[:limit]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def build_summary(
    events: list[dict[str, Any]],
    *,
    metadata_errors: list[str],
    metadata_stats: dict[str, Any] | None = None,
    incremental_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completions = [number(event.get("completion_percent")) for event in events]
    known_completions = [value for value in completions if value is not None]
    active_seconds = [number(event.get("active_play_seconds")) for event in events]
    raw_known_active = [value for value in active_seconds if value is not None]
    known_active = [
        value
        for event, value in zip(events, active_seconds)
        if value is not None and event.get("analysis_included", True)
    ]
    paused_seconds = [number(event.get("paused_seconds")) for event in events]
    raw_known_paused = [value for value in paused_seconds if value is not None]
    known_paused = [
        value
        for event, value in zip(events, paused_seconds)
        if value is not None and event.get("analysis_included", True)
    ]
    anomalous_events = [event for event in events if event.get("is_anomalous")]
    anomaly_reason_counts: dict[str, int] = {}
    for event in anomalous_events:
        for reason in event.get("anomaly_reasons") or []:
            anomaly_reason_counts[str(reason)] = anomaly_reason_counts.get(str(reason), 0) + 1
    dates = sorted({str(event.get("local_date")) for event in events if event.get("local_date")})
    longest_streak = 0
    current_streak = 0
    prior_date: dt.date | None = None
    for value in dates:
        try:
            current_date = dt.date.fromisoformat(value)
        except ValueError:
            continue
        current_streak = current_streak + 1 if prior_date and (current_date - prior_date).days == 1 else 1
        longest_streak = max(longest_streak, current_streak)
        prior_date = current_date

    source_counts = {row["value"]: row["events"] for row in dimension_summary(events, "source")}
    confidence_counts = {row["value"]: row["events"] for row in dimension_summary(events, "match_confidence")}
    bulk_history_ids = {
        str(event.get("bulk_history_cluster_id"))
        for event in events
        if event.get("bulk_history_cluster_id")
    }
    bulk_history_events = sum(bool(event.get("is_bulk_history_cluster")) for event in events)
    binge_ids = {str(event.get("binge_session_id")) for event in events if event.get("binge_session_id")}
    binge_lengths = [
        max(integer(event.get("binge_episode_count")) or 0 for event in events if event.get("binge_session_id") == binge_id)
        for binge_id in binge_ids
    ]
    late_night = sum(event.get("time_of_day") == "late_night" for event in events)
    weekend = sum(event.get("local_weekday") in {"Saturday", "Sunday"} for event in events)
    with_genres = sum(bool(event.get("genres")) for event in events)
    with_completion = len(known_completions)
    with_progress = sum(number(event.get("progress_seconds")) is not None for event in events)
    with_exact_progress = sum(event.get("progress_source") == "tautulli_view_offset" for event in events)
    completed = sum(value >= 90 for value in known_completions)
    abandoned = sum(value < 10 for value in known_completions)

    return {
        "schema_version": "1.3",
        "generated_at": utc_now(),
        "llm_read_me": {
            "purpose": "Compact behavioral summary of joined Plex and Tautulli playback events.",
            "recommended_context_order": [
                "overview",
                "data_quality",
                "completion",
                "watch_time",
                "habits",
                "breakdowns",
                "definitions",
            ],
            "cautions": [
                "Percent watched exists only where Tautulli recorded percent_complete.",
                "Plex-only events must not be treated as 100% watched.",
                "Watch-time summaries exclude sessions flagged as stale or implausibly long; raw totals remain available.",
                "Genre counts are multi-label; one event may count toward several genres.",
                "Current Plex metadata may differ from metadata at the historical playback date.",
                "Tautulli coverage begins when Tautulli started monitoring the server.",
                "Near-simultaneous Plex-only episode rows are treated as bulk mark-watched/import history and excluded from binge statistics.",
            ],
        },
        "overview": {
            "events": len(events),
            "first_event_at": min((event.get("event_at_utc") for event in events if event.get("event_at_utc")), default=None),
            "last_event_at": max((event.get("event_at_utc") for event in events if event.get("event_at_utc")), default=None),
            "active_days": len(dates),
            "longest_daily_streak_days": longest_streak,
            "unique_media_items": len({str(event.get("media_identity")) for event in events}),
            "unique_movies": len({str(event.get("media_identity")) for event in events if event.get("media_type") == "movie"}),
            "unique_episodes": len({str(event.get("media_identity")) for event in events if event.get("media_type") == "episode"}),
            "unique_shows": len({str(event.get("show_title")) for event in events if event.get("show_title")}),
            "unique_genres": len({genre for event in events for genre in (event.get("genres") or [])}),
            "rewatch_events": sum(bool(event.get("is_rewatch")) for event in events),
        },
        "data_quality": {
            "source_counts": source_counts,
            "match_confidence_counts": confidence_counts,
            "events_with_progress": with_progress,
            "progress_coverage_percent": round(with_progress / len(events) * 100, 2) if events else 0,
            "events_with_completion_percent": with_completion,
            "completion_percent_coverage_percent": round(with_completion / len(events) * 100, 2) if events else 0,
            "events_with_exact_progress_seconds": with_exact_progress,
            "events_with_genres": with_genres,
            "genre_coverage_percent": round(with_genres / len(events) * 100, 2) if events else 0,
            "events_with_runtime": sum(number(event.get("duration_seconds")) is not None for event in events),
            "events_with_estimated_progress": sum(bool(event.get("progress_is_estimated")) for event in events),
            "anomalous_events_excluded_from_time_analysis": len(anomalous_events),
            "anomalous_event_share_percent": round(len(anomalous_events) / len(events) * 100, 2) if events else 0,
            "anomaly_reason_counts": dict(sorted(anomaly_reason_counts.items())),
            "metadata_batch_errors": metadata_errors,
            "metadata_recovery": metadata_stats or {},
            "bulk_history_clusters_excluded_from_binge_analysis": len(bulk_history_ids),
            "bulk_history_events_excluded_from_binge_analysis": bulk_history_events,
        },
        "incremental_update": incremental_info or {},
        "completion": {
            "known_events": with_completion,
            "completed_events_90_percent": completed,
            "completion_rate_percent": round(completed / with_completion * 100, 2) if with_completion else None,
            "abandoned_early_events_under_10_percent": abandoned,
            "abandonment_rate_percent": round(abandoned / with_completion * 100, 2) if with_completion else None,
            "average_completion_percent": round(statistics.fmean(known_completions), 2) if known_completions else None,
            "median_completion_percent": round(statistics.median(known_completions), 2) if known_completions else None,
            "completion_percent_p25": round(percentile(known_completions, 0.25), 2) if known_completions else None,
            "completion_percent_p75": round(percentile(known_completions, 0.75), 2) if known_completions else None,
            "by_band": dimension_summary(events, "completion_band"),
        },
        "watch_time": {
            "active_play_hours": round(sum(known_active) / 3600, 3),
            "paused_hours": round(sum(known_paused) / 3600, 3),
            "average_active_session_minutes": round(statistics.fmean(known_active) / 60, 2) if known_active else None,
            "median_active_session_minutes": round(statistics.median(known_active) / 60, 2) if known_active else None,
            "events_with_active_time": len(known_active),
            "raw_active_play_hours": round(sum(raw_known_active) / 3600, 3),
            "raw_paused_hours": round(sum(raw_known_paused) / 3600, 3),
            "raw_events_with_active_time": len(raw_known_active),
            "excluded_active_play_hours": round((sum(raw_known_active) - sum(known_active)) / 3600, 3),
            "excluded_paused_hours": round((sum(raw_known_paused) - sum(known_paused)) / 3600, 3),
            "excluded_anomalous_sessions": len(anomalous_events),
        },
        "habits": {
            "events_per_active_day": round(len(events) / len(dates), 2) if dates else None,
            "late_night_events_midnight_to_0559": late_night,
            "late_night_share_percent": round(late_night / len(events) * 100, 2) if events else 0,
            "weekend_events_saturday_sunday": weekend,
            "weekend_share_percent": round(weekend / len(events) * 100, 2) if events else 0,
            "binge_sessions_two_or_more_distinct_same_show_episodes_within_four_hours": len(binge_ids),
            "largest_binge_episode_count": max(binge_lengths, default=0),
            "bulk_history_clusters_excluded": len(bulk_history_ids),
        },
        "breakdowns": {
            "media_types": dimension_summary(events, "media_type"),
            "genres": dimension_summary(events, "genres", multi=True),
            "shows": dimension_summary([event for event in events if event.get("show_title")], "show_title", limit=50),
            "titles": dimension_summary(events, "full_title", limit=50),
            "platforms": dimension_summary(events, "platform"),
            "players": dimension_summary(events, "player"),
            "products": dimension_summary(events, "product"),
            "transcode_modes": dimension_summary(events, "transcode_decision"),
            "locations": dimension_summary(events, "location"),
            "months": dimension_summary(events, "local_month"),
            "weekdays": dimension_summary(events, "local_weekday"),
            "hours": dimension_summary(events, "local_hour"),
            "times_of_day": dimension_summary(events, "time_of_day"),
            "libraries": dimension_summary(events, "library"),
        },
        "definitions": {
            "event": "One Tautulli session joined to at most one Plex history record, plus unmatched records from either source.",
            "completion_percent": "Tautulli percent_complete, or view_offset divided by media runtime when percent_complete is unavailable; capped to 0–100.",
            "duration_seconds": "Media runtime from Plex/Tautulli item metadata; Tautulli session duration is never used as media runtime.",
            "progress_seconds": "Tautulli view_offset when available, otherwise estimated as completion percent times known media runtime.",
            "active_play_seconds": "Tautulli play_duration, or stopped minus started minus paused time when unavailable.",
            "analysis_included": "False for stale or implausibly long sessions excluded from watch-time totals; raw event data is retained.",
            "completed_90_percent": "A fixed analytical threshold of at least 90%; separate from Tautulli's configurable watched status.",
            "is_rewatch": "A completed event for media that already had an earlier completed event in this dataset.",
            "binge_session": "Two or more distinct episodes of the same show in a consecutive run whose event starts are no more than four hours apart; resume events and bulk Plex history clusters do not count.",
            "bulk_history_cluster": "Three or more distinct Plex-only episodes of one show recorded with no more than ten seconds between rows; retained as history but treated as a mark-watched/import cluster and excluded from binge analysis.",
            "match_confidence": "High: exact item within 15 minutes; medium: supported identity within one hour; low: wider time match.",
        },
    }


def top_labels(summary: dict[str, Any], field: str, limit: int = 5) -> str:
    rows = summary.get("breakdowns", {}).get(field, [])
    labels = [f"{row.get('value')} ({row.get('events', 0):,})" for row in rows[:limit] if row.get("value") != "Unknown"]
    return ", ".join(labels) if labels else "Not enough data"


def count_label(value: Any, singular: str, plural: str | None = None) -> str:
    count = integer(value) or 0
    return f"{count:,} {singular if count == 1 else (plural or singular + 's')}"


def build_insights_markdown(summary: dict[str, Any]) -> str:
    """Create a readable report from fixed rules; no LLM or external service is used."""
    overview = summary.get("overview", {})
    quality = summary.get("data_quality", {})
    completion = summary.get("completion", {})
    watch = summary.get("watch_time", {})
    habits = summary.get("habits", {})
    incremental = summary.get("incremental_update", {})
    recovery = quality.get("metadata_recovery", {})
    first = overview.get("first_event_at") or "unknown"
    last = overview.get("last_event_at") or "unknown"
    lines = [
        "# Histarr viewing insights",
        "",
        f"Generated {summary.get('generated_at')}. This report is deterministic and does not use an LLM.",
        "",
        "## Overview",
        "",
        f"- {overview.get('events', 0):,} playback events from {first} through {last}.",
        f"- {overview.get('unique_media_items', 0):,} unique media items across {overview.get('active_days', 0):,} active days.",
        f"- {count_label(overview.get('unique_movies'), 'movie')}, {count_label(overview.get('unique_episodes'), 'episode')}, and {count_label(overview.get('unique_shows'), 'show')}.",
        f"- Longest daily viewing streak: {overview.get('longest_daily_streak_days', 0):,} days; completed rewatch events: {overview.get('rewatch_events', 0):,}.",
        "",
        "## Completion and time",
        "",
        f"- Completion percent is available for {quality.get('completion_percent_coverage_percent', 0):.2f}% of events; progress seconds are available or estimable for {quality.get('progress_coverage_percent', 0):.2f}%.",
        f"- Of events with progress, {completion.get('completion_rate_percent') if completion.get('completion_rate_percent') is not None else 'unknown'}% reached at least 90% and {completion.get('abandonment_rate_percent') if completion.get('abandonment_rate_percent') is not None else 'unknown'}% ended below 10%.",
        f"- Median completion: {completion.get('median_completion_percent') if completion.get('median_completion_percent') is not None else 'unknown'}%; median active session: {watch.get('median_active_session_minutes') if watch.get('median_active_session_minutes') is not None else 'unknown'} minutes.",
        f"- Clean active play time: {watch.get('active_play_hours', 0):,.1f} hours; clean paused time: {watch.get('paused_hours', 0):,.1f} hours.",
        f"- Raw active time was {watch.get('raw_active_play_hours', 0):,.1f} hours; {watch.get('excluded_active_play_hours', 0):,.1f} hours from {watch.get('excluded_anomalous_sessions', 0):,} anomalous sessions were excluded.",
        "",
        "## Habits",
        "",
        f"- Average events per active day: {habits.get('events_per_active_day') if habits.get('events_per_active_day') is not None else 'unknown'}.",
        f"- Late-night share (00:00–05:59 local): {habits.get('late_night_share_percent', 0):.2f}%; weekend share: {habits.get('weekend_share_percent', 0):.2f}%.",
        f"- Binge sessions: {habits.get('binge_sessions_two_or_more_distinct_same_show_episodes_within_four_hours', 0):,}; largest detected run: {habits.get('largest_binge_episode_count', 0):,} distinct episodes.",
        f"- Bulk Plex mark-watched/import clusters excluded from binge analysis: {habits.get('bulk_history_clusters_excluded', 0):,}.",
        "",
        "## Most frequent dimensions",
        "",
        f"- Genres: {top_labels(summary, 'genres')}",
        f"- Shows: {top_labels(summary, 'shows')}",
        f"- Platforms: {top_labels(summary, 'platforms')}",
        f"- Players: {top_labels(summary, 'players')}",
        f"- Time of day: {top_labels(summary, 'times_of_day')}",
        "",
        "## Data quality",
        "",
        f"- Genre coverage: {quality.get('genre_coverage_percent', 0):.2f}% of events.",
        f"- Time-analysis exclusions: {quality.get('anomalous_events_excluded_from_time_analysis', 0):,} anomalous sessions ({quality.get('anomalous_event_share_percent', 0):.2f}% of events).",
        f"- Metadata recovered from Tautulli: {count_label(recovery.get('tautulli_recovered_items'), 'item')}; unresolved: {integer(recovery.get('unresolved_items')) or 0:,}.",
        f"- Current Plex metadata unavailable for deleted/renumbered items: {integer(recovery.get('plex_unavailable_items')) or 0:,}.",
        f"- Incremental cache used: {'yes' if incremental.get('cache_loaded') else 'no'}; Plex events fetched this run (including overlap): {incremental.get('plex_downloaded_events', 0):,}; Tautulli events fetched this run (including overlap): {incremental.get('tautulli_downloaded_events', 0):,}.",
        "",
        "## Interpretation cautions",
        "",
        "- Missing playback progress is unknown, not 100% watched.",
        "- Clean watch-time totals exclude flagged stale sessions; raw totals remain in summary.json.",
        "- Genre totals overlap because media can have multiple genres.",
        "- Plex and Tautulli retain different history windows; unmatched events are intentionally preserved.",
        "- Near-simultaneous Plex-only episode rows are retained but excluded from binge statistics as bulk history updates.",
        "- Current or cached metadata may differ from metadata at the original playback date.",
        "",
    ]
    return "\n".join(lines)
