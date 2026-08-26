# HunterX v0.5.2 FINAL Implementation Diff

The FINAL delivery does not change the RC3 product runtime under `src/`.
Changes after the byte-verified RC3 base are limited to:

- explicit, machine-validated user waiver handling for the two eight-hour
  actual-browser gates;
- FINAL artifact naming, clean-commit provenance, and GitHub release workflow;
- Windows RC3 binary-base locking and fresh-extract smoke wiring;
- source/Windows dual-runtime byte parity and exact checksum enforcement;
- authoritative FINAL reports, release notes, and user-facing version text;
- an allowlisted Windows root with historical engineering reports moved to
  `docs/release-audit/` and inherited RC/RC2/RC3 clutter removed;
- normal branch/PR Windows CI that validates Windows runtime/release contracts
  without requiring a not-yet-published RC3 GitHub asset;
- a manual-only optional FINAL rebuild workflow so publishing tag `v0.5.2`
  cannot automatically start the known missing-base failure;
- a bounded exact-readiness gate in the local actual-browser synthetic harness
  after its pre-action race was reproduced; and
- tests for strict qualification/waiver exclusivity, archive organization,
  workflows, and release behavior.

The existing automation selection/click core, attempt lifecycle, refresh
owners, dictionary consumers, CAPTCHA/manual boundaries, profiles,
notifications, and named-instance logic were not changed by this packaging
layer.
