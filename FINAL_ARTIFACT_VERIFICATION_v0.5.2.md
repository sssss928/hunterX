# HunterX v0.5.2 RC3 Historical Artifact Verification

> This report verifies the immutable RC3 base. The authoritative FINAL
> delivery result is `FINAL_RELEASE_AUDIT_v0.5.2.md`; the user later explicitly
> waived the two eight-hour gates without representing them as passed.

## Immutable RC3 re-verification

- Checksum manifest contained exactly two RC3 assets and both SHA-256 values verified.
- Windows ZIP: 633 entries, CRC OK, required files present, provenance verified, isolated runtime layout verified.
- Source ZIP: 327 entries, CRC OK, exact commit `86436fb55a94e779578fd520f03a5d9efff95011`, 0 missing, 0 extra, 0 mismatch.
- Pair verification: 79 runtime files and 8 parity targets matched the source archive and both embedded app-source trees.
- Fresh safe extraction packaged smoke: `settings.exe` and `nodriver_tixcraft.exe` both reported `HunterX (0.5.2)`; settings smoke PASS.

## Pre-final rebuild verification

- The repaired candidate was committed cleanly before both archives were built from the same 40-hex commit recorded in their package provenance.
- Windows ZIP: 647 entries, CRC OK, all 25 RC3/pre-final required reports present, provenance verified, and isolated runtime layout verified.
- Source ZIP: 342 entries, CRC OK, 0 missing, 0 extra, and 0 mismatch against the exact release commit.
- Pair verification: 79 runtime files and 8 parity targets matched the source archive and both embedded app-source trees.
- A second fresh safe extraction packaged smoke confirmed `settings.exe` and `nodriver_tixcraft.exe` both report `HunterX (0.5.2)` and the settings smoke passes.
- The checksum verifier accepted exactly the two canonical RC3 ZIP files. Their non-self-referential SHA-256 values are recorded in the external `SHA256SUMS_v0.5.2_RC3.txt` delivered beside them.

## Branding decision

At this RC3 checkpoint, **8H SOAK NOT VERIFIED** and no `_final.zip` artifact
was permitted. The later FINAL delivery is authorized only by the explicit
`FINAL_8H_SOAK_WAIVER.json`, while `final_eligible` remains false.
