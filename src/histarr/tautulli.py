# SPDX-License-Identifier: GPL-3.0-only
"""Tautulli API integration, metadata fallback, and raw exports."""

from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import urllib.parse
from pathlib import Path
from typing import Any

from .core import (
    METADATA_MISS_RETRY_DAYS, PRODUCT, VERSION, ExportError, json_request,
    listify, number, safe_name, tabular_value, utc_now,
)


def tautulli_api_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise ExportError("A Tautulli URL is required")
    if "://" not in value:
        value = "http://" + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExportError("The Tautulli URL must be an HTTP or HTTPS URL")
    if parsed.path.rstrip("/").endswith("/api/v2"):
        return value
    return value + "/api/v2"


def tautulli_call(api_url: str, api_key: str, command: str, **params: Any) -> Any:
    query: dict[str, Any] = {"apikey": api_key, "cmd": command, "out_type": "json"}
    query.update({key: value for key, value in params.items() if value is not None})
    data = json_request(
        f"{api_url}?{urllib.parse.urlencode(query)}",
        {"Accept": "application/json", "User-Agent": f"{PRODUCT}/{VERSION}"},
        timeout=30,
    )
    response = data.get("response", {}) if isinstance(data, dict) else {}
    if response.get("result") != "success":
        message = response.get("message") or "Tautulli rejected the API request"
        raise ExportError(f"Tautulli API error: {message}")
    return response.get("data")


def tautulli_metadata_as_plex(raw: dict[str, Any], rating_key: str) -> dict[str, Any]:
    """Map Tautulli's stored metadata into the subset consumed by combined events."""
    mapped: dict[str, Any] = {
        "ratingKey": str(raw.get("rating_key") or rating_key),
        "type": raw.get("media_type") or raw.get("type"),
        "title": raw.get("title"),
        "grandparentTitle": raw.get("grandparent_title"),
        "parentTitle": raw.get("parent_title"),
        "parentIndex": raw.get("parent_media_index") or raw.get("parent_index"),
        "index": raw.get("media_index") or raw.get("index"),
        "year": raw.get("year"),
        "duration": raw.get("duration"),
        "guid": raw.get("guid"),
        "originallyAvailableAt": raw.get("originally_available_at"),
        "librarySectionTitle": raw.get("section_name") or raw.get("library_name"),
        "summary": raw.get("summary"),
        "tagline": raw.get("tagline"),
        "studio": raw.get("studio"),
        "contentRating": raw.get("content_rating"),
        "rating": raw.get("rating"),
        "audienceRating": raw.get("audience_rating"),
        "Genre": [{"tag": value} for value in listify(raw.get("genres")) if value not in (None, "")],
        "Director": [{"tag": value} for value in listify(raw.get("directors")) if value not in (None, "")],
        "Writer": [{"tag": value} for value in listify(raw.get("writers")) if value not in (None, "")],
        "Role": [{"tag": value} for value in listify(raw.get("actors")) if value not in (None, "")],
        "Country": [{"tag": value} for value in listify(raw.get("countries")) if value not in (None, "")],
        "Guid": [{"id": value} for value in listify(raw.get("guids")) if value not in (None, "")],
        "_exporter_source": "tautulli",
        "_exporter_checked_at": utc_now(),
    }
    media_info = raw.get("media_info") or raw.get("media") or []
    if isinstance(media_info, dict):
        media_info = [media_info]
    if isinstance(media_info, list):
        mapped["Media"] = [value for value in media_info if isinstance(value, dict)]
    return {key: value for key, value in mapped.items() if value not in (None, "", [])}


def metadata_miss_is_recent(value: dict[str, Any]) -> bool:
    if value.get("_exporter_source") != "not_found":
        return False
    checked = str(value.get("_exporter_checked_at") or "")
    try:
        checked_at = dt.datetime.fromisoformat(checked.replace("Z", "+00:00"))
    except ValueError:
        return False
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc) - checked_at < dt.timedelta(days=METADATA_MISS_RETRY_DAYS)


def fetch_tautulli_metadata(
    api_url: str,
    api_key: str,
    rating_keys: list[str],
    *,
    workers: int = 4,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    unique_keys = list(dict.fromkeys(str(key) for key in rating_keys if str(key)))
    if not unique_keys:
        return {}, []
    recovered: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def retrieve(key: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            raw = tautulli_call(api_url, api_key, "get_metadata", rating_key=key)
            if isinstance(raw, dict) and raw:
                return key, tautulli_metadata_as_plex(raw, key), None
            return key, None, None
        except ExportError as exc:
            return key, None, str(exc)

    max_workers = max(1, min(int(workers), 8))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(retrieve, key) for key in unique_keys]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            key, value, error = future.result()
            if value:
                recovered[key] = value
            elif error and len(errors) < 20:
                errors.append(f"Tautulli metadata {key}: {error}")
            if len(unique_keys) > 100 and (index == len(unique_keys) or index % 100 == 0):
                print(f"  Tautulli metadata recovery: {index}/{len(unique_keys)} items", flush=True)
    return recovered, errors


def fetch_tautulli_history(
    api_url: str,
    api_key: str,
    *,
    user_id: Any = None,
    username: str | None = None,
    all_users: bool,
    after_date: str | None = None,
) -> list[dict[str, Any]]:
    page_size = 1000
    start = 0
    history: list[dict[str, Any]] = []
    for _ in range(100000):
        page = tautulli_call(
            api_url,
            api_key,
            "get_history",
            grouping=0,
            include_activity=0,
            order_column="date",
            order_dir="asc",
            start=start,
            length=page_size,
            user_id=None if all_users or user_id in (None, "") else user_id,
            user=None if all_users or user_id not in (None, "") else username,
            after=after_date,
        )
        if not isinstance(page, dict):
            raise ExportError("Tautulli returned an unexpected history response")
        rows = page.get("data") or []
        if not isinstance(rows, list):
            raise ExportError("Tautulli returned an invalid history list")
        valid_rows = [row for row in rows if isinstance(row, dict)]
        history.extend(valid_rows)
        start += len(valid_rows)
        total = page.get("recordsFiltered")
        if total is None:
            total = page.get("recordsTotal")
        try:
            complete = total is not None and start >= int(total)
        except (TypeError, ValueError):
            complete = False
        if not valid_rows or complete or (total is None and len(valid_rows) < page_size):
            return history
        print(f"  Tautulli: downloaded {start} record(s)...", flush=True)
    raise ExportError("Tautulli history pagination exceeded its safety limit")


def tautulli_history_identity(record: dict[str, Any]) -> str:
    row_id = str(record.get("row_id") or "")
    if row_id:
        return "row:" + row_id
    seed = "|".join(
        str(record.get(key) or "")
        for key in ("rating_key", "started", "stopped", "user_id", "full_title")
    )
    return "fallback:" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def merge_tautulli_history(
    cached: list[dict[str, Any]],
    downloaded: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in cached + downloaded:
        if isinstance(row, dict):
            merged[tautulli_history_identity(row)] = row
    return sorted(
        merged.values(),
        key=lambda row: number(row.get("started") or row.get("date") or row.get("stopped")) or 0,
    )


def write_tautulli_exports(
    output_dir: Path,
    user: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    api_url: str,
    all_users: bool,
) -> tuple[Path, Path, Path, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    username = safe_name(str(user.get("username") or user.get("email") or "plex-user"))
    stem = f"tautulli-watch-history-{username}-{stamp}"
    json_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}.csv"
    report_path = output_dir / f"{stem}-report.txt"

    payload = {
        "exported_at": utc_now(),
        "source": "Tautulli get_history API",
        "scope": "all users visible to Tautulli" if all_users else "signed-in Plex user only",
        "tautulli_url": api_url.removesuffix("/api/v2"),
        "account": {
            "id": user.get("id"),
            "username": user.get("username"),
            "email": user.get("email"),
        },
        "record_count": len(history),
        "history": history,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    preferred = [
        "date", "started", "stopped", "play_duration", "paused_counter",
        "view_offset", "duration", "percent_complete", "watched_status",
        "user", "user_id", "full_title", "media_type", "title",
        "grandparent_title", "parent_title", "media_index", "parent_media_index",
        "year", "originally_available_at", "rating_key", "guid", "platform",
        "product", "player", "ip_address", "location", "transcode_decision",
    ]
    available = {key for row in history for key in row}
    fields = [key for key in preferred if key in available]
    fields.extend(sorted(available - set(fields)))
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in history:
            writer.writerow({key: tabular_value(row.get(key)) for key in fields})

    report_lines = [
        "Tautulli history export report",
        f"Exported: {payload['exported_at']}",
        f"Scope: {payload['scope']}",
        f"Records: {len(history)}",
        "",
        "Notes:",
        "- The API key is not stored in these files.",
        "- Tautulli only has detailed sessions recorded while it was running.",
        "- percent_complete is Tautulli's recorded completion value; play_duration excludes recorded pause time.",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return json_path, csv_path, report_path, len(history)
