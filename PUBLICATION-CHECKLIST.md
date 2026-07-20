# Publication Checklist

- [ ] New Git history only; no private repository history.
- [ ] No real dictation logs, Wispr history, voiceprints, audio files, or private calibration text.
- [ ] No hard-coded local user paths.
- [ ] No credentials or pieces.
- [ ] `python3 scripts/public-safety-scan.py` passes.
- [ ] `python3 -m compileall daemon scripts vendor` passes.
- [ ] `python3 scripts/golden-bench.py --skip-daemon` passes.
- [ ] `./build.sh` passes.
