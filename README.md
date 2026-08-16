# Histarr

Histarr exports the watch history retained by Plex and optionally enriches it with detailed Tautulli sessions. It produces private, analysis-ready CSV, JSON, JSONL, summary, and Markdown files without sending viewing data to an LLM.

Histarr is an independent community project and is not affiliated with Plex, Tautulli, or their maintainers.

## Highlights

- Official Plex PIN sign-in; Histarr never receives your Plex password.
- Optional Tautulli API integration for completion, active play time, pauses, player details, and transcode information.
- One-to-one Plex/Tautulli event matching with unmatched history preserved.
- Runtime and metadata recovery from Plex, show-level genre inheritance, then Tautulli fallback.
- Incremental updates with overlap-safe deduplication and a private local cache.
- Explicit anomaly flags, clean and raw watch-time totals, rewatch metrics, and corrected binge detection.
- Stable CSV, JSON, JSONL, `summary.json`, and deterministic `insights.md` outputs.
- Dependency-free at runtime; Python 3.10 or newer is required.

## Install from a clone

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
histarr --help
```

You can also run it without installation:

```sh
PYTHONPATH=src python3 -m histarr
```

## Usage

Plex history only:

```sh
histarr
```

Plex plus Tautulli:

```sh
histarr --tautulli-url https://tautulli.example.com
```

Histarr opens Plex's official sign-in page when needed. For Tautulli, it securely prompts for the API key. Environment variables are supported for unattended local runs:

```sh
TAUTULLI_URL=https://tautulli.example.com \
TAUTULLI_APIKEY=your-key \
histarr --tautulli
```

Command-line API keys can remain in shell history, so prefer the hidden prompt or environment variable.

## Outputs

The default output directory is `./histarr-exports`. With Tautulli enabled, the stable analytical files are:

- `combined_history.csv` — one normalized row per playback event.
- `combined_history.json` — events plus field definitions and data-quality context.
- `combined_history.jsonl` — streaming-friendly compact JSON objects.
- `summary.json` — compact statistics, distributions, definitions, and quality metrics.
- `insights.md` — deterministic readable insights; no LLM or external AI call.

Raw timestamped Plex and Tautulli exports are retained alongside these files. See [the data model](docs/data-model.md) for calculated-field semantics.

## Privacy and security

Your exports can contain viewing behavior, timestamps, usernames, player names, and IP addresses. Do not commit or publish them.

- Plex credentials are stored with user-only permissions in `~/.config/histarr/credentials.json`.
- Plex tokens and Tautulli API keys are never written to exports or the incremental cache.
- Runtime output directories, caches, credentials, environment files, and `local-data/` are excluded by `.gitignore`.
- `histarr --logout` removes Histarr and legacy exporter credential files.

Existing users are migrated from `~/.config/plex-history-exporter/credentials.json` and `.plex-history-exporter-cache.json` automatically.

## Important data semantics

- Missing progress is unknown, never assumed to mean fully watched.
- Tautulli session duration is not media runtime.
- Estimated progress is marked separately from exact playback offsets.
- Implausibly long sessions remain in raw outputs but are excluded from clean watch-time totals.
- Near-simultaneous Plex-only episode rows caused by mark-watched/import operations remain in history but are excluded from binge statistics.
- Current metadata may differ from metadata that existed when playback occurred.

## Development

```sh
python3 tests/test_metadata_retry.py
python3 tests/test_incremental_export.py
python3 tests/test_tautulli_export.py
python3 tests/test_combined_export.py
PYTHONPATH=src python3 -m histarr --help
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [the architecture notes](docs/architecture.md).

## License

Histarr is licensed under the [GNU General Public License v3.0 only](LICENSE). Commercial use is allowed, but anyone who distributes Histarr or a modified version must provide the corresponding source under GPLv3 and preserve the same freedoms for recipients.
