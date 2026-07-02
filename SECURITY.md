# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| `0.1.x` | Yes |
| older fork snapshots | No |

## Reporting A Vulnerability

Please report security issues privately to the repository maintainer instead of opening a public issue with sensitive details. If private contact is unavailable, open a minimal public issue asking for a secure contact path and do not include exploit details, credentials, cookies, tokens, account identifiers, payment data, or personal data.

Useful reports include:

- affected version or commit
- high-level impact
- reproduction steps that do not expose secrets
- whether the issue affects CI, packaging, dependencies, or runtime behavior

## Public Issue Limits

Do not post sensitive information in GitHub Issues, Discussions, pull requests, logs, screenshots, or attachments.

Hunter does not accept vulnerability reports or feature requests that ask maintainers to add, improve, or document captcha bypass, anti-bot evasion, proxy or account pools, rate-limit evasion, platform restriction bypass, unfair queue manipulation, bulk purchasing, resale automation, or automated payment behavior.

## Dependency Security

CI runs `pip-audit -r requirement.txt`. If a vulnerable dependency cannot be upgraded without breaking compatibility, the limitation must be documented in this file or `docs/security-notes.md` before any ignore is considered.
