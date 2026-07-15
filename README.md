# HunterX

[![CI](https://github.com/sssss928/hunterX/actions/workflows/ci.yml/badge.svg)](https://github.com/sssss928/hunterX/actions/workflows/ci.yml)
[![GitHub release](https://img.shields.io/github/v/release/sssss928/hunterX?style=flat-square)](https://github.com/sssss928/hunterX/releases)

Hunter is a maintained fork of [`bouob/tickets_hunter`](https://github.com/bouob/tickets_hunter). This repository keeps the inherited Python ticket automation codebase buildable, testable, and releasable under the Hunter project identity.

## Fork Notice

This repository is not the upstream Tickets Hunter project. It is a HunterX fork maintained at [`sssss928/hunterX`](https://github.com/sssss928/hunterX). Upstream credit remains with the original Tickets Hunter maintainers and contributors. HunterX uses its own versioning, release assets, issue tracker, CI, and documentation.

Current fork version: `HunterX (0.2.3)`

## Legal And Ethical Use

Use this project only for legal, educational, research, and personal learning purposes. You are responsible for complying with ticketing platform terms, local law, and event rules.

Hunter does not accept requests to add or improve:

- captcha bypass or anti-bot evasion
- proxy pools, account pools, fingerprint hiding, or rate-limit evasion
- bulk purchasing, resale automation, unfair queue manipulation, or automated payment flows
- features designed to bypass platform access controls or risk systems

Existing inherited automation code is maintained only for stability, safety, testing, packaging, and documentation.

For TixCraft-specific timing, release-ticket, and multi-instance troubleshooting notes, see
[`guide/tixcraft-practical-guide.md`](guide/tixcraft-practical-guide.md).

## Install From Source

Requirements:

- Windows, macOS, or Linux for source usage
- Python `3.11.9`
- Chrome or another supported browser for runtime usage

```bash
python -m pip install --upgrade pip
pip install -r requirement.txt
```

Launch the settings UI from source:

```bash
python src/settings.py
```

## Development

Install runtime and development dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirement.txt -r requirements-dev.txt
```

Run local checks:

```bash
python -m compileall src
ruff check src tests scripts
pytest
pip-audit -r requirement.txt
```

## Windows Build

The release package is built with PyInstaller and contains the Windows executables plus shared assets:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -Version 0.2.3
```

Expected output:

```text
dist/release/hunterX_windows_0.2.3.zip
```

## GitHub Releases

Users can download packaged Windows builds from:

[`https://github.com/sssss928/hunterX/releases`](https://github.com/sssss928/hunterX/releases)

For `v0.2.3`, the expected asset is:

```text
hunterX_windows_0.2.3.zip
```

Extract the ZIP on a Windows computer and run `settings.exe` to configure the application.

## Known Limits

- Windows packaged releases are the primary supported binary artifact.
- Linux and macOS source usage may work, but packaged binaries are not part of the initial fork release.
- Some inherited documentation may describe upstream behavior; Hunter-specific release and contribution processes are authoritative in this README, `CONTRIBUTING.md`, and `SECURITY.md`.
- Runtime behavior depends on browser versions, platform UI changes, and local environment configuration.

## Repository Layout

```text
src/                 Python source code and runtime assets
src/platforms/       Platform-specific inherited modules
build_scripts/       PyInstaller spec files and legacy local build helpers
scripts/             CI, release, benchmark, and packaging helper scripts
tests/               Metadata, config, import smoke, release, and benchmark tests
.github/workflows/   CI, release, CodeQL, and dependency review workflows
docs/                Inherited technical notes
guide/               Inherited user guides
```

## Report Issues

Use the Hunter issue tracker:

[`https://github.com/sssss928/hunterX/issues`](https://github.com/sssss928/hunterX/issues)

Do not include secrets, cookies, tokens, personal data, payment details, screenshots containing private account data, or platform session identifiers in public issues.

Security reports should follow [`SECURITY.md`](SECURITY.md).

## License

This fork preserves the upstream GPL license terms. See [`LICENSE`](LICENSE).
