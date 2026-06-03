# Contributing to cc-cockpit

Thanks for your interest in improving cc-cockpit! Contributions of all kinds are welcome — bug reports, fixes, features, and docs.

## Ground rules

- Be respectful and constructive.
- **Never** include secrets or personal data (IPs, hostnames, SSH keys, real session paths/names) in issues, PRs, screenshots, or commits.
- Keep it dependency-light: the project is intentionally **Python standard library only** (server/collector) and **vanilla JS, single-file HTML** (UI). Please don't add runtime dependencies or a frontend build step without discussing it first.

## Dev setup

No build step. Clone, then run the server with the built-in sample data:

```bash
git clone https://github.com/<you>/gw-cc-cockpit cc-cockpit
cd cc-cockpit
CC_DEMO=1 python3 server.py     # open http://127.0.0.1:8910
```

`CC_DEMO=1` serves anonymized demo data and disables write-actions, so you can iterate on the UI/server without any real Claude Code sessions or SSH access. For a full local install (autostart service, dedicated key) use `./install.sh`.

Useful while developing:

```bash
python3 -m py_compile server.py enrich.py     # syntax check
CC_PORT=8911 CC_DEMO=1 python3 server.py       # run a throwaway instance on another port
```

## Project layout

| Path | What it is |
|------|------------|
| `server.py` | stdlib HTTP server: polls hosts, serves UI + `/api/*` endpoints |
| `enrich.py` | per-host data collection; runs locally and over SSH (`ssh host python3 - < enrich.py`) |
| `web/index.html` | the entire dashboard UI (HTML/CSS/vanilla JS) |
| `install.sh`, `scripts/add-host.sh` | installer and host onboarding |
| `dist/` | launchd / systemd service templates |

See the **Architecture** section in the [README](README.md) for the data flow.

## Coding style

- **Python**: stdlib only; keep functions small; prefer clarity over cleverness. Run `py_compile` before committing.
- **JS/CSS**: no frameworks, no bundler. Match the existing terminal/"mission-control" aesthetic — restrained palette, semantic status colors only, monospace for data.
- Keep all user-facing strings in **English**.

## Submitting changes

1. Branch from `main`.
2. Make focused commits with clear messages.
3. Test in demo mode (and against a real host if your change touches collection/SSH).
4. Open a PR using the template; describe what changed and how you verified it.

## License of contributions

By contributing, you agree that your contributions are licensed under the project's **GNU AGPL-3.0-or-later** (see [LICENSE](LICENSE)). Copyright in the project is held by GuniWeb moderne Medien GmbH; you retain copyright in your contributions while licensing them under AGPL-3.0.
