# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| Latest release (1.0.x) | ✅ |
| Older tags | ❌ |

Only the latest release receives security fixes. Always test against `main` or
the newest tag before reporting.

## Reporting a Vulnerability

**Do not open a public issue.** Report privately through either channel:

- **GitHub Private Vulnerability Reporting** (preferred): use the
  ["Report a vulnerability"](https://github.com/alesanfe/conflux/security/advisories/new)
  button on the Security tab.
- If PVR is unavailable, contact the maintainer via the email on the repository
  profile, with `conflux security` in the subject.

### What to include

- Affected version / commit and platform (Windows / Linux / macOS).
- Reproduction steps or PoC, and observed vs expected impact.
- Affected component: HTTP surface, config parsing, credential discovery,
  installer/autostart, release pipeline, or supply chain.

### Response targets

| Stage | Target |
|---|---|
| Acknowledgement | 72 hours |
| Initial triage (severity via CVSS 4.0 / 3.1) | 7 days |
| Fix or mitigation for confirmed high/critical | 30 days |

We follow **coordinated disclosure**: we will agree a publication date with the
reporter (default 90 days after acknowledgement). CVEs are requested through
GitHub Security Advisories when a fix ships.

### Found a secret accidentally committed?

Report it as a vulnerability (do not post it publicly). We will rotate it and
purge history; tell us the file and approximate commit so we can confirm the
scope.

### Out of scope

- Denial-of-service against third-party upstreams (Conflux does not own them).
- Tests against Stremio or source providers beyond their published APIs —
  follow their ToS; report issues *in Conflux's handling*, not theirs.
- Attacks requiring full control of the local machine (out of threat model —
  Conflux assumes a single-user host; see `docs/threat-model.md`).

## Credit

Reporters of confirmed vulnerabilities are credited in the advisory and the
CHANGELOG unless they prefer anonymity.
