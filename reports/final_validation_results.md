# Final Validation Results

## Commands

| Command | Result | Evidence |
| --- | ---: | --- |
| `python.exe -m compileall src tests scripts` | 0 | Listing 'tests'... / Listing 'tests\\benchmarks'... / Listing 'scripts'... |
| `python.exe -m pytest -q` | 0 | 1 empty file skipped. / Coverage XML written to file coverage.xml / 102 passed in 13.87s |
| `python.exe -m pytest --cov=src --cov-branch --cov-report=term-missing --cov-report=xml:coverage.xml` | 0 | 1 empty file skipped. / Coverage XML written to file coverage.xml / ============================ 102 passed in 14.60s ============================= |
| `python.exe -m ruff check src tests scripts` | 0 | All checks passed! |
| `python.exe -m mypy` | 0 | Success: no issues found in 4 source files |
| `python.exe -m bandit -c pyproject.toml -r src` | 1 | Low: 253 / Medium: 0 / High: 0 / Files skipped (0): |
| `python.exe -m pip_audit -r requirement.txt` | 0 | No known vulnerabilities found |
| `C:/Users/karry.wei/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe --check src/www/settings.js` | 0 |  |
| `python.exe -c import sys, importlib; sys.path.insert(0, 'src'); mods=['nodriver_tixcraft','platforms.cityline','platforms.famiticket','platforms.fansigo','platforms.funone','platforms.hkticketing','platforms.ibon','platforms.kham','platforms.kktix','platforms.ticketplus','platforms.tixcraft']; [importlib.import_module(m) for m in mods]; print('imported', len(mods))` | 0 | imported 11 |
| `python.exe -c <replacement-character scan>` | 0 | replacement_char_files= 0 |
| `python.exe -m pytest -q --no-cov tests/test_refresh_timing.py tests/test_platform_timing_gate.py tests/test_common_async.py` | 0 | ....................................                                     [100%] / 36 passed in 2.00s |
| `python.exe -m PyInstaller build_scripts/nodriver_tixcraft.spec --clean --noconfirm` | 0 | 28990 INFO: Building COLLECT COLLECT-00.toc / 31361 INFO: Building COLLECT COLLECT-00.toc completed successfully. / 31366 INFO: Build complete! The results are available in: C:\Users\karry.wei\Documents\Codex\2026-07-13\hunterx-source-0-1-6-zip\dist |
| `python.exe -m PyInstaller build_scripts/settings.spec --clean --noconfirm` | 0 | 27067 INFO: Building COLLECT COLLECT-00.toc / 29329 INFO: Building COLLECT COLLECT-00.toc completed successfully. / 29342 INFO: Build complete! The results are available in: C:\Users\karry.wei\Documents\Codex\2026-07-13\hunterx-source-0-1-6-zip\dist |
| `python.exe -c  from __future__ import annotations import os, shutil, zipfile from pathlib import Path root = Path.cwd() package_dir = root / 'dist' / 'hunterX' release_dir = root / 'dist' / 'release' artifact = release_dir / 'hunterX_windows_0.2.0.zip' if package_dir.exists():     shutil.rmtree(package_dir) package_dir.mkdir(parents=True, exist_ok=True) release_dir.mkdir(parents=True, exist_ok=True) shutil.copy2(root / 'dist' / 'nodriver_tixcraft' / 'nodriver_tixcraft.exe', package_dir / 'nodriver_tixcraft.exe') shutil.copy2(root / 'dist' / 'settings' / 'settings.exe', package_dir / 'settings.exe') for source in [root / 'dist' / 'nodriver_tixcraft' / '_internal', root / 'dist' / 'settings' / '_internal']:     if source.exists():         dest = package_dir / '_internal'         dest.mkdir(exist_ok=True)         for item in source.iterdir():             target = dest / item.name             if item.is_dir():                 if target.exists(): shutil.rmtree(target)                 shutil.copytree(item, target)             else:                 shutil.copy2(item, target) for rel in ['src/assets', 'src/www', 'guide']:     source = root / rel     if source.exists():         shutil.copytree(source, package_dir / source.name) for rel in ['build_scripts/README_Release.txt', 'README.md', 'CHANGELOG.md', 'LEGAL_NOTICE.md']:     source = root / rel     if source.exists():         shutil.copy2(source, package_dir / source.name) if artifact.exists():     artifact.unlink() with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:     for path in sorted(package_dir.rglob('*')):         if path.is_file():             zf.write(path, path.relative_to(package_dir).as_posix()) print(artifact) print(artifact.stat().st_size) ` | 0 | C:\Users\karry.wei\Documents\Codex\2026-07-13\hunterx-source-0-1-6-zip\dist\release\hunterX_windows_0.2.0.zip / 200851272 |
| `dist/hunterX/nodriver_tixcraft.exe --help` | 0 |                         9222) /   --mcp_connect PORT    Connect to existing Chrome on specified port (e.g., /                         --mcp_connect 9222) |
| `python.exe -c import zipfile, pathlib; p=pathlib.Path('dist/release/hunterX_windows_0.2.0.zip'); z=zipfile.ZipFile(p); bad=z.testzip(); print('bad=', bad); print('entries=', len(z.infolist()))` | 0 | bad= None / entries= 207 |
| `python.exe -c import hashlib, pathlib; p=pathlib.Path('dist/release/hunterX_windows_0.2.0.zip'); print(hashlib.sha256(p.read_bytes()).hexdigest())` | 0 | 75a3e4613f91043b9dff0839575bf4b9ffb9ddb56b5b170236666baa733a6284 |
| `python.exe tests/benchmarks/audit_performance.py --output work/final_performance_audit.json --markdown work/final_performance_audit.md --samples 5 --iterations 1000` | 0 |  |

## Repeated Tests

- Command repeated 20 times: `python -m pytest -q --no-cov tests/test_refresh_timing.py tests/test_platform_timing_gate.py tests/test_common_async.py`.
- Result: 20/20 passed, each run 36 tests.

## Packaging

- Direct PyInstaller builds passed for `nodriver_tixcraft` and `settings`.
- Manual Windows artifact package created `dist/release/hunterX_windows_0.2.0.zip`.
- Windows zip testzip passed; SHA256: `75a3e4613f91043b9dff0839575bf4b9ffb9ddb56b5b170236666baa733a6284`.
- Source archive integrity is recorded outside the archive in `outputs/hunterX_source_0.2.0.zip.sha256` after source packaging to avoid self-referential archive hashing.

## Known Non-Zero Result

- Bandit exits 1 due Low severity inherited findings only; final Medium and High counts are 0.
