# SPDX-License-Identifier: GPL-3.0-only
"""Shared constants, low-level HTTP helpers, configuration, and cache primitives."""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__


PRODUCT = "Histarr"
VERSION = __version__
PLEX_TV = "https://plex.tv"
PAGE_SIZE = 500
CONFIG_DIR = Path.home() / ".config" / "histarr"
CONFIG_FILE = CONFIG_DIR / "credentials.json"
LEGACY_CONFIG_FILE = Path.home() / ".config" / "plex-history-exporter" / "credentials.json"
CACHE_FILE_NAME = ".histarr-cache.json"
LEGACY_CACHE_FILE_NAME = ".plex-history-exporter-cache.json"
CACHE_SCHEMA_VERSION = "1.1"
INCREMENTAL_OVERLAP_SECONDS = 24 * 3600
METADATA_MISS_RETRY_DAYS = 7
BULK_HISTORY_MAX_GAP_SECONDS = 10
BULK_HISTORY_MIN_DISTINCT_EPISODES = 3


class ExportError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def plex_headers(client_id: str, token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Version": VERSION,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Device": "Computer",
        "X-Plex-Device-Name": socket.gethostname() or PRODUCT,
        "X-Plex-Platform": platform.system() or "Unknown",
        "X-Plex-Platform-Version": platform.release() or "Unknown",
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


def request(
    url: str,
    headers: dict[str, str],
    *,
    method: str = "GET",
    data: bytes | None = None,
    timeout: float = 15,
) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers=headers, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        detail = exc.read(500).decode("utf-8", "replace").strip()
        suffix = f": {detail}" if detail else ""
        raise ExportError(f"HTTP {exc.code} from {urllib.parse.urlsplit(url).netloc}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise ExportError(f"Could not reach {urllib.parse.urlsplit(url).netloc}: {reason}") from exc


def json_request(
    url: str,
    headers: dict[str, str],
    *,
    method: str = "GET",
    data: bytes | None = None,
    timeout: float = 15,
) -> Any:
    body, _ = request(url, headers, method=method, data=data, timeout=timeout)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ExportError(f"Plex returned invalid JSON from {urllib.parse.urlsplit(url).netloc}") from exc


def load_credentials() -> dict[str, str]:
    source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
    if not source.exists():
        return {}
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items() if v is not None}
    except (OSError, json.JSONDecodeError):
        return {}


def save_credentials(client_id: str, token: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(
        json.dumps({"client_id": client_id, "token": token}, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


def empty_incremental_cache(user: dict[str, Any], all_users: bool) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "updated_at": None,
        "account_id": str(user.get("id") or ""),
        "all_users": bool(all_users),
        "plex_servers": {},
        "tautulli": {},
        "metadata": {},
    }


def load_incremental_cache(
    path: Path,
    user: dict[str, Any],
    all_users: bool,
    *,
    ignore: bool = False,
) -> tuple[dict[str, Any], bool]:
    fresh = empty_incremental_cache(user, all_users)
    if ignore or not path.exists():
        return fresh, False
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"Warning: could not read incremental cache {path}; rebuilding it.")
        return fresh, False
    if not isinstance(cached, dict):
        return fresh, False
    if cached.get("schema_version") == "1.0":
        # Preserve downloaded history while invalidating only negative metadata
        # entries created before show-level recovery and corrected field semantics.
        metadata_cache = cached.get("metadata")
        if isinstance(metadata_cache, dict):
            for server_id, values in list(metadata_cache.items()):
                if isinstance(values, dict):
                    metadata_cache[server_id] = {
                        key: value
                        for key, value in values.items()
                        if not (
                            isinstance(value, dict)
                            and value.get("_exporter_source") == "not_found"
                        )
                    }
        cached["schema_version"] = CACHE_SCHEMA_VERSION
        print("Migrated the incremental cache and scheduled missing metadata for recovery.")
    elif cached.get("schema_version") != CACHE_SCHEMA_VERSION:
        print("Incremental cache schema changed; rebuilding it once.")
        return fresh, False
    if str(cached.get("account_id") or "") != str(user.get("id") or ""):
        print("Incremental cache belongs to another Plex account; rebuilding it.")
        return fresh, False
    if bool(cached.get("all_users")) != bool(all_users):
        print("Incremental cache scope changed; rebuilding it.")
        return fresh, False
    for key in ("plex_servers", "tautulli", "metadata"):
        if not isinstance(cached.get(key), dict):
            cached[key] = {}
    return cached, True


def save_incremental_cache(path: Path, cache: dict[str, Any]) -> None:
    """Atomically save private source data, never credentials or API keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cache["updated_at"] = utc_now()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def boolish(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "yes"}


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def child_value(raw: dict[str, Any], child: str, key: str) -> str:
    value = raw.get(child)
    if isinstance(value, list):
        value = value[0] if value else None
    return scalar(value.get(key)) if isinstance(value, dict) else ""


def safe_name(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in value).strip("._")
    return cleaned or "plex-user"


def listify(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def tabular_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    if isinstance(value, str) and text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    numeric = number(value)
    return int(numeric) if numeric is not None else None


def epoch_iso(value: Any, *, local: bool = False) -> str:
    timestamp = number(value)
    if timestamp is None or timestamp <= 0:
        return ""
    try:
        zone = None if local else dt.timezone.utc
        value_dt = dt.datetime.fromtimestamp(timestamp, zone)
        if local:
            value_dt = value_dt.astimezone()
        return value_dt.isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return ""


def normalized_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
