# Security policy

## Reporting a vulnerability

Do not disclose credentials, tokens, private viewing history, or exploitable details in a public issue. Use GitHub's private vulnerability-reporting feature after it is enabled for the repository.

Include the affected Histarr version, impact, reproduction steps using synthetic data, and any proposed mitigation. Please do not test against systems you do not own or administer.

## Credential handling

Histarr stores the Plex token locally with user-only permissions, keeps the Tautulli API key in memory for the current run, and excludes credentials from generated exports and caches. A credential exposed in logs, screenshots, issues, or commits should be revoked immediately at its source.
