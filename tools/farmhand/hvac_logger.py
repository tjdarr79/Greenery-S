"""
HVAC logger: when farmhand asks for dehumidify / cooling / heating, what do the
HVAC relays actually do, and what happen to RH and air temp?

Read-only. Every 30 s it asks farmhand for its live settings (get_farm_info,
which farmhand's own page sends on every load) to read its call_for_* flags,
and reads the live data stream for RH, temperature and relays. Nothing here
changes a setting or switches anything.

Usage (on hydro, farm LAN; leave it running, Ctrl+C to stop):
    py -3 hvac_logger.py
Output:
    results\\hvac_log_<date-time>.csv   (one row every 30 s, saved as it goes)
"""
import csv
import json
import socket
import threading
import time
import urllib.request
from pathlib import Path

HOST = "192.168.200.200"
PORT = 3001
INTERVAL = 30
ENV = "94E68607D090"      # environmental module: CO2, RH, temp
OUT_BOARD = "244CAB0FC00C"
RELAYS = {10: "duct_fans", 11: "overhead_fan", 12: "exhaust_fan",
          13: "hvac_blower", 14: "hvac_cooling", 15: "hvac_heater",
          25: "cult_led_left_red_a"}          # 25 = day/night marker
CALLS = ("call_for_dehum", "call_for_cooling", "call_for_heating",
         "call_for_safety_ventilation")
SETPOINTS = ("pgm_day_rh", "pgm_night_rh", "pgm_day_temperature",
             "pgm_night_temperature")

latest_frame = {}
latest_settings = {}
lock = threading.Lock()
first_frame_saved = threading.Event()
OUT_DIR = Path(__file__).parent / "results"


def sse(path, on_msg):
    while True:
        try:
            req = urllib.request.Request(f"http://{HOST}:{PORT}{path}",
                                         headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                buf = []
                while True:
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
                            on_msg(json.loads("\n".join(buf)))
                        except json.JSONDecodeError:
                            pass
                        buf = []
        except OSError:
            pass
        time.sleep(3)   # farmhand drops streams about every minute; reconnect


def on_frame(msg):
    global latest_frame
    if isinstance(msg, dict) and "state" in msg:
        with lock:
            latest_frame = msg
        if not first_frame_saved.is_set():
            # Record what else the live stream carries, in case the call_for_*
            # flags are in it too.
            keys = {k: (list(v.keys())[:40] if isinstance(v, dict) else type(v).__name__)
                    for k, v in msg.items()}
            (OUT_DIR / "farm_data_keys.json").write_text(json.dumps(keys, indent=2))
            first_frame_saved.set()


def on_monitor(msg):
    if isinstance(msg, dict) and msg.get("response_to") == "get_farm_info":
        s = (msg.get("response") or {}).get("settings") or {}
        with lock:
            latest_settings.clear()
            latest_settings.update(s)


def ask_farm_info():
    req = urllib.request.Request(f"http://{HOST}:{PORT}/farm-control",
                                 data=json.dumps({"command": "get_farm_info"}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def c_to_f(c):
    return round(c * 9 / 5 + 32, 1) if isinstance(c, (int, float)) else ""


def main():
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"hvac_log_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    threading.Thread(target=sse, args=("/farm-data", on_frame), daemon=True).start()
    threading.Thread(target=sse, args=("/farm-monitoring", on_monitor), daemon=True).start()
    time.sleep(3)

    cols = (["time", "air_temp_f", "rh", "co2"] + list(CALLS)
            + list(RELAYS.values()) + list(SETPOINTS))
    print(f"Logging every {INTERVAL}s to {out}  (Ctrl+C to stop)")
    print("time      temp  RH   dehum cool heat | blower cooling heater")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        try:
            while True:
                try:
                    ask_farm_info()
                except OSError as e:
                    print(f"  get_farm_info failed: {e}")
                time.sleep(3)
                with lock:
                    frame, s = dict(latest_frame), dict(latest_settings)
                env = ((frame.get("state") or {}).get(ENV) or {}).get("state") or {}
                outs = ((frame.get("state") or {}).get(OUT_BOARD) or {}).get("state") or {}
                row = [time.strftime("%Y-%m-%d %H:%M:%S"), c_to_f(env.get("temp")),
                       env.get("RH", ""), env.get("CO2", "")]
                row += [s.get(k, "") for k in CALLS]
                row += [int(bool(outs[f"output_{ch}"])) if f"output_{ch}" in outs else ""
                        for ch in RELAYS]
                row += [s.get(k, "") for k in SETPOINTS]
                w.writerow(row)
                f.flush()
                d = dict(zip(cols, row))
                print(f"{d['time'][11:19]}  {d['air_temp_f']:>5} {d['rh']!s:>4}   "
                      f"{d['call_for_dehum']!s:>3}  {d['call_for_cooling']!s:>3}  "
                      f"{d['call_for_heating']!s:>3} |   {d['hvac_blower']!s:>3}     "
                      f"{d['hvac_cooling']!s:>3}     {d['hvac_heater']!s:>3}")
                time.sleep(max(1, INTERVAL - 3))
        except KeyboardInterrupt:
            print(f"\nStopped. Saved: {out}")


if __name__ == "__main__":
    main()
