#!/usr/bin/env python3
"""
Dump one SSE frame from the Farmhand local app and show the structure of the
32-channel relay board (244CAB0FC00C), which farm_bridge.py does not yet map.

Standard library only - no pip installs. Run it on any machine that can reach
the Farmhand app (the farm PC is safest).

    python dump-relay.py

Send the output back to Claude to get the binary_sensor mapping written.
"""

import json
import sys
import urllib.request

URL = "http://192.168.200.200:3001/farm-data"
RELAY = "244CAB0FC00C"

try:
    with urllib.request.urlopen(URL, timeout=30) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue

            payload = json.loads(line[5:].strip())
            state = payload.get("state", {})

            print("=" * 60)
            print("TOP-LEVEL KEYS:", list(payload.keys()))
            print("DEVICES SEEN  :", list(state.keys()))
            print("=" * 60)

            relay = state.get(RELAY)
            if relay is None:
                print(f"\n!! {RELAY} not present in this frame.")
                print("   Full payload follows so we can find where it lives:\n")
                print(json.dumps(payload, indent=2)[:6000])
                sys.exit(0)

            print(f"\n--- {RELAY} FULL OBJECT ---")
            print(json.dumps(relay, indent=2)[:6000])

            inner = relay.get("state")
            if isinstance(inner, dict):
                print(f"\n--- CHANNEL KEYS ({len(inner)}) ---")
                for k, v in sorted(inner.items()):
                    print(f"  {k:<24} = {v!r}   ({type(v).__name__})")
            break

except urllib.error.URLError as e:
    print(f"Could not reach {URL}")
    print(f"Reason: {e.reason}")
    print("\nThis machine may not be on the farm network. Run it on the farm PC")
    print("(the one running farm_bridge.py) instead.")
    sys.exit(1)
