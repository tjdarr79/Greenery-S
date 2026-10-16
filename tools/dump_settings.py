"""
Read-only dump of farmhand's settings and recipe, as stored on the farm's Hub.

Sends ONLY commands that farmhand's own page sends every time it loads:
read_settings_files, get_farm_info, get_version, get_current_mode, read_modules.
Nothing here changes any setting or switches any equipment.

The replies come back on farmhand's second live stream (/farm-monitoring),
so this listens there, sends the requests, waits, and saves everything.

Usage (on hydro, farm LAN):
    py -3 dump_settings.py
Output:
    results\\farmhand_settings_<date-time>.json
"""
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

HOST = "192.168.200.200"
PORT = 3001
LISTEN_SECONDS = 20
MAX_BYTES = 20_000_000
READ_ONLY_COMMANDS = (
    {"command": "read_settings_files"},
    {"command": "get_farm_info"},
    {"command": "get_version"},
    {"command": "get_current_mode"},
    {"command": "read_modules"},
)
FLAG_HINTS = ("24", "day_only", "dayonly", "hour", "schedule", "night", "water_cycle", "always")

events = []
stop = threading.Event()


def listen():
    url = f"http://{HOST}:{PORT}/farm-monitoring"
    total = 0
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            buf = []
            while not stop.is_set():
                try:
                    line = resp.readline()
                except (socket.timeout, TimeoutError):
                    continue
                if not line:
                    break
                total += len(line)
                if total > MAX_BYTES:
                    break
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    buf.append(line[5:].strip())
                elif line == "" and buf:
                    text = "\n".join(buf)
                    buf = []
                    try:
                        body = json.loads(text)
                    except json.JSONDecodeError:
                        body = text
                    events.append({"t": time.time(), "data": body})
    except OSError as e:
        events.append({"t": time.time(), "error": f"monitoring stream: {e}"})


def send(cmd):
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/farm-control",
        data=json.dumps(cmd).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")[:200]


def find_hints(obj, path="", out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if any(h in str(k).lower() for h in FLAG_HINTS):
                out.append((p, v if not isinstance(v, (dict, list)) else type(v).__name__))
            find_hints(v, p, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_hints(v, f"{path}[{i}]", out)
    return out


def main():
    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(2)

    replies = {}
    for cmd in READ_ONLY_COMMANDS:
        try:
            replies[cmd["command"]] = send(cmd)
        except OSError as e:
            replies[cmd["command"]] = f"ERROR {e}"
        time.sleep(0.5)
    print("Sent:", ", ".join(f"{k} -> {v}" for k, v in replies.items()))

    print(f"Listening {LISTEN_SECONDS}s for replies...")
    time.sleep(LISTEN_SECONDS)
    stop.set()

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"farmhand_settings_{time.strftime('%Y%m%d-%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"replies": replies, "events": events}, f, indent=2)

    print(f"\nEvents received: {len(events)}")
    print(f"Saved: {out}")
    hints = find_hints([e.get("data") for e in events])
    if hints:
        print("\nFields that look like day-only / 24-hour settings:")
        for p, v in hints[:60]:
            print(f"  {p} = {v}")
    else:
        print("No obvious 24-hour fields found -- send me the file.")


if __name__ == "__main__":
    main()
