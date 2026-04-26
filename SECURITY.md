# Security Policy

## Supported Versions

Security fixes are applied to the latest release line (`v0.8.x` at this time).
Older tags may not receive backported fixes.

## Reporting a Vulnerability

Please report vulnerabilities privately before public disclosure.

Preferred contact:

- GitHub Security Advisories (private report)
- or repository maintainer contact channel

Include:

- affected version/tag
- reproduction steps
- impact scope
- logs/sanitized evidence

## Disclosure and Fix Policy

1. Acknowledge report and reproduce.
2. Prepare fix and regression test.
3. Publish patched release and release notes.
4. Disclose details after fix is available.

## Operational Security Notes

- Use root-owned `0600` config for `/etc/raspi-sentinel/config.toml`.
- Do not embed long-lived secrets directly in command strings.
- Webhook URLs and tokens should not be exposed in logs/events.
- Use `validate-config --strict` before enabling timers on production hosts.

Japanese guide: [docs/SECURITY.ja.md](docs/SECURITY.ja.md)
