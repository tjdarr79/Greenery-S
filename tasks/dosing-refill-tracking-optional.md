# Task: Make Dosing Refill Tracking Optional, Off by Default

**Target:** next release (can go in with `tasks/duplicate-bridge-1.5.1.md`).
Read `CLAUDE.md` first. Work on `main`, never `stable`.

## Why
3 Corners Farm tops off all four dosing tanks **every day** they're in the
farm. The tanks only run low on purpose, before a tank clean. So:
- **Days Since Refill** and the **Refilled** buttons are clutter nobody
  presses.
- The **silent-sensor backstop** ("<Tank> hasn't reported low in 21 days")
  would fire every week on every tank: a false alarm. With daily top-offs a
  working sensor never reads Low either, so the backstop can't tell a dead
  sensor from a full tank.
- `dosing_silent_days` is `int(1,365)` today, so it can't be turned off.

Interim on the owner's farm: `dosing_silent_days` set to 365.

## Requirements
1. New option `dosing_refill_tracking` (bool), **default `false`**.
   - `false`: don't create Days Since Refill sensors or Refilled buttons, and
     never send the silent-sensor alert. On upgrade, remove the existing
     entities by publishing an empty retained discovery config for each
     (they must disappear from HA, not show unavailable).
   - `true`: today's behavior, using `dosing_silent_days`.
2. Keep unchanged: the four level sensors, the 10-minute debounce, Sensor
   Fault, the Tank Low alert (12 h repeat), and the unstable-sensor alert.
   Don't change any unique_id or topic of the entities that stay.
3. Dashboards: remove the "Days since refill" rows and the Refilled button
   grid from `farm-dashboard.yaml` and `farm-dashboard-mobile.yaml`. Note in
   a comment how to add them back when `dosing_refill_tracking` is on.
4. `automations/23-dosing-tank-alert.yaml`: description updated (silent alert
   only when refill tracking is on). No logic change needed; the bridge
   decides.
5. DOCS.md, *Dosing tank levels*: explain both modes. Daily top-off farms
   leave it off and check tank levels by eye; the sensor only reports
   "almost empty". The dead-sensor caveat stays: a broken or unplugged
   sensor reads OK, so look at the tanks when you top them off.
6. CHANGELOG entry, version bump, copy `farm_bridge.py` to `greenery-bridge/`,
   `python tools/verify-repo.py` → ALL CHECKS PASSED.

## Tests
1. Option off: no refill entities discovered; existing ones removed with
   empty configs; 30 simulated days with no Low → no silent alert.
2. Option on: behaves as 1.4.0 (existing tests pass).
3. Tank Low and Sensor Fault behave the same in both modes.

## Acceptance (owner)
1. After the update, Days Since Refill and Refilled entities are gone from HA
   (not "unavailable").
2. The Dosing Tanks card shows the four levels only.
3. No dosing alerts while tanks are full.
