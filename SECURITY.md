# Security Policy

## Supported versions

Security fixes go into the latest release only. That is the newest
`service-v*`, `admin-v*`, `sdk-v*` and `js-sdk-v*` tags, which are always
cut together (see `CHANGELOG.md`). If you run an older release, upgrade.

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting:
<https://github.com/sidxz/duar/security/advisories/new>

Do not open a public issue or pull request for a security problem.
You will get an acknowledgement within 5 working days, and a fix or a
mitigation plan once the report is confirmed.

## What the service already does

The deployed hardening (headers, rate limits, token lifecycle, audit log)
is documented in `docs/security.md`, with a penetration-test harness under
`pentest/`.
