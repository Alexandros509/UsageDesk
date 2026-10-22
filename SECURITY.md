# Security and privacy

UsageDesk handles authentication material and launches user-selected local files.

## Data handling

- Claude/Codex credentials: current-user Windows DPAPI with restricted local ACLs.
- Grok: reads the CLI credentials for billing and renews them at the fixed official
  `https://auth.x.ai/oauth2/token` endpoint. Uses the CLI-compatible shared lock,
  rechecks credentials after acquiring it, and atomically replaces the CLI file
  with user/SYSTEM-only permissions. Only the link preference is kept in the app's vault.
  A response containing rotated tokens is persisted even if the UI disconnects
  during the exchange. Failed writes retain the response in memory for a retry;
  closing the app before a successful write can still require CLI sign-in again.
- No UsageDesk telemetry or collection server. Provider requests go directly to
  configured service endpoints over HTTPS.
- HTTP redirects and automatic proxy environment configuration are disabled.
- OAuth callbacks bind to loopback and check transaction state.
- Local logout does not revoke tokens on provider servers. Process memory is not
  guaranteed to be completely overwritten on logout.

## Reporting a problem

For UI bugs, include the app version, Windows version, steps and a redacted image.
Never attach tokens, complete OAuth callback URLs, `auth.json`, DPAPI files, user
configuration backups or unredacted network dumps.

For credential-exposure issues, use GitHub private vulnerability reporting when
available. If unavailable, request a private contact route without posting exploit
details or secrets in a public issue.

## Publication boundaries

The repository excludes runtime credentials, user settings, local reference clones,
test output, Python environments, logs, dumps, binaries and private working notes.
Public OAuth client IDs and endpoint constants are protocol configuration, not
access tokens or client secrets. Test credentials are dummy data.

Only register files you intend to open or execute. File associations may execute
code, and a child application can continue running after UsageDesk exits.
