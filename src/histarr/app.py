# SPDX-License-Identifier: GPL-3.0-only
"""Command-line orchestration for Histarr exports."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import json
import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

from .combined import build_combined_events, write_combined_exports
from .core import (
    CACHE_FILE_NAME, CACHE_SCHEMA_VERSION, INCREMENTAL_OVERLAP_SECONDS, LEGACY_CACHE_FILE_NAME,
    CONFIG_FILE, LEGACY_CONFIG_FILE, ExportError, empty_incremental_cache,
    load_incremental_cache, save_incremental_cache,
)
from .plex import (
    authenticate, connection_urls, fetch_history, merge_plex_history,
    normalize_record, resources, safe_name,
)
from .tautulli import (
    fetch_tautulli_history, merge_tautulli_history, tautulli_api_url,
    tautulli_call, write_tautulli_exports,
)


def export_tautulli(
    args: argparse.Namespace,
    user: dict[str, Any],
    cached_state: dict[str, Any] | None = None,
) -> tuple[
    tuple[Path, Path, Path, int],
    list[dict[str, Any]],
    dict[str, Any],
    str,
    str,
    int,
]:
    supplied_url = args.tautulli_url or os.environ.get("TAUTULLI_URL")
    if not supplied_url:
        supplied_url = input("Tautulli URL (for example http://localhost:8181): ").strip()
    api_url = tautulli_api_url(supplied_url)
    parsed_api_url = urllib.parse.urlsplit(api_url)
    if parsed_api_url.scheme == "http" and parsed_api_url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        print("Warning: this remote Tautulli URL uses unencrypted HTTP; HTTPS is recommended for API keys.")

    api_key = (
        args.tautulli_api_key
        or os.environ.get("TAUTULLI_APIKEY")
        or os.environ.get("TAUTULLI_API_KEY")
    )
    if not api_key:
        print("Find the API key in Tautulli: Settings > Web Interface > API.")
        if not args.no_browser:
            webbrowser.open(api_url.removesuffix("/api/v2"))
        api_key = getpass.getpass("Tautulli API key (input hidden): ").strip()
    if not api_key:
        raise ExportError("A Tautulli API key is required")

    print("Downloading detailed Tautulli session history...")
    try:
        server_info = tautulli_call(api_url, api_key, "get_server_info")
        if not isinstance(server_info, dict):
            server_info = {}
    except ExportError as exc:
        print(f"Warning: could not identify Tautulli's Plex server ({exc}); using time/item matching.")
        server_info = {}
    cached_history: list[dict[str, Any]] = []
    if (
        not args.full_refresh
        and isinstance(cached_state, dict)
        and cached_state.get("base_url") == api_url.removesuffix("/api/v2")
        and isinstance(cached_state.get("history"), list)
    ):
        cached_history = [row for row in cached_state["history"] if isinstance(row, dict)]
    last_epoch = max(
        (number(row.get("stopped") or row.get("date") or row.get("started")) or 0 for row in cached_history),
        default=0,
    )
    after_date = None
    if last_epoch > 0:
        after_date = dt.datetime.fromtimestamp(
            max(0, last_epoch - INCREMENTAL_OVERLAP_SECONDS), dt.timezone.utc
        ).date().isoformat()
        print(f"Using incremental Tautulli update from {after_date} (with a one-day overlap).")
    downloaded = fetch_tautulli_history(
        api_url,
        api_key,
        user_id=user.get("id"),
        username=str(user.get("username") or "") or None,
        all_users=args.all_users,
        after_date=after_date,
    )
    history = merge_tautulli_history(cached_history, downloaded)
    paths = write_tautulli_exports(
        args.output_dir.expanduser().resolve(),
        user,
        history,
        api_url=api_url,
        all_users=args.all_users,
    )
    return paths, history, server_info, api_url, api_key, len(downloaded)


def write_exports(
    output_dir: Path,
    user: dict[str, Any],
    server_results: list[dict[str, Any]],
    *,
    all_users: bool,
) -> tuple[Path, Path, Path, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    username = safe_name(str(user.get("username") or user.get("email") or "plex-user"))
    stem = f"plex-watch-history-{username}-{stamp}"
    json_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}.csv"
    report_path = output_dir / f"{stem}-report.txt"

    combined: list[dict[str, Any]] = []
    normalized: list[dict[str, str]] = []
    for result in server_results:
        server_public = result["server"]
        for raw in result.get("records", []):
            combined.append({"server": server_public, "record": raw})
            normalized.append(normalize_record(raw, server_public))

    payload = {
        "exported_at": utc_now(),
        "scope": "all users visible to the server owner" if all_users else "signed-in user only",
        "account": {
            "id": user.get("id"),
            "username": user.get("username"),
            "email": user.get("email"),
        },
        "servers": [
            {
                "server": result["server"],
                "status": result["status"],
                "record_count": len(result.get("records", [])),
                "error": result.get("error"),
            }
            for result in server_results
        ],
        "history": combined,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fields = list(normalized[0].keys()) if normalized else list(normalize_record({}, {}).keys())
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(normalized)

    report_lines = [
        "Histarr Plex history report",
        f"Exported: {payload['exported_at']}",
        f"Scope: {payload['scope']}",
        f"Records: {len(combined)}",
        "",
    ]
    for result in server_results:
        name = result["server"].get("name") or "Unnamed server"
        count = len(result.get("records", []))
        line = f"- {name}: {result['status']} ({count} records)"
        if result.get("error"):
            line += f" — {result['error']}"
        report_lines.append(line)
    report_lines += [
        "",
        "Notes:",
        "- Exports contain no Plex authentication tokens.",
        "- Results include only history still retained by each reachable Plex Media Server.",
        "- A shared server may deny access to its administrative history endpoint.",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return json_path, csv_path, report_path, len(combined)


def export(args: argparse.Namespace) -> int:
    client_id, account_token, user = authenticate(force_login=args.login, no_browser=args.no_browser)
    output_dir = args.output_dir.expanduser().resolve()
    cache_path = output_dir / CACHE_FILE_NAME
    legacy_cache_path = output_dir / LEGACY_CACHE_FILE_NAME
    cache_read_path = (
        cache_path
        if cache_path.exists() or not legacy_cache_path.exists()
        else legacy_cache_path
    )
    cache, cache_loaded = load_incremental_cache(
        cache_read_path,
        user,
        args.all_users,
        ignore=args.full_refresh,
    )
    if cache_loaded:
        print(f"Loaded incremental cache from {cache_read_path}.")
        if cache_read_path == legacy_cache_path:
            print(f"Migrating the legacy cache to {cache_path.name}.")
    servers = resources(client_id, account_token)
    if not servers:
        raise ExportError("No Plex Media Servers are available to this account")

    print(f"Found {len(servers)} Plex Media Server(s).")
    results: list[dict[str, Any]] = []
    plex_downloaded_events = 0
    user_id = str(user.get("id", ""))
    for index, server in enumerate(servers, 1):
        name = str(server.get("name") or f"Server {index}")
        owned = boolish(server.get("owned"))
        account_id = None if args.all_users else ("1" if owned else user_id)
        server_public = {
            "name": name,
            "clientIdentifier": server.get("clientIdentifier"),
            "owned": owned,
        }
        print(f"[{index}/{len(servers)}] Exporting {name}...", end=" ", flush=True)
        errors: list[str] = []
        downloaded: list[dict[str, Any]] | None = None
        server_id = str(server.get("clientIdentifier") or "")
        cached_server = cache.get("plex_servers", {}).get(server_id, {})
        cached_records = (
            [row for row in cached_server.get("records", []) if isinstance(row, dict)]
            if isinstance(cached_server, dict) and not args.full_refresh
            else []
        )
        last_viewed = max((number(row.get("viewedAt")) or 0 for row in cached_records), default=0)
        after_epoch = max(0, last_viewed - INCREMENTAL_OVERLAP_SECONDS) if last_viewed else None
        server_token = str(server.get("accessToken") or account_token)
        for base_url in connection_urls(server):
            try:
                downloaded = fetch_history(
                    base_url,
                    client_id,
                    server_token,
                    account_id=account_id,
                    after_epoch=after_epoch,
                )
                break
            except (ExportError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
        if downloaded is None:
            error = errors[-1] if errors else "Plex supplied no usable server connection"
            if cached_records:
                records = cached_records
                print(f"using {len(records)} cached record(s) ({error})")
                status = "cached_after_failure"
            else:
                records = []
                print(f"skipped ({error})")
                status = "failed"
            results.append(
                {
                    "server": server_public,
                    "status": status,
                    "error": error,
                    "records": records,
                    "_connection_urls": connection_urls(server),
                    "_token": server_token,
                }
            )
        else:
            plex_downloaded_events += len(downloaded)
            records = merge_plex_history(cached_records, downloaded)
            if cached_records:
                print(f"{len(records)} total record(s), {len(downloaded)} fetched in overlap")
            else:
                print(f"{len(records)} record(s)")
            results.append(
                {
                    "server": server_public,
                    "status": "ok",
                    "error": None,
                    "records": records,
                    "_connection_urls": connection_urls(server),
                    "_token": server_token,
                }
            )
        cache.setdefault("plex_servers", {})[server_id] = {
            "server": server_public,
            "records": records,
        }

    json_path, csv_path, report_path, count = write_exports(
        output_dir, user, results, all_users=args.all_users
    )
    save_incremental_cache(cache_path, cache)
    print(f"\nExported {count} record(s):")
    print(f"  JSON:   {json_path}")
    print(f"  CSV:    {csv_path}")
    print(f"  Report: {report_path}")

    if args.tautulli or args.tautulli_url or args.tautulli_api_key:
        (
            tautulli_paths,
            tautulli_history,
            tautulli_server_info,
            tautulli_api_url_value,
            tautulli_api_key,
            tautulli_downloaded_events,
        ) = export_tautulli(args, user, cache.get("tautulli"))
        cache["tautulli"] = {
            "base_url": tautulli_api_url_value.removesuffix("/api/v2"),
            "server_info": tautulli_server_info,
            "history": tautulli_history,
        }
        t_json, t_csv, t_report, t_count = tautulli_paths
        print(f"\nExported {t_count} detailed Tautulli session record(s):")
        print(f"  JSON:   {t_json}")
        print(f"  CSV:    {t_csv}")
        print(f"  Report: {t_report}")
        print("\nJoining Plex and Tautulli events and calculating statistics...")
        combined_events, metadata_errors, metadata_cache, metadata_stats = build_combined_events(
            results,
            tautulli_history,
            tautulli_server_info,
            client_id,
            metadata_cache=cache.get("metadata"),
            tautulli_api_url_value=tautulli_api_url_value,
            tautulli_api_key=tautulli_api_key,
            metadata_workers=args.metadata_workers,
            refresh_metadata=args.full_refresh,
        )
        cache["metadata"] = metadata_cache
        incremental_info = {
            "enabled": True,
            "cache_loaded": cache_loaded,
            "full_refresh": bool(args.full_refresh),
            "cache_file": CACHE_FILE_NAME,
            "overlap_seconds": INCREMENTAL_OVERLAP_SECONDS,
            "plex_downloaded_events": plex_downloaded_events,
            "plex_total_events": sum(len(result.get("records", [])) for result in results),
            "tautulli_downloaded_events": tautulli_downloaded_events,
            "tautulli_total_events": len(tautulli_history),
        }
        combined_csv, combined_json, combined_jsonl, summary_json, insights_md = write_combined_exports(
            output_dir,
            user,
            combined_events,
            metadata_errors=metadata_errors,
            metadata_stats=metadata_stats,
            incremental_info=incremental_info,
        )
        save_incremental_cache(cache_path, cache)
        print(f"\nCreated {len(combined_events)} joined analytical event(s):")
        print(f"  CSV:     {combined_csv}")
        print(f"  JSON:    {combined_json}")
        print(f"  JSONL:   {combined_jsonl}")
        print(f"  Summary: {summary_json}")
        print(f"  Insights:{insights_md}")
        print(f"  Cache:   {cache_path}")
    return 0 if any(result["status"] in {"ok", "cached_after_failure"} for result in results) else 2


def logout() -> int:
    removed = False
    for path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
        if path.exists():
            path.unlink()
            removed = True
    if removed:
        print("Removed the locally saved Plex token. You can also revoke the app in Plex Authorized Devices.")
    else:
        print("No locally saved Plex token was found.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="histarr",
        description="Sign in with Plex and export retained watch history to CSV, JSON, and JSONL."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "histarr-exports",
        help="export directory (default: ./histarr-exports)",
    )
    parser.add_argument("--login", action="store_true", help="ignore a saved token and sign in again")
    parser.add_argument("--logout", action="store_true", help="delete the locally saved Plex token and exit")
    parser.add_argument("--no-browser", action="store_true", help="print the Plex login URL instead of opening it")
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="on owned servers, export all users' history instead of only yours",
    )
    parser.add_argument(
        "--tautulli",
        action="store_true",
        help="also export detailed Tautulli session history (prompts for URL and API key)",
    )
    parser.add_argument(
        "--tautulli-url",
        help="Tautulli base URL, including any HTTP root (also enables Tautulli export)",
    )
    parser.add_argument(
        "--tautulli-api-key",
        help="Tautulli API key (prefer the hidden prompt or TAUTULLI_APIKEY environment variable)",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="ignore incremental history/metadata caches and rebuild them from the sources",
    )
    parser.add_argument(
        "--metadata-workers",
        type=int,
        default=4,
        choices=range(1, 9),
        metavar="1-8",
        help="parallel Tautulli metadata recovery requests (default: 4)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.logout:
        return logout()
    if args.all_users:
        answer = input("This may export other users' viewing data. Type EXPORT ALL to continue: ")
        if answer != "EXPORT ALL":
            print("Cancelled.")
            return 1
    try:
        return export(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except ExportError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
