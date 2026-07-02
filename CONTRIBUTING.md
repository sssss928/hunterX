# Contributing To Hunter

Thanks for helping maintain Hunter. This repository is a fork of `bouob/tickets_hunter`, but contributions here should target the Hunter codebase, CI, packaging, documentation, tests, and safety posture.

## Scope

Accepted contribution areas:

- bug fixes that improve stability or error handling
- tests, benchmarks, packaging, CI, and release automation
- documentation corrections for Hunter
- dependency updates and security hardening
- refactors that reduce import side effects or make configuration safer

Not accepted:

- captcha bypass improvements
- anti-bot evasion, fingerprint hiding, proxy pools, account pools, or rate-limit evasion
- bulk purchasing, resale automation, automated payment, or unfair queue manipulation
- changes that bypass ticketing platform restrictions or access controls

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/hunter.git
cd hunter
git remote add upstream https://github.com/sssss928/hunter.git
python -m pip install --upgrade pip
pip install -r requirement.txt -r requirements-dev.txt
```

## Branches

Use focused branches:

- `feature/<name>` for user-facing improvements
- `fix/<name>` for bug fixes
- `chore/<name>` for maintenance
- `refactor/<name>` for internal cleanup
- `docs/<name>` for documentation

## Commits

Use Conventional Commits:

```text
type(scope): short description
```

Examples:

```text
fix(config): handle invalid settings json with readable error
ci(actions): add windows build smoke artifact
docs(project): document Hunter release flow
```

## Checks

Run these before opening a pull request:

```bash
python -m compileall src
ruff check src tests scripts
pytest
pip-audit -r requirement.txt
```

For Windows packaging changes:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -Version 0.1.0
```

## Pull Requests

Open pull requests against `main` in [`sssss928/hunter`](https://github.com/sssss928/hunter). Include:

- what changed
- why it changed
- local test results
- security impact
- release impact
- rollback plan

Do not include secrets, account identifiers, cookies, tokens, payment data, or screenshots containing private information.

## Upstream Credit

Hunter inherits code and documentation from [`bouob/tickets_hunter`](https://github.com/bouob/tickets_hunter). Upstream credit should be explicit and should not imply this repository is the upstream project.
