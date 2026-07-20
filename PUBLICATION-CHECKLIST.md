# Public Publication Checklist

Run this before pushing, releasing, or accepting public contributions.

## Automated Gates

```bash
python3 scripts/public-safety-scan.py
python3 -m unittest discover tests
python3 -m compileall daemon scripts vendor
python3 scripts/golden-bench.py --skip-daemon
```

For release builds:

```bash
./scripts/smoke-test.sh
```

## Manual Review

- [ ] No real audio files.
- [ ] No real meeting transcripts.
- [ ] No dictation logs.
- [ ] No personal glossary accumulated from daily use.
- [ ] No speaker profiles, embeddings, or voiceprints.
- [ ] No private local paths.
- [ ] No private project names or collaborator names.
- [ ] README marks unfinished features as planned or experimental.
- [ ] Privacy warnings cover speaker identity and review queues.
- [ ] Screenshots are mockups or checked for private text.

## Release Rule

If in doubt, do not publish the artifact. Replace it with a fictional fixture or a generated mockup.
