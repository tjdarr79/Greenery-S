#!/usr/bin/env python3
"""
Greenery S -> Home Assistant MQTT Bridge

Reads the local Farmhand SSE stream (/farm-data) and republishes confirmed
sensor values to MQTT with Home Assistant auto-discovery, so sensors appear
in HA automatically without hand-written YAML.

CONFIRMED DEVICE MAP (do not change without re-verifying against dashboard):
    94E68607D218  -> Cultivation dosing  (pH, EC, temp)
    244CAB0FD624  -> Nursery dosing      (pH, EC, temp)
    94E68607D090  -> Environmental       (CO2, RH, temp)
    8C4B14715024  -> Input module:
                        analog_1 -> Cultivation depth  (raw, uncalibrated offset)
                        analog_2 -> Nursery depth
                        analog_3 -> Left send pressure
                        analog_4 -> Right send pressure
    244CAB0FC00C  -> 32-channel output relay (NOT YET MAPPED to function - published
                      as raw output_N booleans only, no friendly names yet)
    E831CDC7E680  -> Idle / unused input module (not published)

Requires: pip install aiohttp paho-mqtt --break-system-packages
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

import aiohttp
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Load farm-bridge.env from the same folder as this script - no manual
# `set`/`setx` environment variables needed. This is the single biggest
# source of setup failures when this is handed to someone else to run.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "farm-bridge.env"))

# ---------------------------------------------------------------------------
# CONFIG - edit these before running
# ---------------------------------------------------------------------------

SSE_URL = os.environ.get("FARM_SSE_URL", "http://192.168.200.200:3001/farm-data")

MQTT_HOST = os.environ.get("FARM_MQTT_HOST", "192.168.200.187")
MQTT_PORT = int(os.environ.get("FARM_MQTT_PORT", "1883"))
# Credentials pulled from environment, NOT hardcoded - see note below on setting these.
MQTT_USERNAME = os.environ.get("FARM_MQTT_USER")
MQTT_PASSWORD = os.environ.get("FARM_MQTT_PASS")

MQTT_BASE_TOPIC = "farm"          # state topics published under farm/<node>/<field>
DISCOVERY_PREFIX = "homeassistant"  # HA's default discovery prefix - don't change
                                     # unless you customized it in HA's MQTT integration

RECONNECT_DELAY_SECONDS = 5       # wait before retrying SSE after a drop
SSE_READ_TIMEOUT_SECONDS = 90     # stream aborts ~60s server-side; this is our
                                   # client-side ceiling before we force a reconnect

# Overridable so the Home Assistant add-on can expose it as a dropdown.
LOG_LEVEL = getattr(logging, os.environ.get("FARM_LOG_LEVEL", "INFO").upper(), logging.INFO)

# ---------------------------------------------------------------------------
# FARM IDENTITY
#
# FARM_NAME becomes the Home Assistant device name. HA builds every entity ID
# from device name + entity name, so this decides the IDs a FRESH install gets:
#     "Smith Farm"  ->  sensor.smith_farm_cultivation_ph
#
# The default is the name every install had before this was configurable. Keep
# it: an install upgraded with no farm_name set must come up exactly as before.
#
# HA fixes an entity ID in its registry the first time it sees the entity. A
# later rename changes the device and friendly names only; the IDs stay put.
# Set it once, before first start - see DOCS.md.
#
# Deliberately NOT derived from the name: unique_ids, the device identifier and
# the MQTT topics. Each farm has its own broker, so they cannot collide, and
# tying them to an editable name would orphan every entity on a rename.
# ---------------------------------------------------------------------------

LEGACY_FARM_NAME = "Greenery S Farm"
DEVICE_IDENTIFIER = "greenery_s_farm"   # HA device registry key - never change


def farm_slug(name: str) -> str:
    """The prefix Home Assistant will give this farm's entity IDs.

    Mirrors HA's slugify for Latin-script names: accents folded to ASCII, every
    other run of non-alphanumerics collapsed to one underscore. Keep identical
    to the copy in tools/render-farm-yaml.py - verify-repo.py checks it.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")


FARM_NAME_REQUESTED = (os.environ.get("FARM_NAME") or "").strip()
# A name with no letters or digits gives HA nothing to build an ID from.
FARM_NAME = FARM_NAME_REQUESTED if farm_slug(FARM_NAME_REQUESTED) else LEGACY_FARM_NAME
FARM_SLUG = farm_slug(FARM_NAME)

# Shared by every discovery payload, so every entity lands on one device that
# carries the farm's name.
DEVICE_INFO = {
    "identifiers": [DEVICE_IDENTIFIER],
    "name": FARM_NAME,
    "manufacturer": "Freight Farms",
    "model": "Greenery S",
}

# ---------------------------------------------------------------------------
# Sensor definitions - drives both MQTT topic naming and HA discovery payloads
# ---------------------------------------------------------------------------

@dataclass
class SensorDef:
    unique_id: str          # stable HA entity id suffix
    name: str                # friendly name shown in HA
    device_id: str           # source device key in the SSE payload
    state_path: tuple        # path into state[device_id]["state"] dict, e.g. ("pH",)
    unit: str = None
    device_class: str = None # HA device_class, e.g. "temperature", "humidity", "co2"
    state_class: str = "measurement"

SENSORS = [
    # --- Cultivation dosing ---
    SensorDef("cultivation_ph", "Cultivation pH", "94E68607D218", ("pH",),
              unit="pH"),
    SensorDef("cultivation_ec", "Cultivation EC", "94E68607D218", ("ec",),
              unit="µS/cm"),
    SensorDef("cultivation_water_temp", "Cultivation Water Temp", "94E68607D218", ("temp",),
              unit="°C", device_class="temperature"),

    # --- Nursery dosing ---
    SensorDef("nursery_ph", "Nursery pH", "244CAB0FD624", ("pH",),
              unit="pH"),
    SensorDef("nursery_ec", "Nursery EC", "244CAB0FD624", ("ec",),
              unit="µS/cm"),
    SensorDef("nursery_water_temp", "Nursery Water Temp", "244CAB0FD624", ("temp",),
              unit="°C", device_class="temperature"),

    # --- Environmental ---
    SensorDef("farm_co2", "Farm CO2", "94E68607D090", ("CO2",),
              unit="ppm", device_class="carbon_dioxide"),
    SensorDef("farm_humidity", "Farm Humidity", "94E68607D090", ("RH",),
              unit="%", device_class="humidity"),
    SensorDef("farm_air_temp", "Farm Air Temp", "94E68607D090", ("temp",),
              unit="°C", device_class="temperature"),

    # --- Depth / pressure (input module 8C4B14715024) ---
    # NOTE: Cultivation depth (analog_1) is uncalibrated - raw value published,
    # will not match Farmhand's displayed % until sensor is recalibrated.
    SensorDef("cultivation_depth_raw", "Cultivation Depth (raw)", "8C4B14715024", ("analog_1",),
              unit="%"),
    SensorDef("nursery_depth", "Nursery Depth", "8C4B14715024", ("analog_2",),
              unit="%"),
    SensorDef("left_send_pressure", "Left Send Pressure", "8C4B14715024", ("analog_3",),
              unit="%"),
    SensorDef("right_send_pressure", "Right Send Pressure", "8C4B14715024", ("analog_4",),
              unit="%"),
]

# --- BEGIN relay output board mapping (added by apply_relay_patch) ---
# Channel -> (friendly name, HA device_class, circuit breaker)
# Source: Freight Farms "How to Read the Greenery S Electrical Schematic", Mar 2025.
#
# The CB is kept here for reference but deliberately NOT put in the entity name:
# HA derives entity_id from the name, and "(CB5)" would end up in every id you
# ever type into an automation.
OUTPUT_MAP = {
    1:  ("Cultivation Recirc Pump",        "running", "CB5"),
    2:  ("Left Send Pump",                 "running", "CB5"),
    3:  ("Right Send Pump",                "running", "CB5"),
    4:  ("Cultivation Autofill",           "opening", "CB5"),
    5:  ("Nursery Autofill",               "opening", "CB5"),
    6:  ("Nursery Top Trough Pump",        "running", "CB4"),
    7:  ("Nursery Bottom Trough Pump",     "running", "CB4"),
    # Ch8 also carries the CHILLER PUMP - same power as the nursery recirc pump
    # so Task Mode drops both together during a tank cleanout. Owner-designed
    # interlock; the name reflects it on purpose.
    8:  ("Nursery Recirc and Chiller Pump", "running", "CB4"),
    9:  ("CO2 Regulator",                  "opening", "CB4"),
    10: ("Duct Fans",                      "running", "CB5"),
    11: ("Overhead Fan",                   "running", "CB4"),
    12: ("Exhaust Fan",                    "running", "CB4"),
    13: ("HVAC Blower",                    "running", "CB11"),
    14: ("HVAC Cooling",                   "running", "CB11"),
    15: ("HVAC Heater",                    "running", "CB11"),
    16: ("Output 16 Unmapped",             None,      None),
    17: ("Nursery LED Top Red",            "light",   "CB4"),
    18: ("Nursery LED Top Blue",           "light",   "CB4"),
    19: ("Nursery LED Bottom Red",         "light",   "CB4"),
    20: ("Nursery LED Bottom Blue",        "light",   "CB4"),
    21: ("Nursery Work LEDs",              "light",   "CB4"),
    22: ("Cultivation Work LEDs",          "light",   "CB5"),
    # Ch23 is held in MANUAL by the owner: chiller unit + extra fans. It is
    # never "auto", which is why it is excluded from Task Mode detection.
    23: ("Spare Chiller and Extra Fans",   "power",   "CB2"),
    24: ("Output 24 Unmapped",             None,      None),
    25: ("Cultivation LED Left Red A",     "light",   "CB6"),
    26: ("Cultivation LED Left Red B",     "light",   "CB7"),
    27: ("Cultivation LED Left Red C",     "light",   "CB7"),
    28: ("Cultivation LED Left Blue",      "light",   "CB8"),
    29: ("Cultivation LED Right Red A",    "light",   "CB9"),
    30: ("Cultivation LED Right Red B",    "light",   "CB10"),
    31: ("Cultivation LED Right Red C",    "light",   "CB10"),
    32: ("Cultivation LED Right Blue",     "light",   "CB8"),
}

OUTPUT_DEVICE_ID = "244CAB0FC00C"

# Channels the owner intentionally holds in manual. Without this exclusion the
# Task Mode sensor reads true forever and means nothing.
TASK_MODE_EXCLUDE = {23}

# Channels Home Assistant may switch by hand - only while the farm is in Task
# Mode. A channel is added here only after it has been physically tested
# switching on AND off. See README, "Pump switches (Task Mode only)".
#   1, 2, 3: 2026-09-25    8: 2026-10-16 (capture tool: only output_8 moved)
MANUAL_CONTROL_CHANNELS = (1, 2, 3, 8)

# Checked on every start. An explicit raise, not assert: python -O strips those.
if (set(MANUAL_CONTROL_CHANNELS) & TASK_MODE_EXCLUDE
        or not set(MANUAL_CONTROL_CHANNELS) <= set(OUTPUT_MAP)):
    raise RuntimeError("MANUAL_CONTROL_CHANNELS must be mapped channels and must "
                       "not include a TASK_MODE_EXCLUDE channel")

# Retained "online"/"offline": whether the switches may be used right now.
MANUAL_CONTROL_AVAILABLE_TOPIC = f"{MQTT_BASE_TOPIC}/manual_control/available"

# NOTE on the chiller: ch23 carries the chiller UNIT, ch8 its PUMP. Do NOT derive
# a "running dry" sensor from ch23 AND NOT ch8 - the owner kills the chiller at
# its own switch before opening the nursery valve, and that switch is invisible
# here (ch23 stays energised). Such a sensor would fire through every cleanout.
# The meaningful alarm is "recirc pump stopped while in AUTO", done in HA.


@dataclass
class BinarySensorDef:
    unique_id: str
    name: str
    device_class: str = None
    entity_category: str = None


BINARY_SENSORS = [
    BinarySensorDef(
        unique_id=f"output_{_ch}",
        name=_name,
        device_class=_dc,
        entity_category=None if _cb else "diagnostic",
    )
    for _ch, (_name, _dc, _cb) in sorted(OUTPUT_MAP.items())
] + [
    BinarySensorDef("task_mode_active", "Task Mode Active", device_class="problem"),
    BinarySensorDef("output_board_connected", "Output Board Connected",
                    device_class="connectivity", entity_category="diagnostic"),
    # HYPOTHESIS - validate before trusting. "shadow" appears to hold commanded
    # state while "state" holds actual; a mismatch would mean a relay was told
    # to switch and did not. output_24 is absent from shadow and is excluded.
    BinarySensorDef("relay_state_mismatch", "Relay State Mismatch",
                    device_class="problem", entity_category="diagnostic"),
    # Published by the cool-down section below, not from the frame directly.
    BinarySensorDef("cool_down_active", "Cool-Down Active"),
    BinarySensorDef("cool_down_fault", "Cool-Down Fault", device_class="problem"),
]


def _output_board(payload: dict) -> dict:
    try:
        return payload["state"][OUTPUT_DEVICE_ID]
    except (KeyError, TypeError):
        return {}


def publish_binary_states(client, payload: dict):
    global _BOARD_FRAME_AT
    board = _output_board(payload)
    if not board:
        log.warning("Output board %s missing from frame", OUTPUT_DEVICE_ID)
        output_board_lost(client)
        return

    outputs = board.get("state") or {}
    modes = board.get("mode") or {}
    with _MODES_LOCK:
        LATEST_MODES.clear()
        LATEST_MODES.update(modes)
        LATEST_OUTPUTS.clear()
        LATEST_OUTPUTS.update(outputs)
        _BOARD_FRAME_AT = time.monotonic()
    shadow = board.get("shadow") or {}

    def send(uid, on):
        client.publish(f"{MQTT_BASE_TOPIC}/{uid}/state",
                       "ON" if on else "OFF", retain=True)

    for ch in OUTPUT_MAP:
        key = f"output_{ch}"
        if key in outputs:
            send(key, bool(outputs[key]))

    excluded = {f"output_{c}" for c in TASK_MODE_EXCLUDE}
    send("task_mode_active",
         any(v != "auto" for k, v in modes.items() if k not in excluded))

    send("output_board_connected", bool(board.get("connected")))

    send("relay_state_mismatch",
         any(outputs[k] != shadow[k] for k in outputs if k in shadow))

    client.publish(MANUAL_CONTROL_AVAILABLE_TOPIC,
                   "online" if _farm_in_task_mode() else "offline", retain=True)

    cool_down_on_frame(client, outputs)


def publish_binary_discovery(client, availability_topic: str):
    for bsensor in BINARY_SENSORS:
        config_topic = f"{DISCOVERY_PREFIX}/binary_sensor/{bsensor.unique_id}/config"
        payload = {
            "name": bsensor.name,
            "unique_id": f"greenery_s_{bsensor.unique_id}",
            "state_topic": f"{MQTT_BASE_TOPIC}/{bsensor.unique_id}/state",
            "availability_topic": availability_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": DEVICE_INFO,
        }
        if bsensor.device_class:
            payload["device_class"] = bsensor.device_class
        if bsensor.entity_category:
            payload["entity_category"] = bsensor.entity_category
        client.publish(config_topic, json.dumps(payload), qos=1, retain=True)
        log.info(f"Published discovery config for {bsensor.name}")
# --- END relay output board mapping ---

# --- BEGIN farmhand task mode control (added by apply_control_patch) ---
# ---------------------------------------------------------------------------
# ONE-TAP TASK MODE CONTROL
#
# Captured from the farmhand local UI (DevTools):
#     {"command": "enter_mode", "mode": "task_mode"}
#     {"command": "exit_mode"}
#     {"command": "get_current_mode"}
#
# UNSUPPORTED ENDPOINT. Freight Farms can change this in any update. It will
# fail loudly (Farm Control Status goes to "failed"), not silently - but expect
# to re-capture it after a farmhand update.
#
# Endpoint confirmed by DevTools capture on this farm. Both values are
# overridable via farm-bridge.env (FARM_CONTROL_URL, FARM_CONTROL_TRANSPORT)
# if the farm's IP changes or a future farmhand build moves the route.
# ---------------------------------------------------------------------------

# CONFIRMED from DevTools: POST http://192.168.200.200:3001/farm-control -> 200 OK
FARMHAND_CONTROL_TRANSPORT = os.environ.get("FARM_CONTROL_TRANSPORT", "http")   # "http" or "ws"
FARMHAND_CONTROL_URL = os.environ.get(
    "FARM_CONTROL_URL", "http://192.168.200.200:3001/farm-control"
)

CMD_ENTER_TASK_MODE = {"command": "enter_mode", "mode": "task_mode"}
CMD_EXIT_MODE = {"command": "exit_mode"}
CMD_GET_MODE = {"command": "get_current_mode"}

CONTROL_TIMEOUT = 15          # seconds for the command round-trip
VERIFY_DELAY = 6              # seconds to wait for the SSE stream to reflect it

# Latest per-channel mode and relay state from the output board, refreshed on
# every SSE frame. This is the ground truth used to verify a command actually
# took effect.
#
# Written from the asyncio thread (via publish_binary_states) and read from
# the command threads - _MODES_LOCK guards all three, so a reader never sees a
# mid-clear()/update() dict.
LATEST_MODES = {}
LATEST_OUTPUTS = {}
_BOARD_FRAME_AT = None        # time.monotonic() of the last frame with the board
_MODES_LOCK = threading.Lock()

# One farm command at a time, whichever button or switch sent it, so two
# presses can never interleave their send-and-verify.
_FARM_COMMAND_LOCK = threading.Lock()


def _control_status(client, text):
    # Home Assistant rejects a sensor state longer than 255 characters.
    text = text[:255]
    client.publish(f"{MQTT_BASE_TOPIC}/control_status/state", text, retain=True)
    log.info("Farm control status: %s", text)


def _farm_in_task_mode() -> bool:
    """True if any channel is off 'auto', ignoring the permanently-manual ones."""
    excluded = {f"output_{c}" for c in TASK_MODE_EXCLUDE}
    with _MODES_LOCK:
        modes = dict(LATEST_MODES)
    return any(v != "auto" for k, v in modes.items() if k not in excluded)


async def _ws_command(payload: dict) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(FARMHAND_CONTROL_URL,
                                      timeout=CONTROL_TIMEOUT) as ws:
            await ws.send_json(payload)
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=CONTROL_TIMEOUT)
                return str(msg.data)[:200]
            except asyncio.TimeoutError:
                return "(sent, no reply)"


def _http_command(payload: dict) -> str:
    import urllib.request
    req = urllib.request.Request(
        FARMHAND_CONTROL_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=CONTROL_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")[:200]


def farmhand_command(payload: dict) -> str:
    """Send one command. Runs in a command thread (see run_farm_command), so it
    uses its own short-lived event loop rather than reaching into the main one."""
    # Every command passes here, so no code path can write a setting the
    # allow-list does not name. See "24-hour settings switches" below.
    if payload.get("command") == "bulk_update_settings":
        _check_settings_write(payload.get("settings"))
    if FARMHAND_CONTROL_TRANSPORT == "http":
        return _http_command(payload)
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_ws_command(payload))
    finally:
        loop.close()


def _send_mode_command(client, want_task_mode: bool, label: str) -> bool:
    """Send enter/exit, then VERIFY against the output board rather than
    assuming. True only if the board confirms. Cool-Down reuses this."""
    payload = CMD_ENTER_TASK_MODE if want_task_mode else CMD_EXIT_MODE

    _control_status(client, f"{label}: sending")
    try:
        reply = farmhand_command(payload)
        log.info("farmhand replied: %s", reply)
    except Exception as e:
        _control_status(client, f"{label}: FAILED - {type(e).__name__}: {e}")
        return False

    _control_status(client, f"{label}: sent, verifying")
    time.sleep(VERIFY_DELAY)

    actual = _farm_in_task_mode()
    if actual == want_task_mode:
        _control_status(client, f"{label}: CONFIRMED")
        return True
    _control_status(
        client,
        f"{label}: NOT CONFIRMED - board still reports "
        f"{'task mode' if actual else 'auto'}"
    )
    return False


def handle_control_command(client, action: str):
    """Send, then VERIFY against the output board rather than assuming."""
    want_task_mode = (action == "enter")
    label = "Enter Task Mode" if want_task_mode else "Exit Task Mode"
    _send_mode_command(client, want_task_mode, label)


def publish_button_discovery(client, availability_topic: str):
    buttons = [
        ("enter_task_mode", "Enter Task Mode", "mdi:hand-back-right"),
        ("exit_task_mode", "Exit Task Mode", "mdi:play-circle"),
        ("start_cool_down", "Start Cool-Down", "mdi:snowflake-thermometer"),
        ("end_cool_down", "End Cool-Down", "mdi:play-circle"),
    ]
    for uid, name, icon in buttons:
        client.publish(
            f"{DISCOVERY_PREFIX}/button/{uid}/config",
            json.dumps({
                "name": name,
                "unique_id": f"greenery_s_{uid}",
                "command_topic": f"{MQTT_BASE_TOPIC}/{uid}/set",
                "payload_press": "PRESS",
                "availability_topic": availability_topic,
                "icon": icon,
                "device": DEVICE_INFO,
            }),
            qos=1, retain=True,
        )
        log.info(f"Published discovery config for {name}")

    client.publish(
        f"{DISCOVERY_PREFIX}/sensor/control_status/config",
        json.dumps({
            "name": "Farm Control Status",
            "unique_id": "greenery_s_control_status",
            "state_topic": f"{MQTT_BASE_TOPIC}/control_status/state",
            "availability_topic": availability_topic,
            "icon": "mdi:message-alert",
            "entity_category": "diagnostic",
            "device": DEVICE_INFO,
        }),
        qos=1, retain=True,
    )
    client.publish(f"{MQTT_BASE_TOPIC}/control_status/state", "idle", retain=True)
    log.info("Published discovery config for Farm Control Status")


def run_farm_command(handler, *args):
    """Run a command handler off paho's network thread, one at a time.

    The handlers block for seconds while they verify; on paho's thread that
    would stall every MQTT message behind them.
    """
    def worker():
        if not _FARM_COMMAND_LOCK.acquire(blocking=False):
            log.info("Farm command waiting for the previous one to finish")
            _FARM_COMMAND_LOCK.acquire()
        try:
            handler(*args)
        except Exception:
            log.exception("Farm command handler crashed")
        finally:
            _FARM_COMMAND_LOCK.release()

    threading.Thread(target=worker, name="farm-command", daemon=True).start()
# --- END farmhand task mode control ---

# --- BEGIN task-mode pump switches ---
# ---------------------------------------------------------------------------
# PUMP SWITCHES - TASK MODE ONLY
#
# Captured from the farmhand local UI 2026-09-25; the pumps physically ran:
#     {"command": "manual_action", "module": "244CAB0FC00C",
#      "output": "output_2", "type": "set_output", "value": "on"}
#
# Same endpoint as Task Mode. farmhand replies "Control message received!" to
# EVERY command, including ones it ignores, so the reply proves nothing: the
# result is read back from the relay's own state in the SSE stream.
# ---------------------------------------------------------------------------

OUTPUT_VERIFY_TIMEOUT = 10    # seconds to wait for the relay to report the change
OUTPUT_VERIFY_POLL = 0.5
# A command needs a frame at most this old to decide Task Mode from; stale data
# is not proof of anything. Also how long the switches stay available with no
# new frame: farmhand ends /farm-data every ~60 s, and the reconnect is far
# shorter than this, so a routine reconnect no longer greys them out.
BOARD_FRESH_SECONDS = 30
BOARD_CHECK_SECONDS = 2       # how often watch_board_data looks


def _board_reading(key: str):
    """(relay state or None, monotonic time of the frame it came from)."""
    with _MODES_LOCK:
        raw = LATEST_OUTPUTS.get(key)
        at = _BOARD_FRAME_AT
    return (None if raw is None else bool(raw)), at


def output_board_lost(client):
    """A frame arrived WITHOUT the output board: no proof of Task Mode.

    Refuse switch commands until the board is back, and grey the switches out
    - so a command published by hand is refused just as HA's UI would refuse
    it. Not called on a stream reconnect: see watch_board_data.
    """
    global _BOARD_FRAME_AT
    with _MODES_LOCK:
        _BOARD_FRAME_AT = None
    client.publish(MANUAL_CONTROL_AVAILABLE_TOPIC, "offline", qos=1, retain=True)


def _board_fresh() -> bool:
    """The latest frame with the output board is no older than BOARD_FRESH_SECONDS."""
    with _MODES_LOCK:
        at = _BOARD_FRAME_AT
    return at is not None and time.monotonic() - at <= BOARD_FRESH_SECONDS


async def watch_board_data(client):
    """Grey the switches out once no frame has carried the output board for
    BOARD_FRESH_SECONDS - the farm is unreachable, not just reconnecting.
    Until then they keep the last known Task Mode state; the next frame
    republishes it either way."""
    stale_reported = False
    while True:
        await asyncio.sleep(BOARD_CHECK_SECONDS)
        if _board_fresh():
            stale_reported = False
        elif not stale_reported:
            log.warning("No output board data for %ss - pump switches unavailable",
                        BOARD_FRESH_SECONDS)
            client.publish(MANUAL_CONTROL_AVAILABLE_TOPIC, "offline", qos=1, retain=True)
            stale_reported = True


def handle_output_command(client, ch: int, want_on: bool):
    """Switch one relay channel, then VERIFY it from the relay's own state."""
    name = OUTPUT_MAP[ch][0]

    # Decided now, from the latest data - not from whenever HA last saw the
    # switch available - and only if that data is under BOARD_FRESH_SECONDS old.
    if not _board_fresh():
        _control_status(client, f"{name}: REFUSED - no recent data from the output board")
        return
    if not _farm_in_task_mode():
        _control_status(client, f"{name}: REFUSED - farm not in Task Mode")
        return

    _switch_output(client, ch, want_on)


def _switch_output(client, ch: int, want_on: bool, prefix: str = "") -> bool:
    """Send one channel on/off and watch the relay. No Task Mode check - the
    caller makes it. True only once the relay itself reports the new state."""
    key = f"output_{ch}"
    label = f"{prefix}{OUTPUT_MAP[ch][0]} {'ON' if want_on else 'OFF'}"

    _control_status(client, f"{label}: sending")
    started = time.monotonic()
    try:
        reply = farmhand_command({
            "command": "manual_action", "module": OUTPUT_DEVICE_ID,
            "output": key, "type": "set_output",
            "value": "on" if want_on else "off",
        })
        log.info("farmhand replied: %s", reply)
    except Exception as e:
        _control_status(client, f"{label}: FAILED - {type(e).__name__}: {e}")
        return False

    _control_status(client, f"{label}: sent, verifying")
    # Only a frame that arrived after farmhand took the command counts.
    acked = time.monotonic()
    while True:
        state, frame_at = _board_reading(key)
        fresh = frame_at is not None and frame_at >= acked
        if fresh and state == want_on:
            _control_status(client, f"{label}: CONFIRMED in {time.monotonic() - started:.1f}s")
            return True
        if time.monotonic() - acked >= OUTPUT_VERIFY_TIMEOUT:
            break
        time.sleep(OUTPUT_VERIFY_POLL)

    if not fresh:
        _control_status(client, f"{label}: NOT CONFIRMED - no update from the "
                                f"output board in {OUTPUT_VERIFY_TIMEOUT}s")
    else:
        reported = "nothing" if state is None else ("ON" if state else "OFF")
        _control_status(client, f"{label}: NOT CONFIRMED - relay still reports {reported}")
    return False


def publish_switch_discovery(client, availability_topic: str):
    for ch in OUTPUT_MAP:
        config_topic = f"{DISCOVERY_PREFIX}/switch/output_{ch}_switch/config"
        if ch not in MANUAL_CONTROL_CHANNELS:
            # An empty retained config deletes the switch, so taking a channel
            # off the list removes it from HA too.
            client.publish(config_topic, "", qos=1, retain=True)
            continue
        name = f"{OUTPUT_MAP[ch][0]} Switch"
        client.publish(
            config_topic,
            json.dumps({
                "name": name,
                "unique_id": f"greenery_s_output_{ch}_switch",
                "command_topic": f"{MQTT_BASE_TOPIC}/output_{ch}/set",
                "state_topic": f"{MQTT_BASE_TOPIC}/output_{ch}/state",
                "payload_on": "ON",
                "payload_off": "OFF",
                "optimistic": False,
                "retain": False,
                # Both must be online: the bridge is up AND the farm is in
                # Task Mode. Otherwise HA shows the switch unavailable.
                "availability": [
                    {"topic": availability_topic},
                    {"topic": MANUAL_CONTROL_AVAILABLE_TOPIC},
                ],
                "availability_mode": "all",
                "icon": "mdi:pump",
                "device": DEVICE_INFO,
            }),
            qos=1, retain=True,
        )
        log.info(f"Published discovery config for {name}")

    # Unavailable until the first frame proves Task Mode - never "online" left
    # over from before a restart.
    client.publish(MANUAL_CONTROL_AVAILABLE_TOPIC, "offline", qos=1, retain=True)
# --- END task-mode pump switches ---

# --- BEGIN cool-down ---
# ---------------------------------------------------------------------------
# COOL-DOWN - ONE TAP FROM THE PHONE, NEVER AUTOMATIC
#
# For a farm running hot in the day: Task Mode takes the grow lights off, then
# relays 1 and 8 are switched back on so water keeps moving through the
# chiller. Built only from the two commands above - enter/exit_mode and
# manual_action - and each step confirmed from the output board.
#
# Not for cleanouts: those are run from farmhand Local. The bridge never
# starts or ends a cool-down by itself; the watchdog below only reports.
# ---------------------------------------------------------------------------

COOL_DOWN_PUMPS = (1, 8)   # cultivation recirc, nursery recirc + chiller pump

if not set(COOL_DOWN_PUMPS) <= set(MANUAL_CONTROL_CHANNELS):
    raise RuntimeError("COOL_DOWN_PUMPS must all be in MANUAL_CONTROL_CHANNELS - "
                       "Cool-Down only switches physically tested channels")

# "Lights off" is read from these relays, not assumed from Task Mode.
COOL_DOWN_LIGHTS = tuple(ch for ch, (n, _dc, _cb) in sorted(OUTPUT_MAP.items())
                         if n.startswith("Cultivation LED"))

COOL_DOWN_ACTIVE_TOPIC = f"{MQTT_BASE_TOPIC}/cool_down_active/state"
COOL_DOWN_FAULT_TOPIC = f"{MQTT_BASE_TOPIC}/cool_down_fault/state"
COOL_DOWN_EXIT_CHECK_SECONDS = 15      # after End, both pumps back ON within this
COOL_DOWN_PUMP_OFF_FAULT_SECONDS = 60  # watchdog: a pump OFF this long is a fault
# After a restart the first frame can beat the broker's retained Active. Wait
# this long after subscribing before deciding there was none.
COOL_DOWN_RESTORE_WAIT_SECONDS = 3


@dataclass
class _CoolDownState:
    active: bool = False
    busy: bool = False            # a Start/End handler owns the state right now
    settled: bool = False         # Active published from a live frame since start
    retained_seen: bool = False   # the broker's pre-restart Active has been read
    restore: bool = False         # ...and it was ON: re-check on the next frame
    read_until: float = None      # monotonic end of the wait for that value
    off_since: dict = field(default_factory=dict)   # ch -> monotonic first OFF
    reported: set = field(default_factory=set)      # chs the watchdog flagged


_CD = _CoolDownState()
_COOL_DOWN_LOCK = threading.Lock()


def _publish_cool_down_active(client, active: bool):
    with _COOL_DOWN_LOCK:
        _CD.active = active
        _CD.settled = True
        _CD.off_since.clear()
        _CD.reported.clear()
    client.publish(COOL_DOWN_ACTIVE_TOPIC, "ON" if active else "OFF", qos=1, retain=True)


def _publish_cool_down_fault(client, fault: bool, status: str = None):
    # Status first: the fault alert quotes Farm Control Status.
    if status:
        _control_status(client, status)
    client.publish(COOL_DOWN_FAULT_TOPIC, "ON" if fault else "OFF", qos=1, retain=True)


def cool_down_subscribed():
    with _COOL_DOWN_LOCK:
        if _CD.read_until is None:
            _CD.read_until = time.monotonic() + COOL_DOWN_RESTORE_WAIT_SECONDS


def cool_down_retained(active_on: bool):
    """The broker's retained Cool-Down Active, as found after a (re)start.

    Only the first one counts: later deliveries are the bridge's own."""
    with _COOL_DOWN_LOCK:
        if not _CD.retained_seen:
            _CD.retained_seen = True
            _CD.restore = active_on


def _lights_on() -> list:
    with _MODES_LOCK:
        return [ch for ch in COOL_DOWN_LIGHTS if LATEST_OUTPUTS.get(f"output_{ch}")]


def _cool_down_pumps_stopped(since: float):
    """(COOL_DOWN_PUMPS names not reporting ON, whether a frame since `since` exists)."""
    with _MODES_LOCK:
        stopped = [OUTPUT_MAP[ch][0] for ch in COOL_DOWN_PUMPS
                   if not LATEST_OUTPUTS.get(f"output_{ch}")]
        fresh = _BOARD_FRAME_AT is not None and _BOARD_FRAME_AT >= since
    return stopped, fresh


def _run_cool_down(client, handler):
    """Fresh board data or nothing is sent; the frame watcher stands aside
    while the handler owns the cool-down state."""
    if not _board_fresh():
        _control_status(client, "Cool-Down: REFUSED - no recent data from the output board")
        return
    with _COOL_DOWN_LOCK:
        _CD.busy = True
    try:
        handler(client)
    finally:
        with _COOL_DOWN_LOCK:
            _CD.busy = False


def handle_start_cool_down(client):
    _run_cool_down(client, _start_cool_down)


def handle_end_cool_down(client):
    _run_cool_down(client, _end_cool_down)


def _start_cool_down(client):
    # Each attempt starts clean, so whatever it finds is a fresh OFF -> ON and
    # alerts again.
    _publish_cool_down_fault(client, False)

    # The bridge's "Task Mode" is any channel off auto; one channel held in
    # manual is not Task Mode, and its grow lights are still on.
    if _farm_in_task_mode() and not _lights_on():
        log.info("Cool-Down: farm already in Task Mode with the grow lights off")
    elif not _send_mode_command(client, True, "Cool-Down - Enter Task Mode"):
        _publish_cool_down_fault(client, True, "Cool-Down: FAILED - Task Mode not confirmed")
        return

    not_running = []
    for ch in COOL_DOWN_PUMPS:
        if not (_switch_output(client, ch, True, "Cool-Down - ")
                or _switch_output(client, ch, True, "Cool-Down retry - ")):
            not_running.append(OUTPUT_MAP[ch][0])

    # In Task Mode either way, so this is a cool-down, fault or not. The owner
    # decides when it ends.
    _publish_cool_down_active(client, True)
    lights = _lights_on()
    if not_running:
        _publish_cool_down_fault(
            client, True, f"Cool-Down: FAULT - {' and '.join(not_running)} not running")
    elif lights:
        _control_status(client, f"Cool-Down: ACTIVE - recirc and chiller pump running, but "
                                f"{len(lights)} grow light relay(s) still report ON - "
                                f"check farmhand")
    else:
        _control_status(client, "Cool-Down: ACTIVE - lights off, recirc and chiller pump running")


def _end_cool_down(client):
    if _farm_in_task_mode():
        if not _send_mode_command(client, False, "Cool-Down - Exit Task Mode"):
            # Still in Task Mode: the cool-down, and its watchdog, carry on.
            _control_status(client, "Cool-Down: NOT ENDED - Task Mode exit not confirmed, "
                                    "cool-down still active")
            return
    else:
        log.info("Cool-Down: farm already out of Task Mode; nothing sent")

    _publish_cool_down_fault(client, False)
    # Both pumps run on auto, so the resumed recipe keeps them ON.
    since = time.monotonic()
    while True:
        stopped, fresh = _cool_down_pumps_stopped(since)
        if (fresh and not stopped) or time.monotonic() - since >= COOL_DOWN_EXIT_CHECK_SECONDS:
            break
        time.sleep(OUTPUT_VERIFY_POLL)

    if not fresh:
        _publish_cool_down_fault(client, True, f"Cool-Down: FAULT - no update from the output "
                                               f"board in {COOL_DOWN_EXIT_CHECK_SECONDS}s, "
                                               f"pumps not confirmed after exit")
    elif stopped:
        _publish_cool_down_fault(
            client, True, f"Cool-Down: FAULT - {' and '.join(stopped)} stopped after exit")
    else:
        _control_status(client, "Cool-Down: ENDED - recipe resumed")
    _publish_cool_down_active(client, False)


def cool_down_on_frame(client, outputs: dict):
    """Every frame with the output board. Keeps Cool-Down Active true to the
    farm and watches the pumps. Never sends a command."""
    in_task_mode = _farm_in_task_mode()
    now = time.monotonic()
    publish, ended_outside, stopped = False, False, []
    with _COOL_DOWN_LOCK:
        if _CD.busy:
            return
        if not (_CD.settled or _CD.retained_seen
                or (_CD.read_until is not None and now >= _CD.read_until)):
            return    # still waiting to learn whether one was running
        if _CD.restore:
            # Restarted mid cool-down: it survives only if the farm is still
            # in Task Mode.
            _CD.restore = False
            if in_task_mode and not _CD.active:
                _CD.active = publish = True
                log.info("Cool-Down still active after restart: farm is in Task Mode")
        if not _CD.settled:
            # First word since start. OFF clears a stale retained ON.
            _CD.settled = publish = True
        elif _CD.active and not in_task_mode:
            _CD.active = False
            publish = ended_outside = True

        if _CD.active:
            for ch in COOL_DOWN_PUMPS:
                raw = outputs.get(f"output_{ch}")
                if raw is None:
                    continue
                if raw:
                    _CD.off_since.pop(ch, None)
                    _CD.reported.discard(ch)
                    continue
                # Not reset when the stream reconnects (every ~60 s): only an
                # ON report ends an OFF spell.
                first_off = _CD.off_since.setdefault(ch, now)
                if now - first_off >= COOL_DOWN_PUMP_OFF_FAULT_SECONDS and ch not in _CD.reported:
                    _CD.reported.add(ch)
                    stopped.append(OUTPUT_MAP[ch][0])
        else:
            _CD.off_since.clear()
            _CD.reported.clear()
        active = _CD.active

    if publish:
        client.publish(COOL_DOWN_ACTIVE_TOPIC, "ON" if active else "OFF", qos=1, retain=True)
    if ended_outside:
        _publish_cool_down_fault(client, False, "Cool-Down: ENDED - farm left Task Mode "
                                                "without End Cool-Down")
    if stopped:
        _publish_cool_down_fault(client, True, f"Cool-Down: FAULT - {' and '.join(stopped)} stopped")
# --- END cool-down ---

# --- BEGIN module offline detection (added by apply_modules_patch) ---
# ---------------------------------------------------------------------------
# MODULE OFFLINE DETECTION
#
# Replicates the Freight Farms built-in alert "Module has been offline for 5
# minutes" locally, so it still fires when the farmhand cloud is unreachable.
#
# device_id -> (friendly name, severity, what breaks when it is offline)
# Severity drives which automation treats it as critical.
# ---------------------------------------------------------------------------

MODULE_MAP = {
    "94E68607D090": ("Environmental Module", "critical",
                     "farm refuses to run HVAC or LEDs"),
    "244CAB0FC00C": ("Output Module", "critical",
                     "no farm equipment receives power"),
    "94E68607D218": ("Cultivation Dosing Module", "high",
                     "no cultivation dosing; pH and EC drift uncorrected"),
    "244CAB0FD624": ("Nursery Dosing Module", "high",
                     "no nursery dosing; pH and EC drift uncorrected"),
    "8C4B14715024": ("Input Module", "info",
                     "tank depth and pressure data stops updating"),
}

# farmhand's own definition of offline.
MODULE_STALE_SECONDS = 300


def _module_slug(name: str) -> str:
    return name.lower().replace(" ", "_")


MODULE_SENSORS = [
    BinarySensorDef(
        unique_id=f"{_module_slug(_name)}_online",
        name=f"{_name} Online",
        device_class="connectivity",
        entity_category=None if _sev in ("critical", "high") else "diagnostic",
    )
    for _dev, (_name, _sev, _why) in MODULE_MAP.items()
] + [
    # One rollup so a dashboard can show a single red light, and so an
    # automation can catch a module we have not individually mapped.
    BinarySensorDef("any_module_offline", "Any Module Offline",
                    device_class="problem"),
]


def publish_module_states(client, payload: dict):
    """A module is offline if it says so, OR if it has stopped reporting.

    The staleness half matters more: a module can keep claiming `connected`
    while its data goes stale, and the bridge would republish the last known
    values indefinitely - plausible readings that no longer describe the farm.
    """
    devices = payload.get("state") or {}
    now = time.time()
    any_offline = False

    for dev_id, (name, _sev, _why) in MODULE_MAP.items():
        dev = devices.get(dev_id) or {}

        if not dev:
            online = False
        else:
            connected = dev.get("connected")
            last = dev.get("last_update")
            fresh = True
            if isinstance(last, (int, float)):
                fresh = (now - float(last)) < MODULE_STALE_SECONDS
            online = (connected is not False) and fresh

        if not online:
            any_offline = True
            log.warning("Module offline: %s (%s)", name, dev_id)

        client.publish(
            f"{MQTT_BASE_TOPIC}/{_module_slug(name)}_online/state",
            "ON" if online else "OFF",
            retain=True,
        )

    client.publish(
        f"{MQTT_BASE_TOPIC}/any_module_offline/state",
        "ON" if any_offline else "OFF",
        retain=True,
    )


def publish_module_discovery(client, availability_topic: str):
    for bsensor in MODULE_SENSORS:
        payload = {
            "name": bsensor.name,
            "unique_id": f"greenery_s_{bsensor.unique_id}",
            "state_topic": f"{MQTT_BASE_TOPIC}/{bsensor.unique_id}/state",
            "availability_topic": availability_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": DEVICE_INFO,
        }
        if bsensor.device_class:
            payload["device_class"] = bsensor.device_class
        if bsensor.entity_category:
            payload["entity_category"] = bsensor.entity_category
        client.publish(
            f"{DISCOVERY_PREFIX}/binary_sensor/{bsensor.unique_id}/config",
            json.dumps(payload), qos=1, retain=True,
        )
        log.info(f"Published discovery config for {bsensor.name}")
# --- END module offline detection ---




# ---------------------------------------------------------------------------
# MQTT setup
# ---------------------------------------------------------------------------

log = logging.getLogger("farm_bridge")

CONNACK_CODES = {
    0: "Connected successfully",
    1: "Refused - incorrect protocol version",
    2: "Refused - invalid client identifier",
    3: "Refused - server unavailable",
    4: "Refused - bad username or password",
    5: "Refused - not authorized (check FARM_MQTT_USER/FARM_MQTT_PASS)",
}


def build_mqtt_client() -> mqtt.Client:
    client = mqtt.Client(client_id="farm-bridge", clean_session=True)
    if MQTT_USERNAME:
        client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    availability_topic = f"{MQTT_BASE_TOPIC}/bridge/status"
    client.will_set(availability_topic, payload="offline", qos=1, retain=True)

    output_set_topics = {f"{MQTT_BASE_TOPIC}/output_{ch}/set": ch
                         for ch in MANUAL_CONTROL_CHANNELS}
    fh24_set_topics = {f"{MQTT_BASE_TOPIC}/{group}/set": group for group in FH24_GROUPS}
    button_handlers = {
        f"{MQTT_BASE_TOPIC}/enter_task_mode/set": (handle_control_command, "enter"),
        f"{MQTT_BASE_TOPIC}/exit_task_mode/set": (handle_control_command, "exit"),
        f"{MQTT_BASE_TOPIC}/start_cool_down/set": (handle_start_cool_down,),
        f"{MQTT_BASE_TOPIC}/end_cool_down/set": (handle_end_cool_down,),
    }

    def on_connect(c, userdata, flags, rc):
        msg = CONNACK_CODES.get(rc, f"Unknown return code {rc}")
        if rc == 0:
            log.info(f"MQTT connected: {msg}")
            c.publish(availability_topic, "online", qos=1, retain=True)
            for topic in [*button_handlers, *output_set_topics, *fh24_set_topics]:
                c.subscribe(topic, qos=1)
            # Read back, not acted on: whether a cool-down was running when
            # the bridge last stopped. See cool_down_on_frame.
            c.subscribe(COOL_DOWN_ACTIVE_TOPIC, qos=1)
            cool_down_subscribed()
        else:
            log.error(f"MQTT connection FAILED: {msg}")

    def on_disconnect(c, userdata, rc):
        if rc != 0:
            log.warning(f"MQTT disconnected unexpectedly (rc={rc}); paho will auto-reconnect")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    def on_message(c, userdata, msg):
        topic = msg.topic
        if topic == COOL_DOWN_ACTIVE_TOPIC:
            if msg.retain:
                cool_down_retained(msg.payload == b"ON")
            return
        # The broker replays a retained message on every (re)subscribe - i.e.
        # every bridge restart and reconnect. A command must never replay, so
        # drop it and clear it off the broker.
        if topic.endswith("/set") and msg.retain:
            log.warning("IGNORED retained command on %s - commands are never "
                        "replayed; clearing it from the broker", topic)
            c.publish(topic, "", qos=1, retain=True)
            return
        # The clear above arrives back here as an empty message. Home
        # Assistant never sends an empty command.
        if not msg.payload:
            return
        log.info("MQTT command received on %s", topic)
        if topic in button_handlers:
            handler, *args = button_handlers[topic]
            run_farm_command(handler, c, *args)
        elif topic in output_set_topics or topic in fh24_set_topics:
            value = msg.payload.decode("utf-8", "replace").strip().upper()
            if value not in ("ON", "OFF"):
                log.warning("Ignored %r on %s - expected ON or OFF", value, topic)
                return
            if topic in output_set_topics:
                run_farm_command(handle_output_command, c,
                                 output_set_topics[topic], value == "ON")
            else:
                run_farm_command(handle_fh24_command, c,
                                 fh24_set_topics[topic], value == "ON")

    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    except (OSError, ConnectionRefusedError) as e:
        log.error(
            f"Could not reach MQTT broker at {MQTT_HOST}:{MQTT_PORT} - {e}. "
            f"Check the IP is correct and port 1883 is reachable from this machine."
        )
        raise

    client.loop_start()
    return client


def publish_discovery(client: mqtt.Client):
    """Publish HA MQTT discovery configs once at startup so sensors auto-register."""
    availability_topic = f"{MQTT_BASE_TOPIC}/bridge/status"

    # Bridge status itself, as a visible entity (not just an availability gate
    # for the other sensors) - lets the dashboard show connectivity at a glance.
    status_config_topic = f"{DISCOVERY_PREFIX}/binary_sensor/farm_bridge_status/config"
    status_payload = {
        "name": "Farm Bridge Status",
        "unique_id": "greenery_s_bridge_status",
        "state_topic": availability_topic,
        "payload_on": "online",
        "payload_off": "offline",
        "device_class": "connectivity",
        "device": DEVICE_INFO,
    }
    client.publish(status_config_topic, json.dumps(status_payload), qos=1, retain=True)
    log.info("Published discovery config for Farm Bridge Status")

    for sensor in SENSORS:
        state_topic = f"{MQTT_BASE_TOPIC}/{sensor.unique_id}/state"
        config_topic = f"{DISCOVERY_PREFIX}/sensor/{sensor.unique_id}/config"

        payload = {
            "name": sensor.name,
            "unique_id": f"greenery_s_{sensor.unique_id}",
            "state_topic": state_topic,
            "availability_topic": availability_topic,
            "device": DEVICE_INFO,
        }
        if sensor.unit:
            payload["unit_of_measurement"] = sensor.unit
        if sensor.device_class:
            payload["device_class"] = sensor.device_class
        if sensor.state_class:
            payload["state_class"] = sensor.state_class

        client.publish(config_topic, json.dumps(payload), qos=1, retain=True)
        log.info(f"Published discovery config for {sensor.name}")

    publish_binary_discovery(client, availability_topic)
    publish_button_discovery(client, availability_topic)
    publish_switch_discovery(client, availability_topic)
    publish_module_discovery(client, availability_topic)
    publish_farmhand_discovery(client, availability_topic)


def extract_value(payload: dict, sensor: SensorDef):
    try:
        device_state = payload["state"][sensor.device_id]["state"]
        value = device_state
        for key in sensor.state_path:
            value = value[key]
        return value
    except (KeyError, TypeError):
        return None


def publish_states(client: mqtt.Client, payload: dict):
    for sensor in SENSORS:
        value = extract_value(payload, sensor)
        if value is None:
            log.warning(f"No value found for {sensor.name} (device {sensor.device_id})")
            continue
        state_topic = f"{MQTT_BASE_TOPIC}/{sensor.unique_id}/state"
        client.publish(state_topic, round(value, 2) if isinstance(value, float) else value, retain=True)

    publish_binary_states(client, payload)
    publish_module_states(client, payload)

# ---------------------------------------------------------------------------
# SSE stream reader
# ---------------------------------------------------------------------------

async def stream_farm_data(mqtt_client: mqtt.Client):
    timeout = aiohttp.ClientTimeout(total=None, sock_read=SSE_READ_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        log.info(f"Connecting to SSE stream: {SSE_URL}")
        async with session.get(SSE_URL) as response:
            async for line_bytes in response.content:
                line = line_bytes.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                raw_json = line[len("data:"):].strip()
                try:
                    payload = json.loads(raw_json)
                except json.JSONDecodeError:
                    log.warning("Skipped malformed JSON line from SSE stream")
                    continue

                publish_states(mqtt_client, payload)
                log.debug(f"Published farm state @ {time.strftime('%X')}")


# ---------------------------------------------------------------------------
# FARMHAND REPLY STREAM - /farm-monitoring
#
# /farm-control answers every command "Control message received!". The real
# answers arrive on farmhand's second SSE stream as JSON events, verified
# 2026-10-15 for read_settings_files, get_farm_info, get_version,
# get_current_mode and read_modules:
#     {"response_to": "get_version", "response": "2.0.26", "error_message": "",
#      "controller_id": "...", "request_id": "..."}
#
# The bridge reads it all the time. Once at start and every FH_POLL_SECONDS it
# sends get_farm_info and get_version - read-only, and both sent by farmhand's
# own page on every load. Those are the only things it sends unprompted.
# ---------------------------------------------------------------------------

# Follows the farm's host and port, like FARM_CONTROL_URL does.
MONITORING_URL = os.environ.get("FARM_MONITORING_URL",
                                SSE_URL.rsplit("/", 1)[0] + "/farm-monitoring")
# Investigation aid: log the first MONITORING_LOG_EVENTS events after start.
FARM_LOG_MONITORING = os.environ.get("FARM_LOG_MONITORING", "").strip().lower() in (
    "1", "true", "yes", "on")
MONITORING_LOG_EVENTS = 20
MONITORING_LOG_CHARS = 2000        # per event, so one huge frame cannot flood the log
# farmhand's page asks for read_settings_files on every load and every
# listener gets the reply. An event bigger than this is skipped, not buffered.
MONITORING_MAX_EVENT_BYTES = 8_000_000

FH_POLL_SECONDS = 300
FH_POLL_RETRY_SECONDS = 60         # sooner, after a poll that got no reply
FH_REPLY_TIMEOUT = 10
FH_REPLY_ATTEMPT_SECONDS = 4       # verify-after-write asks again after this

CMD_GET_FARM_INFO = {"command": "get_farm_info"}
CMD_GET_VERSION = {"command": "get_version"}

FARMHAND_VERSION_TOPIC = f"{MQTT_BASE_TOPIC}/farmhand_version/state"

# The Hub's LIVE running settings, from the latest get_farm_info reply.
# Written on the asyncio thread, read by command threads, under _FH_REPLIES.
LATEST_FH_SETTINGS = {}
LATEST_FH_VERSION = None
_FH_REPLIES = threading.Condition()
_fh_info_replies = 0               # get_farm_info replies seen; waiters count on it
_monitoring_drops = 0              # a reply in flight across a drop is lost
_MONITORING_CONNECTED = threading.Event()
_monitoring_logged = 0


def handle_monitoring_event(client, msg):
    global _fh_info_replies, LATEST_FH_VERSION
    if not isinstance(msg, dict) or "response_to" not in msg:
        log.debug("farm-monitoring: event without response_to ignored")
        return
    what = msg.get("response_to")
    response = msg.get("response")
    if msg.get("error_message"):
        log.warning("farmhand error replying to %s: %s", what, msg["error_message"])

    if what == "get_farm_info":
        settings = response.get("settings") if isinstance(response, dict) else None
        if not isinstance(settings, dict):
            log.warning("get_farm_info reply carries no settings - ignored")
            return
        with _FH_REPLIES:
            LATEST_FH_SETTINGS.clear()
            LATEST_FH_SETTINGS.update(settings)
            _fh_info_replies += 1
            _FH_REPLIES.notify_all()
        publish_fh24_states(client, settings)
    elif what == "get_version":
        version = str(response).strip() if isinstance(response, (str, int, float)) else ""
        if not version:
            return
        if version != LATEST_FH_VERSION:
            log.info("farmhand version: %s", version)
        LATEST_FH_VERSION = version
        client.publish(FARMHAND_VERSION_TOPIC, version, qos=1, retain=True)
    else:
        log.debug("farm-monitoring: reply to %s", what)


def request_farm_info(timeout: float = FH_REPLY_TIMEOUT):
    """Send get_farm_info and wait for the next reply: the settings, or None.

    "Next" means any reply after the send - possibly one answering someone
    else's earlier request. A caller verifying a write therefore checks the
    values and asks again until they appear or time runs out.
    """
    deadline = time.monotonic() + timeout
    # A reply sent while the stream is reconnecting is lost.
    if not _MONITORING_CONNECTED.wait(timeout):
        return None
    with _FH_REPLIES:
        seen, drops = _fh_info_replies, _monitoring_drops
        if not _MONITORING_CONNECTED.is_set():
            return None
    try:
        farmhand_command(CMD_GET_FARM_INFO)
    except Exception as e:
        log.warning("get_farm_info not sent: %s: %s", type(e).__name__, e)
        return None
    with _FH_REPLIES:
        _FH_REPLIES.wait_for(lambda: _fh_info_replies > seen or _monitoring_drops != drops,
                             max(0.0, deadline - time.monotonic()))
        return dict(LATEST_FH_SETTINGS) if _fh_info_replies > seen else None


async def poll_farmhand(client):
    """Read-only, at start and every FH_POLL_SECONDS: live settings, version."""
    while True:
        delay = FH_POLL_SECONDS
        if _FARM_COMMAND_LOCK.locked():
            # A command is mid-verify and reads what it needs itself.
            delay = FH_POLL_RETRY_SECONDS
        else:
            if await asyncio.to_thread(request_farm_info) is None:
                log.warning("No get_farm_info reply from farmhand in %ss; asking again in %ss",
                            FH_REPLY_TIMEOUT, FH_POLL_RETRY_SECONDS)
                delay = FH_POLL_RETRY_SECONDS
            if _MONITORING_CONNECTED.is_set():
                try:
                    await asyncio.to_thread(farmhand_command, CMD_GET_VERSION)
                except Exception as e:
                    log.warning("get_version not sent: %s: %s", type(e).__name__, e)
        await asyncio.sleep(delay)


async def _sse_events(content):
    """Yield each SSE event's data, multi-line data: fields joined by newlines.

    Reads raw chunks rather than aiohttp's readline, whose line limit a large
    reply would hit.
    """
    buf = bytearray()
    data, size, skipping = [], 0, False
    async for chunk in content.iter_any():
        scan = len(buf)            # what is already in buf holds no newline
        buf += chunk
        start = 0
        while (nl := buf.find(b"\n", scan)) >= 0:
            line = bytes(buf[start:nl]).rstrip(b"\r")
            start = scan = nl + 1
            if not line:           # a blank line ends the event
                if data and not skipping:
                    yield b"\n".join(data).decode("utf-8", "replace")
                data, size, skipping = [], 0, False
            elif not skipping and line.startswith(b"data:"):
                value = line[5:]
                value = value[1:] if value.startswith(b" ") else value
                size += len(value)
                data.append(value)
                if size > MONITORING_MAX_EVENT_BYTES:
                    log.warning("farm-monitoring: event over %d bytes skipped",
                                MONITORING_MAX_EVENT_BYTES)
                    data, skipping = [], True
        del buf[:start]
        if len(buf) > MONITORING_MAX_EVENT_BYTES:
            log.warning("farm-monitoring: event over %d bytes skipped",
                        MONITORING_MAX_EVENT_BYTES)
            buf.clear()
            data, skipping = [], True


async def stream_farm_monitoring(client):
    global _monitoring_logged, _monitoring_drops
    timeout = aiohttp.ClientTimeout(total=None, sock_read=SSE_READ_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        log.debug("Connecting to reply stream: %s", MONITORING_URL)
        async with session.get(MONITORING_URL,
                               headers={"Accept": "text/event-stream"}) as response:
            response.raise_for_status()
            _MONITORING_CONNECTED.set()
            try:
                async for text in _sse_events(response.content):
                    if FARM_LOG_MONITORING and _monitoring_logged < MONITORING_LOG_EVENTS:
                        _monitoring_logged += 1
                        log.info("farm-monitoring event %d/%d (%d chars): %s",
                                 _monitoring_logged, MONITORING_LOG_EVENTS, len(text),
                                 text[:MONITORING_LOG_CHARS])
                    try:
                        msg = json.loads(text)
                    except json.JSONDecodeError:
                        log.debug("farm-monitoring: non-JSON event skipped")
                        continue
                    try:
                        handle_monitoring_event(client, msg)
                    except Exception:
                        log.exception("farm-monitoring: event handler failed")
            finally:
                _MONITORING_CONNECTED.clear()
                with _FH_REPLIES:
                    _monitoring_drops += 1
                    _FH_REPLIES.notify_all()


async def run_farm_monitoring(client):
    """Same reconnect loop as /farm-data, beside it."""
    if FARM_LOG_MONITORING:
        log.info("FARM_LOG_MONITORING on: logging the first %d events from %s",
                 MONITORING_LOG_EVENTS, MONITORING_URL)
    while True:
        try:
            await stream_farm_monitoring(client)
            log.debug("farm-monitoring stream ended; reconnecting")
        except asyncio.TimeoutError:
            # Nothing for SSE_READ_TIMEOUT_SECONDS: normal while nobody asks.
            log.debug("farm-monitoring stream idle; reconnecting")
        except aiohttp.ClientError as e:
            log.warning(f"farm-monitoring stream dropped ({e}); reconnecting in "
                        f"{RECONNECT_DELAY_SECONDS}s")
        except Exception as e:
            log.error(f"farm-monitoring: unexpected error: {e}; reconnecting in "
                      f"{RECONNECT_DELAY_SECONDS}s")
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


# ---------------------------------------------------------------------------
# 24-HOUR SETTINGS SWITCHES
#
# farmhand's 24-hour flags keep the nursery lights, or the cultivation
# watering, going through the recipe's night. Written with the command
# farmhand Local sends when it saves a recipe card - a PARTIAL update, only the
# keys sent change (before/after dump, 2026-10-15):
#     {"command": "bulk_update_settings", "settings": {"<key>": 0 or 1, ...}}
#
# Not gated on Task Mode: recipe settings for normal operation. A switch shows
# only what the Hub reports in get_farm_info - the farmhand cloud app can
# change these too until the subscription ends.
# ---------------------------------------------------------------------------

# THE ONLY settings the bridge may ever write. farmhand_command() refuses a
# bulk_update_settings carrying any other key, or any value but 0 or 1.
FH24_WRITABLE_KEYS = (
    "nursery_top_red_24hr_enable", "nursery_top_blue_24hr_enable",
    "nursery_bottom_red_24hr_enable", "nursery_bottom_blue_24hr_enable",
    "cultivation_left_water_24hr_enable", "cultivation_right_water_24hr_enable",
)

FH24_GROUPS = {
    "nursery_top_24h":    ("nursery_top_red_24hr_enable",
                           "nursery_top_blue_24hr_enable"),
    "nursery_bottom_24h": ("nursery_bottom_red_24hr_enable",
                           "nursery_bottom_blue_24hr_enable"),
    "cultivation_water_24h": ("cultivation_left_water_24hr_enable",
                              "cultivation_right_water_24hr_enable"),
}

# group -> (switch name, icon, the pair that can disagree)
FH24_SWITCHES = {
    "nursery_top_24h": ("Nursery Top 24 h", "mdi:lightbulb-on", "red/blue"),
    "nursery_bottom_24h": ("Nursery Bottom 24 h", "mdi:lightbulb-on", "red/blue"),
    "cultivation_water_24h": ("Cultivation Watering 24 h", "mdi:water-sync", "left/right"),
}

if (not {k for keys in FH24_GROUPS.values() for k in keys} <= set(FH24_WRITABLE_KEYS)
        or set(FH24_SWITCHES) != set(FH24_GROUPS)):
    raise RuntimeError("Every FH24_GROUPS key must be in FH24_WRITABLE_KEYS, and every "
                       "group needs an FH24_SWITCHES entry")

FH24_VERIFY_TIMEOUT = 10
_fh24_last = {}    # group -> last state seen, so MIXED is reported once, not every poll


def _check_settings_write(settings):
    """Raise unless `settings` holds only allow-listed keys, each 0 or 1."""
    if not (isinstance(settings, dict) and settings
            and all(k in FH24_WRITABLE_KEYS for k in settings)
            and all(type(v) is int and v in (0, 1) for v in settings.values())):
        log.error("REFUSED bulk_update_settings %.200r - only the 24-hour flags "
                  "may be written, as 0 or 1", settings)
        raise ValueError("settings write refused - not on the 24-hour allow-list")


def publish_fh24_states(client, settings: dict):
    for group, keys in FH24_GROUPS.items():
        name, _icon, pair = FH24_SWITCHES[group]
        values = [settings.get(k) for k in keys]
        if any(v not in (0, 1) for v in values):
            if _fh24_last.get(group) != "unknown":
                log.warning("%s: farmhand reports %s - state not published",
                            name, dict(zip(keys, values)))
            _fh24_last[group] = "unknown"
            continue
        state = ("ON" if all(v == 1 for v in values)
                 else "OFF" if all(v == 0 for v in values) else "MIXED")
        client.publish(f"{MQTT_BASE_TOPIC}/{group}/state",
                       "ON" if state == "ON" else "OFF", qos=1, retain=True)
        if state == "MIXED" and _fh24_last.get(group) != "MIXED":
            _control_status(client, f"{name}: MIXED - {pair} differ, check farmhand")
        _fh24_last[group] = state


def handle_fh24_command(client, group: str, want_on: bool):
    """Write one 24-hour group, then VERIFY it from a fresh get_farm_info."""
    name = FH24_SWITCHES[group][0]
    keys = FH24_GROUPS[group]
    value = 1 if want_on else 0
    label = f"{name} {'ON' if want_on else 'OFF'}"

    # A write that cannot be read back is not sent.
    if not _MONITORING_CONNECTED.wait(RECONNECT_DELAY_SECONDS + 5):
        _control_status(client, f"{name}: REFUSED - not connected to farmhand's reply stream")
        return

    _control_status(client, f"{label}: sending")
    started = time.monotonic()
    try:
        reply = farmhand_command({"command": "bulk_update_settings",
                                  "settings": {k: value for k in keys}})
        log.info("farmhand replied: %s", reply)
    except Exception as e:
        _control_status(client, f"{label}: FAILED - {type(e).__name__}: {e}")
        return

    _control_status(client, f"{label}: sent, verifying")
    deadline = time.monotonic() + FH24_VERIFY_TIMEOUT
    reported = None
    while time.monotonic() < deadline:
        settings = request_farm_info(min(FH_REPLY_ATTEMPT_SECONDS, deadline - time.monotonic()))
        if settings is not None:
            reported = {k: settings.get(k) for k in keys}
            if all(v == value for v in reported.values()):
                _control_status(client, f"{label}: CONFIRMED in {time.monotonic() - started:.1f}s")
                return
        # Not applied yet, or a reply to an older request: ask again.
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    if reported is None:
        _control_status(client, f"{label}: NOT CONFIRMED - no reply from farmhand "
                                f"in {FH24_VERIFY_TIMEOUT}s")
    else:
        _control_status(client, f"{label}: NOT CONFIRMED - farm reports "
                        + ", ".join(f"{k}={v}" for k, v in reported.items()))


def publish_farmhand_discovery(client, availability_topic: str):
    for group, (name, icon, _pair) in FH24_SWITCHES.items():
        client.publish(
            f"{DISCOVERY_PREFIX}/switch/{group}/config",
            json.dumps({
                "name": name,
                "unique_id": f"greenery_s_{group}",
                "command_topic": f"{MQTT_BASE_TOPIC}/{group}/set",
                "state_topic": f"{MQTT_BASE_TOPIC}/{group}/state",
                "payload_on": "ON",
                "payload_off": "OFF",
                "optimistic": False,
                "retain": False,
                "availability_topic": availability_topic,
                "icon": icon,
                "device": DEVICE_INFO,
            }),
            qos=1, retain=True,
        )
        log.info(f"Published discovery config for {name}")

    client.publish(
        f"{DISCOVERY_PREFIX}/sensor/farmhand_version/config",
        json.dumps({
            "name": "farmhand Version",
            "unique_id": "greenery_s_farmhand_version",
            "state_topic": FARMHAND_VERSION_TOPIC,
            "availability_topic": availability_topic,
            "icon": "mdi:information-outline",
            "entity_category": "diagnostic",
            "device": DEVICE_INFO,
        }),
        qos=1, retain=True,
    )
    log.info("Published discovery config for farmhand Version")


def log_farm_identity():
    """Say up front which farm this is and what its entity IDs will be.

    The repo's automations and dashboards reference entity IDs literally, so
    whoever installs a new farm needs this prefix to render them - see
    tools/render-farm-yaml.py.
    """
    if FARM_NAME_REQUESTED and FARM_NAME_REQUESTED != FARM_NAME:
        log.warning("Farm name %r has no letters or digits; using %r instead",
                    FARM_NAME_REQUESTED, FARM_NAME)
    log.info("Farm name: %s", FARM_NAME)
    log.info("New entities get IDs like sensor.%s_cultivation_ph "
             "(entities already in HA keep the IDs they have)", FARM_SLUG)
    if FARM_NAME != LEGACY_FARM_NAME:
        log.info('Render the HA automations/dashboards for this farm with: '
                 'python tools/render-farm-yaml.py "%s"', FARM_NAME)


async def run_forever():
    log_farm_identity()
    mqtt_client = None

    # Retry MQTT setup itself - a transient network blip or the broker not
    # being up yet (e.g. on boot) should not crash the whole service.
    while mqtt_client is None:
        try:
            mqtt_client = build_mqtt_client()
            publish_discovery(mqtt_client)
        except Exception as e:
            log.error(f"MQTT setup failed ({e}); retrying in {RECONNECT_DELAY_SECONDS}s")
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    # Held in a variable: asyncio keeps only a weak reference to a task.
    background_tasks = [asyncio.create_task(run_farm_monitoring(mqtt_client)),
                        asyncio.create_task(poll_farmhand(mqtt_client)),
                        asyncio.create_task(watch_board_data(mqtt_client))]

    while True:
        try:
            await stream_farm_data(mqtt_client)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning(f"SSE stream dropped ({e}); reconnecting in {RECONNECT_DELAY_SECONDS}s")
        except Exception as e:
            log.error(f"Unexpected error: {e}; reconnecting in {RECONNECT_DELAY_SECONDS}s")
        # The last frame stays valid through the reconnect; watch_board_data
        # greys the switches out if no new one arrives in BOARD_FRESH_SECONDS.
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        log.info("Shutting down.")
