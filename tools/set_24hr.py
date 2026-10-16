"""
Switch farmhand's 24-hour settings locally -- no cloud app needed.

Uses the same command farmhand Local sends when you save a recipe card:
    {"command": "bulk_update_settings", "settings": {...}}
Only the six 24-hour fields below can ever be sent. Nothing else.

Usage (on hydro, farm LAN):
    py -3 set_24hr.py status
    py -3 set_24hr.py top on          (or off)   - nursery TOP trough lights
    py -3 set_24hr.py bottom on       (or off)   - nursery BOTTOM trough lights
    py -3 set_24hr.py nursery on      (or off)   - all four nursery LED flags
    py -3 set_24hr.py water on        (or off)   - left + right cultivation watering

Every change asks you to type "yes", then reads the settings back from the
farm to confirm they actually changed.
"""
import json
import socket
import sys
import threading
import time
import urllib.request

HOST = "192.168.200.200"
PORT = 3001

GROUPS = {
    "top": ("nursery_top_red_24hr_enable", "nursery_top_blue_24hr_enable"),
    "bottom": ("nursery_bottom_red_24hr_enable", "nursery_bottom_blue_24hr_enable"),
    "water": ("cultivation_left_water_24hr_enable", "cultivation_right_water_24hr_enable"),
}
GROUPS["nursery"] = GROUPS["top"] + GROUPS["bottom"]
ALL_FLAGS = GROUPS["nursery"] + GROUPS["water"]


def post(payload):
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/farm-control",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def read_live_settings(timeout=15):
    """Ask for get_farm_info and catch its reply on /farm-monitoring."""
    result = {}
    ready = threading.Event()
    done = threading.Event()

    def listen():
        try:
            req = urllib.request.Request(f"http://{HOST}:{PORT}/farm-monitoring",
                                         headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                ready.set()
                buf = []
                end = time.time() + timeout
                while time.time() < end and not done.is_set():
                    try:
                        line = resp.readline()
                    except (socket.timeout, TimeoutError):
                        continue
                    if not line:
                        break
                    line = line.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        buf.append(line[5:].strip())
                    elif line == "" and buf:
                        try:
                            msg = json.loads("\n".join(buf))
                        except json.JSONDecodeError:
                            msg = None
                        buf = []
                        if isinstance(msg, dict) and msg.get("response_to") == "get_farm_info":
                            result.update(msg["response"].get("settings", {}))
                            done.set()
        except OSError as e:
            print(f"  monitoring stream error: {e}")
            ready.set()

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    ready.wait(6)
    time.sleep(0.5)
    post({"command": "get_farm_info"})
    done.wait(timeout)
    return result


def show(settings, title):
    print(title)
    for f in ALL_FLAGS:
        v = settings.get(f, "?")
        print(f"  {f:40s} {'ON' if v == 1 else 'OFF' if v == 0 else v}")


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if args == ["status"]:
        show(read_live_settings(), "Current 24-hour settings on the farm:")
        return
    if len(args) != 2 or args[0] not in GROUPS or args[1] not in ("on", "off"):
        print(__doc__)
        sys.exit(1)

    group, want = args
    value = 1 if want == "on" else 0
    payload = {"command": "bulk_update_settings",
               "settings": {f: value for f in GROUPS[group]}}

    before = read_live_settings()
    show(before, "Before:")
    print("\nWill send:")
    print("  " + json.dumps(payload))
    if input("\nType yes to send: ").strip().lower() != "yes":
        print("Cancelled. Nothing sent.")
        return

    print("  farmhand replied:", post(payload)[:80], "(this reply confirms nothing)")
    time.sleep(3)
    after = read_live_settings()
    show(after, "\nAfter (read back from the farm):")

    ok = after and all(after.get(f) == value for f in GROUPS[group])
    print("\nRESULT:", "CONFIRMED" if ok else "NOT CONFIRMED -- check farmhand / the cloud app")


if __name__ == "__main__":
    main()
