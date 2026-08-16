# SPDX-License-Identifier: GPL-3.0-only
"""Plex authentication, discovery, history, and metadata access."""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import secrets
import time
import urllib.parse
import webbrowser
from typing import Any

from .core import (
    CONFIG_FILE, PAGE_SIZE, PLEX_TV, ExportError, boolish, child_value,
    json_request, number, plex_headers, request, safe_name, save_credentials,
    scalar, utc_now,
)


def new_client_id() -> str:
    return secrets.token_hex(16)


def validate_token(client_id: str, token: str) -> dict[str, Any] | None:
    try:
        user = json_request(f"{PLEX_TV}/api/v2/user", plex_headers(client_id, token))
        return user if isinstance(user, dict) else None
    except ExportError as exc:
        if "HTTP 401" in str(exc):
            return None
        raise


def sign_in(client_id: str, *, no_browser: bool = False) -> tuple[str, dict[str, Any]]:
    print("\nSign in securely with Plex")
    pin = json_request(
        f"{PLEX_TV}/api/v2/pins?strong=true",
        plex_headers(client_id),
        method="POST",
        data=b"",
    )
    try:
        pin_id = str(pin["id"])
        pin_code = str(pin["code"])
    except (KeyError, TypeError) as exc:
        raise ExportError("Plex did not return a usable sign-in PIN") from exc

    fragment = urllib.parse.urlencode(
        {
            "clientID": client_id,
            "code": pin_code,
            "context[device][product]": PRODUCT,
        }
    )
    auth_url = f"https://app.plex.tv/auth#?{fragment}"
    opened = False if no_browser else webbrowser.open(auth_url)
    if opened:
        print("A Plex sign-in page has opened in your browser.")
    else:
        print(f"Open this URL in your browser:\n{auth_url}")
    print("Waiting for you to approve access", end="", flush=True)

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        time.sleep(2)
        check_url = f"{PLEX_TV}/api/v2/pins/{urllib.parse.quote(pin_id)}?" + urllib.parse.urlencode(
            {"code": pin_code}
        )
        result = json_request(check_url, plex_headers(client_id), timeout=10)
        token = result.get("authToken") if isinstance(result, dict) else None
        if token:
            print(" signed in.")
            user = validate_token(client_id, str(token))
            if not user:
                raise ExportError("Plex issued a token but did not accept it")
            save_credentials(client_id, str(token))
            return str(token), user
        print(".", end="", flush=True)
    print()
    raise ExportError("Plex sign-in timed out after 5 minutes")


def authenticate(*, force_login: bool, no_browser: bool) -> tuple[str, str, dict[str, Any]]:
    saved = load_credentials()
    client_id = saved.get("client_id") or new_client_id()
    token = saved.get("token")
    if token and not force_login:
        user = validate_token(client_id, token)
        if user:
            if not CONFIG_FILE.exists():
                save_credentials(client_id, token)
                print("Migrated the saved Plex sign-in to Histarr's private config directory.")
            print(f"Signed in as {user.get('username') or user.get('email') or 'Plex user'}.")
            return client_id, token, user
    token, user = sign_in(client_id, no_browser=no_browser)
    return client_id, token, user


def resources(client_id: str, token: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"includeHttps": 1, "includeRelay": 1, "includeIPv6": 1})
    result = json_request(f"{PLEX_TV}/api/v2/resources?{query}", plex_headers(client_id, token))
    if isinstance(result, list):
        values = result
    elif isinstance(result, dict):
        values = result.get("resources") or result.get("MediaContainer", {}).get("Device") or []
    else:
        values = []
    return [r for r in values if isinstance(r, dict) and "server" in str(r.get("provides", "")).split(",")]


def connection_urls(resource: dict[str, Any]) -> list[str]:
    connections = resource.get("connections") or resource.get("Connection") or []
    if isinstance(connections, dict):
        connections = [connections]
    usable: list[dict[str, Any]] = []
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        uri = str(connection.get("uri") or "").rstrip("/")
        if uri.startswith(("http://", "https://")):
            item = dict(connection)
            item["uri"] = uri
            usable.append(item)
    usable.sort(
        key=lambda c: (
            boolish(c.get("relay")),
            not str(c.get("uri", "")).startswith("https://"),
            not boolish(c.get("local")),
        )
    )
    seen: set[str] = set()
    result: list[str] = []
    for item in usable:
        uri = str(item["uri"])
        if uri not in seen:
            seen.add(uri)
            result.append(uri)
    return result


def parse_history_page(body: bytes, content_type: str) -> tuple[list[dict[str, Any]], int | None]:
    stripped = body.lstrip()
    if "json" in content_type.lower() or stripped.startswith((b"{", b"[")):
        data = json.loads(body)
        container = data.get("MediaContainer", data) if isinstance(data, dict) else {}
        records = container.get("Metadata") or container.get("Video") or []
        if isinstance(records, dict):
            records = [records]
        total = container.get("totalSize")
        return [r for r in records if isinstance(r, dict)], int(total) if total is not None else None

    # Plex normally honors Accept: application/json. This small fallback parser
    # handles its simple XML response without relying on a system expat library.
    text = body.decode("utf-8", "replace")
    tags = list(re.finditer(r"<\s*(/?)\s*([\w:.-]+)([^>]*)>", text))
    if not tags:
        raise ExportError("Plex returned neither JSON nor recognizable XML")

    def attributes(source: str) -> dict[str, str]:
        found: dict[str, str] = {}
        pattern = r"([\w:.-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')"
        for match in re.finditer(pattern, source):
            found[match.group(1)] = html.unescape(match.group(2) if match.group(2) is not None else match.group(3))
        return found

    root_match = tags[0]
    root_attrs = attributes(root_match.group(3))
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_tag = ""
    for match in tags[1:]:
        closing, tag, tail = match.group(1), match.group(2), match.group(3)
        self_closing = tail.rstrip().endswith("/")
        if closing:
            if current is not None and tag == current_tag:
                records.append(current)
                current = None
                current_tag = ""
            continue
        value: dict[str, Any] = attributes(tail)
        if current is None:
            current = {"_tag": tag, **value}
            current_tag = tag
            if self_closing:
                records.append(current)
                current = None
                current_tag = ""
        else:
            if tag in current:
                existing = current[tag]
                current[tag] = existing + [value] if isinstance(existing, list) else [existing, value]
            else:
                current[tag] = value
    if current is not None:
        records.append(current)
    total_text = root_attrs.get("totalSize")
    return records, int(total_text) if total_text else None


def fetch_history(
    base_url: str,
    client_id: str,
    token: str,
    *,
    account_id: str | None,
    after_epoch: float | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    start = 0
    for _ in range(100000):
        query: dict[str, Any] = {
            "sort": "viewedAt:desc" if after_epoch is not None else "viewedAt:asc",
            "X-Plex-Container-Start": start,
            "X-Plex-Container-Size": PAGE_SIZE,
        }
        if account_id is not None:
            query["accountID"] = account_id
        url = f"{base_url}/status/sessions/history/all?{urllib.parse.urlencode(query)}"
        body, content_type = request(url, plex_headers(client_id, token), timeout=30)
        page, total = parse_history_page(body, content_type)
        if after_epoch is None:
            records.extend(page)
        else:
            newer = [row for row in page if (number(row.get("viewedAt")) or 0) >= after_epoch]
            records.extend(newer)
            if len(newer) < len(page):
                return records
        start += len(page)
        if not page or (total is not None and start >= total) or (total is None and len(page) < PAGE_SIZE):
            return records
    raise ExportError("History pagination exceeded its safety limit")


def plex_history_identity(record: dict[str, Any]) -> str:
    history_key = str(record.get("historyKey") or "")
    if history_key:
        return "history:" + history_key
    seed = "|".join(
        str(record.get(key) or "")
        for key in ("ratingKey", "viewedAt", "accountID", "type", "title", "grandparentTitle")
    )
    return "fallback:" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def merge_plex_history(
    cached: list[dict[str, Any]],
    downloaded: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in cached + downloaded:
        if isinstance(row, dict):
            merged[plex_history_identity(row)] = row
    return sorted(merged.values(), key=lambda row: number(row.get("viewedAt")) or 0)


def fetch_plex_metadata(
    result: dict[str, Any],
    client_id: str,
    rating_keys: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
    """Fetch current metadata with connection failover and transient retries."""
    unique_keys = list(dict.fromkeys(str(key) for key in rating_keys if str(key)))
    if not unique_keys or not result.get("_connection_urls"):
        return {}, [], []
    urls = list(result["_connection_urls"])
    preferred_url: str | None = None
    token = str(result["_token"])
    metadata: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    unavailable: list[str] = []

    def is_not_found(error: str) -> bool:
        return error.startswith("HTTP 404 ") or "HTTP 404 from " in error

    def store_items(data: Any) -> None:
        container = data.get("MediaContainer", {}) if isinstance(data, dict) else {}
        items = container.get("Metadata") or []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if isinstance(item, dict) and item.get("ratingKey") is not None:
                item = dict(item)
                item["_exporter_source"] = "plex"
                item["_exporter_checked_at"] = utc_now()
                metadata[str(item["ratingKey"])] = item

    batch_size = 50
    total_batches = (len(unique_keys) + batch_size - 1) // batch_size
    for batch_number, offset in enumerate(range(0, len(unique_keys), batch_size), 1):
        batch = unique_keys[offset : offset + batch_size]
        query = urllib.parse.urlencode(
            {"includeGuids": 1, "includeChapters": 0, "includeExtras": 0}
        )
        candidates = ([preferred_url] if preferred_url else []) + [url for url in urls if url != preferred_url]
        data: Any = None
        last_error = ""
        for attempt in range(2):
            for base_url in candidates:
                if not base_url:
                    continue
                try:
                    data = json_request(
                        f"{base_url}/library/metadata/{','.join(batch)}?{query}",
                        plex_headers(client_id, token),
                        timeout=45,
                    )
                    preferred_url = base_url
                    break
                except ExportError as exc:
                    last_error = str(exc)
            if data is not None:
                break
            if attempt == 0:
                time.sleep(0.35)
        if data is None:
            # Plex returns a successful partial response if at least one key in a
            # multi-key request still exists, and 404 only when every key is no
            # longer in the current library. Treat that as expected historical
            # unavailability, not a failed batch or connection problem.
            if is_not_found(last_error):
                unavailable.extend(batch)
                continue
            failed_parts = 0
            split_error = last_error
            for part in (batch[: len(batch) // 2], batch[len(batch) // 2 :]):
                if not part:
                    continue
                part_data: Any = None
                for base_url in candidates:
                    if not base_url:
                        continue
                    try:
                        part_data = json_request(
                            f"{base_url}/library/metadata/{','.join(part)}?{query}",
                            plex_headers(client_id, token),
                            timeout=45,
                        )
                        preferred_url = base_url
                        break
                    except ExportError as exc:
                        split_error = str(exc)
                if part_data is None:
                    if is_not_found(split_error):
                        unavailable.extend(part)
                    else:
                        failed_parts += 1
                else:
                    store_items(part_data)
            if failed_parts:
                errors.append(
                    f"metadata batch {batch_number} had {failed_parts} failed split part(s) "
                    f"after connection retry: {split_error or 'unreachable'}"
                )
            continue
        store_items(data)
        if total_batches > 1 and (batch_number == total_batches or batch_number % 10 == 0):
            print(
                f"  Metadata: {min(offset + batch_size, len(unique_keys))}/{len(unique_keys)} items",
                flush=True,
            )
    return metadata, errors, list(dict.fromkeys(unavailable))


def normalize_record(raw: dict[str, Any], server: dict[str, Any]) -> dict[str, str]:
    viewed_raw = raw.get("viewedAt")
    viewed_at = ""
    try:
        viewed_at = dt.datetime.fromtimestamp(int(str(viewed_raw)), dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        pass
    return {
        "server_name": scalar(server.get("name")),
        "server_machine_id": scalar(server.get("clientIdentifier")),
        "viewed_at": viewed_at,
        "viewed_at_epoch": scalar(viewed_raw),
        "media_type": scalar(raw.get("type") or raw.get("_tag")),
        "title": scalar(raw.get("title")),
        "show_title": scalar(raw.get("grandparentTitle")),
        "season_title": scalar(raw.get("parentTitle")),
        "year": scalar(raw.get("year")),
        "season_number": scalar(raw.get("parentIndex")),
        "episode_number": scalar(raw.get("index")),
        "duration_ms": scalar(raw.get("duration")),
        "library": scalar(raw.get("librarySectionTitle")),
        "rating_key": scalar(raw.get("ratingKey")),
        "guid": scalar(raw.get("guid")),
        "account_id": scalar(raw.get("accountID")),
        "user": child_value(raw, "User", "title"),
        "player": child_value(raw, "Player", "title"),
        "platform": child_value(raw, "Player", "platform"),
        "device_address": child_value(raw, "Player", "address"),
    }
