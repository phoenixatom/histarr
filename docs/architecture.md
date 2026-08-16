# Architecture

Histarr is intentionally a small, dependency-free Python application.

## Runtime flow

1. Authenticate through Plex's PIN flow or validate the saved token.
2. Discover available Plex Media Servers and incrementally fetch retained history.
3. Optionally fetch incremental Tautulli history for the signed-in user.
4. Recover current item and show metadata from Plex, then use Tautulli as a fallback.
5. Match Plex and Tautulli rows one-to-one, retaining unmatched rows.
6. Calculate sequence, completion, time, anomaly, rewatch, and binge fields.
7. Regenerate stable analytical outputs and atomically update the private cache.

## Source layout

- `src/histarr/core.py` contains shared constants, HTTP, configuration, cache, and scalar helpers.
- `src/histarr/plex.py` contains Plex authentication, discovery, history, and metadata access.
- `src/histarr/tautulli.py` contains Tautulli history, metadata fallback, and raw exports.
- `src/histarr/matching.py` joins source records and constructs normalized events.
- `src/histarr/analytics.py` calculates sequence metrics, summaries, and deterministic insights.
- `src/histarr/combined.py` enriches events and writes stable analytical outputs.
- `src/histarr/app.py` orchestrates exports and defines command-line options.
- `src/histarr/cli.py` remains a small compatibility entry point.
- `src/histarr/__main__.py` supports `python -m histarr`.
- `tests/` contains synthetic regression tests and performs no live API calls.
- `scripts/macos/` contains optional Finder launchers.

Modules remain dependency-free and communicate through explicit imports. Refactors should preserve the public CLI and output schemas.

## Trust boundaries

- Plex passwords stay on Plex's official site.
- Plex tokens are persisted only in the private config file.
- Tautulli API keys are accepted through a hidden prompt, environment variable, or explicit CLI flag and are never persisted by Histarr.
- Exports and the incremental cache are private user data and never belong in the repository.
