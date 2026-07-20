# Overlay release, upgrade, and rollback

This runbook keeps Open Dictate as the single source of truth for generic code
while allowing Muse Dictate or another private product to attach memory,
lexicon, and integration adapters without copying the core.

## Repository boundary

Open Dictate owns the app and daemon core, protocol, schemas, packaging,
installer, generic tests, documentation, and release artifacts. A private
overlay owns only its `ProductConfig`, adapter implementations, private test
fixtures, and an upstream lock file. Runtime memory, recordings, transcripts,
review queues, speaker profiles, and credentials stay outside both repositories.

An overlay must not patch vendored upstream source. A generic fix discovered in
private use is first reproduced with synthetic data in Open Dictate, fixed and
released there, and then consumed through a lock-file update.

## Upgrade procedure

1. Select a signed Open Dictate release and record its SemVer tag, full 40-byte
   commit SHA, source-archive SHA-256, wire protocol version, and product-config
   schema version in `open-dictate.lock.json`.
2. Download the source archive from that immutable release. Never build an
   overlay release from a moving branch or unverified working tree.
3. Run `scripts/check-contracts.py` with the private product config, lock file,
   and downloaded archive. Reject unknown keys and incompatible wire protocols.
   An `IO-CONTRACT` document revision or feature note is not a wire version and
   must not be copied into `wireProtocolVersion`.
4. Build the public core without source patches, load private adapters through
   documented extension points, then run every command listed in
   `compatibility.overlayTests`.
5. Test migration against a disposable copy of runtime data. Verify dictation,
   glossary reload and mutation, permissions, restart, update, and uninstall.
6. Promote to the overlay Beta channel. After the observation window passes,
   promote the exact same upstream commit and artifact hashes to Stable.

The overlay CI should run nightly against its pinned release and, separately,
against the newest compatible Open Dictate release. The second job only reports
drift; it must not silently edit the lock file or publish an update.

## Rollback procedure

Keep the current and previous known-good public runtime side by side. Before an
upgrade, create an atomic metadata snapshot containing config/schema versions
and a pointer to the active runtime. User content is not copied into the app
bundle and is never deleted as part of rollback.

If health checks or overlay tests fail:

1. Stop the new daemon and atomically restore the previous runtime pointer.
2. Restore the previous config snapshot only when its schema differs. Do not
   overwrite glossary, recordings, transcripts, review queues, or profiles.
3. Start the previous daemon and require `ping` plus one synthetic transcription
   smoke test before declaring recovery.
4. Preserve redacted diagnostics locally and mark the failed version blocked.
5. If the new release performed an irreversible user-data migration, stop
   automatic rollback and show a recovery path. Such a migration should not be
   released until an explicit export/restore mechanism exists.

## Release gates

- Public safety and secret scans pass on the complete Git history and artifacts.
- Protocol and product-config contract checks pass.
- Source archive and packaged artifacts match recorded hashes.
- Private fixtures and absolute private paths are absent from public artifacts.
- The overlay runs without modifying upstream source.
- Stable and Beta channels use separate signed metadata, and a failed version
  cannot be reselected automatically.
