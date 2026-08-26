# HunterX v0.5.2 FINAL GitHub Actions Audit

Audit date: 2026-08-25 (Asia/Taipei). The public GitHub API, current remote
refs, local workflow files, and executable local tests were cross-checked.

## Public state before this correction

- Remote `main`: `bbdd1f1a9dc02fef37da18a43e7f20f928b39ffd`.
- Import branch `v0.5.2-final-import`:
  `cca0dc72738a7a6e2bbd9c8fd65cefa03b51b7db`.
- The import branch and the locally verified FINAL commit had identical Git
  trees before this packaging correction.
- PR run `32825036915`: Linux test/lint/audit passed; Windows job failed only
  at `Download and verify v0.5.2 RC3 Windows build base` because the repository
  has no `build-base-v0.5.2-rc3` release.
- PR CodeQL run `32825036912` and Dependency Review run `32825036914` passed.
- The public Releases list ended at `v0.5.1`; no v0.5.2 base or official
  release existed at audit time.

## Root cause and correction

Normal branch and pull-request CI incorrectly depended on a release asset that
could only exist after an external publication step. This made valid source
changes permanently red on Windows even though the Linux suite and security
jobs passed.

The Windows CI job now performs genuine Windows-specific entrypoint, PowerShell
syntax, runtime lifecycle, user-dictionary, release verifier, and packaging
contract tests directly from source. It no longer downloads or claims to build
a package from an unpublished base. Full native packaged smoke remains a
mandatory local artifact gate and an optional manual automated-rebuild gate.

The FINAL publish workflow is now `workflow_dispatch` only. Creating the
official `v0.5.2` tag during a manually verified GitHub Release therefore does
not automatically start a build that is known to lack its RC3 base. The exact
manual upload procedure is documented in `GITHUB_RELEASE_GUIDE_zh-TW.md`.

## Publication truth

Local workflow parsing and tests can prove syntax and contract behavior, but
cannot claim a future remote run green. After pushing the corrected source,
the new commit must pass the visible GitHub checks before merging. The release
must contain exactly the two verified ZIP files and checksum manifest.

Status: **LOCAL CORRECTION VERIFIED; NEW REMOTE RUN REQUIRED AFTER PUSH.**
