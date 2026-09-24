# Notes for development sessions

- **Never commit to, branch from, or develop against `stable`.** It is what
  customer farms install. Work on `main` (or a branch from it); `stable` only
  fast-forwards to proven `main` commits. See `CONTRIBUTING.md`.
- Run `python tools/verify-repo.py` after every change; it must print
  `ALL CHECKS PASSED`.
- `farm_bridge.py` exists twice — repo root and `greenery-bridge/`. Edit the
  root copy, then copy it over the other. verify-repo fails on drift.
- A change that should reach a farm bumps `version:` in
  `greenery-bridge/config.yaml` and adds a matching CHANGELOG entry.
- Entity IDs derive from the app's `farm_name`. Never change unique_ids, the
  device identifier or MQTT topics - that orphans every entity on every farm.
