# Final-Layer Artifact Verification Contract

RC3 artifacts are built only from one clean, full 40-hex HEAD commit. The source builder archives that exact commit. The Windows builder overlays the same commit on the immutable `hunterX_windows_0.5.2_rc2.zip` base with SHA-256 `47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48`.

Promotion is fail-closed and requires:

1. exact RC3 filename and required-report manifest;
2. `RC3_BUILD_PROVENANCE.json` with both 8-hour gates false and `final_eligible=false`;
3. source archive exact-commit byte verification;
4. Windows ZIP CRC, denylist, version, isolated-runtime, and PE checks;
5. fresh safe extraction and native `settings.exe`/`nodriver_tixcraft.exe` packaged smoke;
6. byte-identical source staging versus both embedded runtime `app_src` trees and packaged assets/www;
7. a checksum manifest containing exactly the source and Windows RC3 ZIPs;
8. strict re-verification of both SHA-256 entries.

Exact archive hashes are recorded outside the archives in `SHA256SUMS_v0.5.2_RC3.txt`; embedding an archive's own hash would be self-referential. The canonical build log and external manifest are the authoritative post-build evidence.

