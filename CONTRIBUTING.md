# Contributing to Histarr

Thanks for helping improve Histarr.

## Development setup

1. Use Python 3.10 or newer.
2. Create a virtual environment: `python3 -m venv .venv`.
3. Activate it and run `python -m pip install -e .`.
4. Run all four test scripts listed in the README before opening a pull request.

Histarr deliberately has no runtime dependencies. Discuss additions that introduce a dependency before implementing them.

## Pull requests

- Keep changes focused and explain user-visible behavior.
- Add regression coverage for bug fixes.
- Preserve raw source data and make derived assumptions explicit.
- Never include real Plex tokens, Tautulli keys, IP addresses, watch history, or export caches.
- Update the README, data model, or changelog when behavior or schemas change.

By submitting a contribution, you agree that it may be distributed under Histarr's GNU GPLv3-only license.

## Reporting bugs

Use the bug-report template and include sanitized output only. For security issues, follow [SECURITY.md](SECURITY.md).
