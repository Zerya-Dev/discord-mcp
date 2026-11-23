# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/Zerya-Dev/zerya-discord-mcp/security/advisories/new) or by email
to us@zerya.dev. Do not open a public issue. We aim to reply within 3 working days and to ship a fix for
confirmed issues within 14 days.

## Supported versions

Only the latest release receives security fixes.

## Scope and threat model

In scope: the OAuth authorization server and sign-in page, access control (a member reading channels, threads or
messages they cannot see on Discord, including through facets, hints, counts or mentions), the `send` tool,
token storage, and anything that lets message content escape the untrusted-data wrapper.

Known limits, by design:

- Message content is untrusted input for the agent. We wrap it and instruct the agent, but no wrapper fully
  prevents prompt injection; keep `ZDM_WRITE_ENABLED=false` unless members need posting.
- Anyone who obtains a member's `/mcp login` code within its 10-minute lifetime can connect as that member. The
  sign-in page shows where the code is sent; restrict it with `ZDM_OAUTH_REDIRECT_HOSTS`.
- Rate limits are per process and in memory.
