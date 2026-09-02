# tools/

Diagnostic scripts. **None of these are needed to run the bridge.** They exist
for discovery and for re-capturing things that Freight Farms can change without
notice.

Standard library only — no `pip install` required. Both are strictly read-only:
they issue GET requests and never change farm state.

Run them on a machine that can reach the farm network.

## dump-relay.py

Prints one SSE frame's worth of the 32-channel output board
(`244CAB0FC00C`) — every channel's state, mode, and shadow value.

```
python tools/dump-relay.py
```

Use it to:

- Confirm a channel mapping before trusting it in an automation
- Work out what channels **16** and **24** do — they are unmapped in the
  Freight Farms schematic. Toggle equipment and watch which channel moves.
- Sanity-check the `shadow` vs `state` hypothesis behind the
  `Relay State Mismatch` sensor

## discover-farmhand-api.py

Fetches the farmhand local web app's HTML and JavaScript bundles and greps them
for API routes.

```
python tools/discover-farmhand-api.py
```

Use it when the Task Mode buttons stop working after a farmhand update. The
control endpoint (`POST /farm-control`) is undocumented and unsupported — a
Freight Farms release can move or remove it.

Faster alternative when you have a browser on the farm network: open the
farmhand UI, F12 → Network, toggle Task Mode, and read the request URL, method,
and JSON body directly. That is how the current endpoint was found.

## Not in this repo

`apply_relay_patch.py` and `apply_control_patch.py` were one-time migration
tools for a bridge that was already deployed. `farm_bridge.py` in this repo
already contains everything they added, so a fresh install needs neither.
They are deliberately excluded — keeping them would invite someone to run
them against an already-complete file.
