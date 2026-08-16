# Changelog

All notable user-facing changes will be documented here.

## 1.5.0 - 2026-08-17

- Rebranded and packaged the exporter as Histarr.
- Licensed Histarr under GNU GPLv3-only.
- Split the original single-file implementation into focused core, Plex, Tautulli, matching, analytics, combined-output, and application modules.
- Added installable `histarr` and `python -m histarr` entry points.
- Added incremental metadata recovery, JSONL output, deterministic insights, and expanded summary statistics.
- Corrected Tautulli duration/runtime semantics and marked estimated progress.
- Added stale-session anomaly exclusions while retaining raw totals.
- Preserved fractional Tautulli watched states.
- Added show-level genre recovery and explicit unavailable-metadata accounting.
- Excluded bulk Plex mark-watched/import clusters from binge analysis.
