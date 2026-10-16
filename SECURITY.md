# Security Policy

## Reporting a vulnerability

If you find a security issue in this repo, report it privately rather than opening a public issue. Use GitHub's private vulnerability reporting: open this repo's Security tab and choose Report a vulnerability. There is no email address for reports.

Include what you found, how to reproduce it, and its likely impact. Expect an acknowledgment within a few days. That is a real intention and not an SLA, and it does not promise a fix on any particular timeline.

## Scope

This is a solo practitioner's repo, maintained alongside client work and not under an SLA. Treat this policy as "there is a real person who wants to know," not as a guarantee of response time or process.

The repo is methodology and tooling. It is documents and local command-line tools, not a hosted service, so the attack surface is small. A useful report is concrete: a tool that writes outside its target folder, a command injection through a typed value, or a committed secret.

## What's out of scope

- `video-use`, the upstream skill that `tools/video-editor/` wraps. Report issues in it upstream to Browser Use.
- Anything in a third-party dependency. Report it to that project.
