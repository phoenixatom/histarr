# SPDX-License-Identifier: GPL-3.0-only

import json
import pathlib
import sys
import tempfile


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
from histarr import combined as module

SERVER_ID = "server-abc"
plex_results = [
    {
        "server": {"name": "Test Server", "clientIdentifier": SERVER_ID, "owned": True},
        "status": "ok",
        "error": None,
        "records": [
            {"historyKey": "h1", "ratingKey": "1", "type": "movie", "title": "Movie", "viewedAt": "1700000200"},
            {"historyKey": "h2", "ratingKey": "2", "type": "episode", "title": "Pilot", "grandparentTitle": "Show", "parentIndex": "1", "index": "1", "viewedAt": "1700003800"},
            {"historyKey": "h3", "ratingKey": "3", "type": "movie", "title": "Plex only", "viewedAt": "1700020000"},
        ],
        "_connection_urls": ["http://plex.test:32400"],
        "_token": "plex-secret-token",
    }
]

tautulli_history = [
    {
        "row_id": 11,
        "rating_key": "1",
        "media_type": "movie",
        "title": "Movie",
        "full_title": "Movie",
        "started": 1700000000,
        "stopped": 1700000300,
        "date": 1700000000,
        "duration": 280,
        "view_offset": 5700000,
        "percent_complete": 95,
        "play_duration": 280,
        "paused_counter": 20,
        "watched_status": 1,
        "user_id": 42,
        "platform": "TestOS",
        "player": "Living Room",
        "transcode_decision": "direct play",
    },
    {
        "row_id": 12,
        "rating_key": "2",
        "media_type": "episode",
        "title": "Pilot",
        "full_title": "Show - Pilot",
        "grandparent_title": "Show",
        "grandparent_rating_key": "100",
        "parent_media_index": 1,
        "media_index": 1,
        "started": 1700003600,
        "stopped": 1700003900,
        "date": 1700003600,
        "duration": 290,
        "view_offset": 1200000,
        "percent_complete": 50,
        "play_duration": 290,
        "paused_counter": 10,
        "watched_status": 0.75,
        "user_id": 42,
    },
    {
        "row_id": 13,
        "rating_key": "4",
        "media_type": "episode",
        "title": "Second",
        "full_title": "Show - Second",
        "grandparent_title": "Show",
        "grandparent_rating_key": "100",
        "parent_media_index": 1,
        "media_index": 2,
        "started": 1700007200,
        "stopped": 1700009000,
        "date": 1700007200,
        "duration": 1750,
        "percent_complete": 96.7,
        "play_duration": 1750,
        "paused_counter": 50,
        "watched_status": 1,
        "user_id": 42,
    },
    {
        "row_id": 14,
        "rating_key": "1",
        "media_type": "movie",
        "title": "Movie",
        "full_title": "Movie",
        "started": 1700100000,
        "stopped": 1700106000,
        "date": 1700100000,
        "duration": 5900,
        "view_offset": 6000000,
        "percent_complete": 100,
        "play_duration": 5900,
        "paused_counter": 100,
        "watched_status": 1,
        "user_id": 42,
    },
]

metadata = {
    "1": {"ratingKey": "1", "title": "Movie", "duration": 6000000, "Genre": [{"tag": "Drama"}, {"tag": "Mystery"}]},
    "2": {"ratingKey": "2", "title": "Pilot", "grandparentTitle": "Show", "grandparentRatingKey": "100", "duration": 2400000},
    "3": {"ratingKey": "3", "title": "Plex only", "duration": 5400000, "Genre": [{"tag": "Comedy"}]},
    "4": {"ratingKey": "4", "title": "Second", "grandparentTitle": "Show", "grandparentRatingKey": "100", "duration": 2400000},
    "100": {"ratingKey": "100", "type": "show", "title": "Show", "Genre": [{"tag": "Drama"}]},
}


def fake_fetch(_result, _client_id, rating_keys):
    found = {key: metadata[key] for key in rating_keys if key in metadata and key != "4"}
    unavailable = [key for key in rating_keys if key not in found]
    return found, [], unavailable


def fake_tautulli_metadata(_api_url, _api_key, rating_keys, *, workers=4):
    assert workers == 4
    return {
        key: {**metadata[key], "_exporter_source": "tautulli"}
        for key in rating_keys
        if key == "4"
    }, []


module.fetch_plex_metadata = fake_fetch
module.fetch_tautulli_metadata = fake_tautulli_metadata
events, errors, metadata_cache, metadata_stats = module.build_combined_events(
    plex_results,
    tautulli_history,
    {"pms_identifier": SERVER_ID},
    "client-id",
    tautulli_api_url_value="http://tautulli.test/api/v2",
    tautulli_api_key="tautulli-secret-token",
)
assert not errors
assert metadata_stats["tautulli_recovered_items"] == 1
assert metadata_stats["plex_unavailable_items"] == 1
assert metadata_stats["show_metadata_fetched"] == 1
assert metadata_cache[SERVER_ID]["4"]["_exporter_source"] == "tautulli"
assert len(events) == 5
assert sum(event["source"] == "plex+tautulli" for event in events) == 2
assert sum(event["source"] == "tautulli_only" for event in events) == 2
assert sum(event["source"] == "plex_only" for event in events) == 1
assert any(event["is_rewatch"] for event in events)
assert sum(bool(event["binge_session_id"]) for event in events) == 2
assert next(event for event in events if event.get("tautulli_row_id") == 11)["completion_percent"] == 95
assert next(event for event in events if event.get("tautulli_row_id") == 11)["duration_seconds"] == 6000
assert next(event for event in events if event.get("plex_history_key") == "h3")["completion_percent"] is None
assert next(event for event in events if event.get("tautulli_row_id") == 13)["metadata_source"] == "tautulli"
assert next(event for event in events if event.get("tautulli_row_id") == 13)["genre_source"] == "show_metadata"
assert next(event for event in events if event.get("tautulli_row_id") == 13)["progress_is_estimated"] is True
assert next(event for event in events if event.get("tautulli_row_id") == 12)["tautulli_watched_status"] == 0.75
assert next(event for event in events if event.get("tautulli_row_id") == 12)["tautulli_watched"] is False

output_dir = pathlib.Path(tempfile.mkdtemp())
csv_path, json_path, jsonl_path, summary_path, insights_path = module.write_combined_exports(
    output_dir,
    {"id": 42, "username": "test-user"},
    events,
    metadata_errors=errors,
    metadata_stats=metadata_stats,
    incremental_info={"cache_loaded": True, "plex_downloaded_events": 2, "tautulli_downloaded_events": 3},
)
summary = json.loads(summary_path.read_text())
combined = json.loads(json_path.read_text())
assert summary["overview"]["events"] == 5
assert summary["completion"]["known_events"] == 4
assert summary["data_quality"]["progress_coverage_percent"] == 80.0
assert summary["habits"]["binge_sessions_two_or_more_distinct_same_show_episodes_within_four_hours"] == 1
assert any(row["value"] == "Drama" and row["events"] == 4 for row in summary["breakdowns"]["genres"])
assert combined["event_count"] == 5
assert sum(1 for _ in csv_path.open()) == 6
assert sum(1 for _ in jsonl_path.open()) == 5
assert "does not use an LLM" in insights_path.read_text()
assert summary["data_quality"]["metadata_recovery"]["tautulli_recovered_items"] == 1

resume_events = [
    {
        "event_at_utc": f"2024-01-01T00:{minute:02d}:00Z",
        "media_type": "episode",
        "show_title": "Resume Show",
        "media_identity": "same-episode",
        "completed_90_percent": False,
    }
    for minute in (0, 10, 20)
]
module.add_sequence_metrics(resume_events)
assert not any(event.get("binge_session_id") for event in resume_events)
resume_events.append(
    {
        "event_at_utc": "2024-01-01T00:30:00Z",
        "media_type": "episode",
        "show_title": "Resume Show",
        "media_identity": "next-episode",
        "completed_90_percent": False,
    }
)
module.add_sequence_metrics(resume_events)
assert all(event["binge_episode_count"] == 2 for event in resume_events)
assert all(event["binge_event_count"] == 4 for event in resume_events)

bulk_events = [
    {
        "event_at_utc": f"2024-01-02T00:00:0{second}Z",
        "media_type": "episode",
        "show_title": "Bulk Show",
        "full_title": f"Bulk Show - Episode {second}",
        "media_identity": f"bulk-episode-{second}",
        "source": "plex_only",
        "completed_90_percent": False,
    }
    for second in range(5)
]
module.add_sequence_metrics(bulk_events)
assert all(event["is_bulk_history_cluster"] for event in bulk_events)
assert len({event["bulk_history_cluster_id"] for event in bulk_events}) == 1
assert not any(event["binge_session_id"] for event in bulk_events)
assert all(
    event["binge_exclusion_reason"] == "bulk_plex_history_timestamp_cluster"
    for event in bulk_events
)
bulk_summary = module.build_summary(bulk_events, metadata_errors=[])
assert bulk_summary["data_quality"]["bulk_history_clusters_excluded_from_binge_analysis"] == 1
assert bulk_summary["data_quality"]["bulk_history_events_excluded_from_binge_analysis"] == 5
assert bulk_summary["habits"]["binge_sessions_two_or_more_distinct_same_show_episodes_within_four_hours"] == 0

real_binge_events = [
    {
        "event_at_utc": f"2024-01-03T0{hour}:00:00Z",
        "media_type": "episode",
        "show_title": "Real Show",
        "full_title": f"Real Show - Episode {hour}",
        "media_identity": f"real-episode-{hour}",
        "source": "tautulli_only",
        "completed_90_percent": True,
    }
    for hour in (0, 1)
]
module.add_sequence_metrics(real_binge_events)
assert all(event["binge_session_id"] for event in real_binge_events)
assert not any(event["is_bulk_history_cluster"] for event in real_binge_events)

stale = module.make_combined_event(
    {
        "row_id": 99,
        "rating_key": "1",
        "media_type": "movie",
        "title": "Stale",
        "started": 100,
        "stopped": 100 + 14 * 3600,
        "play_duration": 14 * 3600,
        "paused_counter": 0,
        "percent_complete": 50,
        "watched_status": 0.5,
    },
    None,
    metadata["1"],
    None,
    SERVER_ID,
)
assert stale["is_anomalous"] is True
assert stale["analysis_included"] is False
assert "active_play_over_12_hours" in stale["anomaly_reasons"]
stale_summary = module.build_summary(events + [stale], metadata_errors=[])
assert stale_summary["data_quality"]["anomalous_events_excluded_from_time_analysis"] == 1
assert stale_summary["watch_time"]["raw_active_play_hours"] > stale_summary["watch_time"]["active_play_hours"]
all_output = "\n".join(
    path.read_text() for path in (csv_path, json_path, jsonl_path, summary_path, insights_path)
)
assert "plex-secret-token" not in all_output
assert "tautulli-secret-token" not in all_output

print("Combined join, recovery, JSONL, insights, CSV, and token-redaction tests passed")
