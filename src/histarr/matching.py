# SPDX-License-Identifier: GPL-3.0-only
"""Plex/Tautulli identity matching and normalized event construction."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

from .core import epoch_iso, integer, normalized_text, number


def plex_signature(raw: dict[str, Any]) -> str:
    media_type = str(raw.get("type") or "")
    if media_type == "episode":
        return "|".join(
            [
                "episode",
                normalized_text(raw.get("grandparentTitle")),
                str(raw.get("parentIndex") or ""),
                str(raw.get("index") or ""),
            ]
        )
    return "|".join([media_type, normalized_text(raw.get("title"))])


def tautulli_signature(raw: dict[str, Any]) -> str:
    media_type = str(raw.get("media_type") or raw.get("type") or "")
    if media_type == "episode":
        return "|".join(
            [
                "episode",
                normalized_text(raw.get("grandparent_title")),
                str(raw.get("parent_media_index") or ""),
                str(raw.get("media_index") or ""),
            ]
        )
    return "|".join([media_type, normalized_text(raw.get("title") or raw.get("full_title"))])


def metadata_values(metadata: dict[str, Any], key: str, attribute: str = "tag") -> list[str]:
    values = metadata.get(key) or []
    if isinstance(values, dict):
        values = [values]
    result: list[str] = []
    for value in values:
        if isinstance(value, dict) and value.get(attribute) not in (None, ""):
            result.append(str(value[attribute]))
    return result


def first_media_value(metadata: dict[str, Any], key: str) -> Any:
    media = metadata.get("Media") or []
    if isinstance(media, dict):
        media = [media]
    return media[0].get(key) if media and isinstance(media[0], dict) else None


def enrich_with_show_metadata(
    item_metadata: dict[str, Any],
    show_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Inherit show-level classification without copying a show's runtime/title."""
    item = dict(item_metadata) if isinstance(item_metadata, dict) else {}
    show = show_metadata if isinstance(show_metadata, dict) else {}
    inherited = False
    for key in ("Genre", "Country", "Collection"):
        if not item.get(key) and show.get(key):
            item[key] = show[key]
            inherited = True
    for key in ("studio", "contentRating", "audienceRating", "rating"):
        if item.get(key) in (None, "") and show.get(key) not in (None, ""):
            item[key] = show[key]
            inherited = True
    if item_metadata:
        item["_exporter_source"] = item.get("_exporter_source") or "plex"
    elif show:
        item["_exporter_source"] = "plex_show"
    item["_genre_source"] = (
        "item_metadata"
        if item_metadata.get("Genre")
        else "show_metadata"
        if show.get("Genre")
        else "none"
    )
    if inherited or (show and not item_metadata):
        item["_exporter_show_rating_key"] = show.get("ratingKey")
    return item


def history_matches(
    plex_entries: list[dict[str, Any]],
    tautulli_history: list[dict[str, Any]],
    tautulli_server_id: str,
) -> tuple[dict[int, tuple[int, str, float, str]], set[int], set[int]]:
    candidates: list[tuple[int, float, int, int, str, str]] = []
    for t_index, t_row in enumerate(tautulli_history):
        t_key = str(t_row.get("rating_key") or "")
        t_signature = tautulli_signature(t_row)
        t_times = [
            value
            for value in (
                number(t_row.get("stopped")),
                number(t_row.get("date")),
                number(t_row.get("started")),
            )
            if value is not None and value > 0
        ]
        if not t_times:
            continue
        for p_index, p_entry in enumerate(plex_entries):
            if tautulli_server_id and p_entry["server_id"] != tautulli_server_id:
                continue
            p_row = p_entry["record"]
            p_time = number(p_row.get("viewedAt"))
            if p_time is None:
                continue
            p_key = str(p_row.get("ratingKey") or "")
            if t_key and p_key and t_key == p_key:
                method = "rating_key_and_time"
                priority = 0
                max_delta = 12 * 3600
            elif t_signature and t_signature == plex_signature(p_row):
                method = "title_episode_identity_and_time"
                priority = 1
                max_delta = 6 * 3600
            else:
                continue
            delta = min(abs(p_time - timestamp) for timestamp in t_times)
            if delta <= max_delta:
                if method == "rating_key_and_time" and delta <= 15 * 60:
                    confidence = "high"
                elif delta <= 60 * 60:
                    confidence = "medium"
                else:
                    confidence = "low"
                candidates.append((priority, delta, t_index, p_index, method, confidence))

    matches: dict[int, tuple[int, str, float, str]] = {}
    matched_t: set[int] = set()
    matched_p: set[int] = set()
    for _priority, delta, t_index, p_index, method, confidence in sorted(candidates):
        if t_index in matched_t or p_index in matched_p:
            continue
        matched_t.add(t_index)
        matched_p.add(p_index)
        matches[t_index] = (p_index, method, delta, confidence)
    return matches, matched_t, matched_p


def make_combined_event(
    tautulli: dict[str, Any] | None,
    plex_entry: dict[str, Any] | None,
    metadata: dict[str, Any],
    match: tuple[str, float, str] | None,
    tautulli_server_id: str,
) -> dict[str, Any]:
    t = tautulli or {}
    p = plex_entry["record"] if plex_entry else {}
    server = plex_entry["server"] if plex_entry else {}
    server_id = str(server.get("clientIdentifier") or tautulli_server_id or t.get("server_id") or "")
    rating_key = str(t.get("rating_key") or p.get("ratingKey") or metadata.get("ratingKey") or "")
    media_type = str(t.get("media_type") or p.get("type") or metadata.get("type") or "")

    # Tautulli get_history duration/play_duration are session seconds, not media
    # runtime. Media runtime comes only from item metadata.
    duration_ms = number(metadata.get("duration"))
    progress_ms = number(t.get("view_offset"))
    duration_seconds = duration_ms / 1000 if duration_ms is not None else None
    progress_seconds = progress_ms / 1000 if progress_ms is not None else None
    completion_raw = number(t.get("percent_complete"))
    completion = completion_raw
    if completion is None and progress_ms is not None and duration_ms and duration_ms > 0:
        completion = progress_ms / duration_ms * 100
    if completion is not None:
        completion = max(0.0, min(100.0, completion))
    progress_source = "tautulli_view_offset" if progress_seconds is not None else "unavailable"
    progress_is_estimated = False
    if progress_seconds is None and duration_seconds is not None and completion is not None:
        progress_seconds = duration_seconds * completion / 100
        progress_source = "estimated_from_completion_percent"
        progress_is_estimated = True
    runtime_source = (
        str(metadata.get("_exporter_source") or "metadata")
        if duration_seconds is not None
        else "unavailable"
    )

    started = number(t.get("started"))
    stopped = number(t.get("stopped"))
    event_epoch = started or number(t.get("date")) or number(p.get("viewedAt")) or stopped
    paused_seconds = number(t.get("paused_counter"))
    active_seconds = number(t.get("play_duration"))
    active_play_source = "tautulli_play_duration" if active_seconds is not None else "unavailable"
    if active_seconds is None and tautulli:
        active_seconds = number(t.get("duration"))
        if active_seconds is not None:
            active_play_source = "tautulli_duration_fallback"
    wall_seconds = stopped - started if started and stopped and stopped >= started else None
    if active_seconds is None and wall_seconds is not None:
        active_seconds = max(0.0, wall_seconds - (paused_seconds or 0.0))
        active_play_source = "derived_wall_clock_minus_pause"
    pause_share = (
        (paused_seconds or 0.0) / wall_seconds * 100
        if wall_seconds is not None and wall_seconds > 0
        else None
    )
    active_runtime = (
        active_seconds / duration_seconds * 100
        if active_seconds is not None and duration_seconds and duration_seconds > 0
        else None
    )
    remaining_seconds = (
        max(0.0, duration_seconds - progress_seconds)
        if duration_seconds is not None and progress_seconds is not None
        else None
    )
    anomaly_reasons: list[str] = []
    if active_seconds is not None and active_seconds > 12 * 3600:
        anomaly_reasons.append("active_play_over_12_hours")
    if paused_seconds is not None and paused_seconds > 12 * 3600:
        anomaly_reasons.append("paused_time_over_12_hours")
    if wall_seconds is not None and wall_seconds > 24 * 3600:
        anomaly_reasons.append("wall_clock_over_24_hours")
    if (
        active_seconds is not None
        and wall_seconds is not None
        and active_seconds > wall_seconds + 60
    ):
        anomaly_reasons.append("active_play_exceeds_wall_clock")
    if completion_raw is not None and not 0 <= completion_raw <= 100:
        anomaly_reasons.append("completion_outside_0_100")
    is_anomalous = bool(anomaly_reasons)

    if completion is None:
        completion_band = "unknown"
    elif completion < 10:
        completion_band = "abandoned_early_0_9"
    elif completion < 25:
        completion_band = "sampled_10_24"
    elif completion < 50:
        completion_band = "partial_25_49"
    elif completion < 75:
        completion_band = "partial_50_74"
    elif completion < 90:
        completion_band = "nearly_finished_75_89"
    else:
        completion_band = "completed_90_100"

    genres = metadata_values(metadata, "Genre")
    directors = metadata_values(metadata, "Director")
    writers = metadata_values(metadata, "Writer")
    actors = metadata_values(metadata, "Role")
    countries = metadata_values(metadata, "Country")
    external_guids = metadata_values(metadata, "Guid", "id")
    ratings = metadata.get("Rating") or []
    if isinstance(ratings, dict):
        ratings = [ratings]

    local_dt = None
    if event_epoch:
        try:
            local_dt = dt.datetime.fromtimestamp(event_epoch).astimezone()
        except (ValueError, OverflowError, OSError):
            pass
    identity = "|".join(
        [server_id, rating_key or str(metadata.get("guid") or t.get("guid") or plex_signature(p))]
    )
    row_seed = "|".join(
        [
            server_id,
            str(t.get("row_id") or ""),
            str(p.get("historyKey") or ""),
            str(event_epoch or ""),
            rating_key,
        ]
    )
    source = "plex+tautulli" if tautulli and plex_entry else ("tautulli_only" if tautulli else "plex_only")
    match_method, match_delta, match_confidence = match or ("unmatched", None, "none")
    watched_status = t.get("watched_status")
    title = t.get("title") or p.get("title") or metadata.get("title") or ""
    show_title = t.get("grandparent_title") or p.get("grandparentTitle") or metadata.get("grandparentTitle") or ""
    full_title = t.get("full_title") or (f"{show_title} - {title}" if show_title and title else title)

    return {
        "event_id": hashlib.sha256(row_seed.encode()).hexdigest()[:20],
        "source": source,
        "metadata_source": metadata.get("_exporter_source") or "history_only",
        "genre_source": metadata.get("_genre_source") or ("item_metadata" if genres else "none"),
        "match_method": match_method,
        "match_confidence": match_confidence,
        "match_time_delta_seconds": round(match_delta, 3) if match_delta is not None else None,
        "server_name": server.get("name") or t.get("server_name") or "",
        "server_id": server_id,
        "plex_history_key": p.get("historyKey"),
        "tautulli_row_id": t.get("row_id"),
        "media_identity": identity,
        "rating_key": rating_key,
        "guid": metadata.get("guid") or t.get("guid") or p.get("guid") or "",
        "external_guids": external_guids,
        "media_type": media_type,
        "title": title,
        "full_title": full_title,
        "show_title": show_title,
        "season_title": t.get("parent_title") or p.get("parentTitle") or metadata.get("parentTitle") or "",
        "season_number": integer(t.get("parent_media_index") or p.get("parentIndex") or metadata.get("parentIndex")),
        "episode_number": integer(t.get("media_index") or p.get("index") or metadata.get("index")),
        "year": integer(t.get("year") or metadata.get("year")),
        "originally_available_at": t.get("originally_available_at") or p.get("originallyAvailableAt") or metadata.get("originallyAvailableAt") or "",
        "library": metadata.get("librarySectionTitle") or t.get("section_name") or "",
        "summary": metadata.get("summary") or "",
        "tagline": metadata.get("tagline") or "",
        "studio": metadata.get("studio") or "",
        "content_rating": metadata.get("contentRating") or "",
        "critic_rating": number(metadata.get("rating")),
        "audience_rating": number(metadata.get("audienceRating")),
        "ratings": ratings,
        "genres": genres,
        "directors": directors,
        "writers": writers,
        "actors": actors,
        "countries": countries,
        "event_at_utc": epoch_iso(event_epoch),
        "event_at_local": epoch_iso(event_epoch, local=True),
        "started_at_utc": epoch_iso(started),
        "stopped_at_utc": epoch_iso(stopped),
        "plex_viewed_at_utc": epoch_iso(p.get("viewedAt")),
        "local_date": local_dt.date().isoformat() if local_dt else "",
        "local_month": local_dt.strftime("%Y-%m") if local_dt else "",
        "local_weekday": local_dt.strftime("%A") if local_dt else "",
        "local_hour": local_dt.hour if local_dt else None,
        "time_of_day": (
            "late_night" if local_dt and local_dt.hour < 6 else
            "morning" if local_dt and local_dt.hour < 12 else
            "afternoon" if local_dt and local_dt.hour < 18 else
            "evening" if local_dt else ""
        ),
        "duration_seconds": round(duration_seconds, 3) if duration_seconds is not None else None,
        "runtime_source": runtime_source,
        "progress_seconds": round(progress_seconds, 3) if progress_seconds is not None else None,
        "progress_source": progress_source,
        "progress_is_estimated": progress_is_estimated,
        "completion_percent": round(completion, 3) if completion is not None else None,
        "completion_band": completion_band,
        "completed_90_percent": completion is not None and completion >= 90,
        "tautulli_watched_status": number(watched_status),
        "tautulli_watched": number(watched_status) >= 1 if number(watched_status) is not None else None,
        "remaining_seconds": round(remaining_seconds, 3) if remaining_seconds is not None else None,
        "active_play_seconds": round(active_seconds, 3) if active_seconds is not None else None,
        "active_play_source": active_play_source,
        "tautulli_session_duration_seconds": number(t.get("duration")),
        "paused_seconds": round(paused_seconds, 3) if paused_seconds is not None else None,
        "wall_clock_seconds": round(wall_seconds, 3) if wall_seconds is not None else None,
        "pause_share_percent": round(pause_share, 3) if pause_share is not None else None,
        "active_play_vs_runtime_percent": round(active_runtime, 3) if active_runtime is not None else None,
        "is_anomalous": is_anomalous,
        "anomaly_reasons": anomaly_reasons,
        "analysis_included": not is_anomalous,
        "user": t.get("user") or t.get("friendly_name") or "",
        "user_id": t.get("user_id") or p.get("accountID"),
        "platform": t.get("platform") or "",
        "product": t.get("product") or "",
        "player": t.get("player") or "",
        "machine_id": t.get("machine_id") or "",
        "location": t.get("location") or "",
        "ip_address": t.get("ip_address") or "",
        "secure": t.get("secure"),
        "relayed": t.get("relayed"),
        "transcode_decision": t.get("transcode_decision") or "",
        "video_decision": t.get("video_decision") or t.get("stream_video_decision") or "",
        "audio_decision": t.get("audio_decision") or t.get("stream_audio_decision") or "",
        "container": first_media_value(metadata, "container") or t.get("container") or "",
        "video_codec": first_media_value(metadata, "videoCodec") or t.get("video_codec") or "",
        "audio_codec": first_media_value(metadata, "audioCodec") or t.get("audio_codec") or "",
        "video_resolution": first_media_value(metadata, "videoResolution") or t.get("video_resolution") or "",
        "width": integer(first_media_value(metadata, "width") or t.get("width")),
        "height": integer(first_media_value(metadata, "height") or t.get("height")),
        "bitrate_kbps": integer(first_media_value(metadata, "bitrate") or t.get("bitrate")),
        "audio_channels": integer(first_media_value(metadata, "audioChannels") or t.get("audio_channels")),
        "play_number_for_item": None,
        "completed_watch_number_for_item": None,
        "is_rewatch": False,
        "minutes_since_previous_event": None,
        "binge_session_id": "",
        "binge_episode_count": None,
        "binge_event_count": None,
        "is_bulk_history_cluster": False,
        "bulk_history_cluster_id": "",
        "binge_exclusion_reason": "",
    }
