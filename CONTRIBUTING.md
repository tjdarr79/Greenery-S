# Contributing and releasing

Two farms or more run this code, and only one of them is the owner's. This file
is the boundary between them.

## Two channels

| Branch | Installed by | What lands on it |
|---|---|---|
| `main` | **3 Corners Farm only** — the dev farm | Every change, first. This is where changes get proven. |
| `stable` | **Every customer farm** | Only commits that have already run on 3 Corners Farm. |

**Never commit to `stable`, never branch from it for new work, and never point a
development session at it.** New work branches from `main` and merges back to
`main`. `stable` only ever fast-forwards to a commit `main` already has.

Home Assistant offers **Update** when the `version:` in
`greenery-bridge/config.yaml` on the branch a farm tracks is higher than the
version it runs. So a version bump on `main` reaches 3 Corners Farm only; a
customer farm sees an update only when `stable` moves.

## Day to day — on `main`

1. Branch from `main`. Make the change.
2. `python tools/verify-repo.py` must print `ALL CHECKS PASSED`.
3. If the change should reach the dev farm, bump `version:` in
   `greenery-bridge/config.yaml` — without a bump Home Assistant does not
   offer the update, not even to your own farm:
   - **patch** (`1.1.0 → 1.1.1`): fixes, no new options
   - **minor** (`1.1.x → 1.2.0`): new options or features, nothing to redo
   - **major** (`1.x → 2.0.0`): anything that changes entity IDs or makes a
     farm redo part of its setup
4. Add a matching `## <version>` entry at the top of
   `greenery-bridge/CHANGELOG.md` in the same change. `verify-repo.py` fails
   when the two disagree.
5. Merge to `main`, update 3 Corners Farm, and watch it run.

## Releasing to `stable`

A commit is **proven** when it has run on 3 Corners Farm, the app's Log is
clean, and the behaviour it changes has been seen working — not merely
installed. Until then it does not go to `stable`.

Before releasing, all of these hold:

- `verify-repo.py` passes on the exact `main` commit being released
- that commit is what 3 Corners Farm is running, and it is proven
- its `version:` is higher than the one on `stable`, with a CHANGELOG entry —
  every release to `stable` carries a new version and a new entry, or
  customers are never offered it

```
git fetch origin
git checkout main
git pull --ff-only origin main
python tools/verify-repo.py              # ALL CHECKS PASSED, or stop

git checkout stable
git pull --ff-only origin stable
git merge --ff-only main                 # fails if stable was committed to directly - stop and find out why
git push origin stable

git tag -a v1.1.0 -m "Greenery S Farm Bridge 1.1.0"    # the version in config.yaml
git push origin v1.1.0
```

`--ff-only` is the point: `stable` is then byte-for-byte a commit that ran on
the dev farm, never a merge result nobody has run.

### First release — `stable` does not exist yet

Until this is done, the `#stable` repository URL in `README.md` fails to add
(Supervisor cannot clone a branch that does not exist). Do not send a customer
the URL first.

```
git fetch origin
git checkout main
git pull --ff-only origin main
python tools/verify-repo.py              # ALL CHECKS PASSED, or stop

git branch stable main
git push -u origin stable

git tag -a v1.1.0 -m "Greenery S Farm Bridge 1.1.0" main
git push origin v1.1.0
```

Optional, for a rollback reference: tag the 1.0.0 baseline too —
`git tag -a v1.0.0 ed7ce6d -m "Greenery S Farm Bridge 1.0.0"`, then
`git push origin v1.0.0`.

Then, in GitHub → Settings → Branches, add a rule for `stable` that blocks
direct pushes and force-pushes, so this file is not the only thing enforcing
it.

### Urgent customer fix while `main` holds unproven work

Fast-forwarding would ship the unproven work too. A side branch does not help
either: the dev farm cannot try one without uninstalling the app, because
Supervisor will not remove a repository URL while an app from it is installed.

So keep it on `main`: `git revert` the unproven commits (revert, never reset
— `main` is shared history), land the fix with a patch bump and CHANGELOG
entry, prove it on 3 Corners Farm, release as normal, then re-land the
reverted work.

### When a release is bad

Supervisor has no "install the previous version" button. Two ways back:

- **Per farm, immediately:** restore the backup Home Assistant offers to make
  before every app update. Tell customers to leave that option on.
- **For everyone:** revert on `main`, bump the version, prove it, release. A
  lower version number is never offered as an update.
