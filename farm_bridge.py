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
    E831CDC7E680  -> Dosing tank level sensors, digital_1..4 (see DOSING TANK LEVELS)

Requires: pip install aiohttp paho-mqtt --break-system-packages
"""

import asyncio
import json
import logging
import math
import os
import re
import secrets
import socket
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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

# --- BEGIN dosing tank levels ---
# ---------------------------------------------------------------------------
# DOSING TANK LEVELS
#
# Each of the four 1-gallon dosing tanks has an optical level sensor that
# trips when the tank is almost empty: four digital inputs on module
# E831CDC7E680. farmhand shows them on its Environmental card as "ok" or low.
#
# Owner tests, 2026-10-29 (tools/farmhand/dosing_levels.py):
#   - 0 = ok. Low is ASSUMED to be 1 (dosing_level_low_value) - no real low
#     tank had been seen yet; the first one confirms it.
#   - Corroded connectors make an input flicker, so a single raw 1 never
#     means low: a full rolling window decides, and frequent flicker raises
#     Sensor Fault instead.
#   - A dead or unplugged sensor reads a steady 0 - ok, exactly like a full
#     tank, in farmhand too. Only the silent-sensor backstop catches it.
#
# Every state publishes on change only, retained, so the frame rate never
# reaches the recorder. Unique IDs and topics follow the tank's SLOT (1-4),
# never its configurable name, so a renamed tank keeps its entities.
#
# Alerts are decided here, with their repeat timers kept in a retained memory
# topic per tank so a restart neither repeats nor forgets one. Each goes to
# DOSING_ALERT_TOPIC; automation 23 hands it to script.farm_alerts.
# ---------------------------------------------------------------------------

DOSING_DEVICE_ID = "E831CDC7E680"
DOSING_DEFAULT_NAMES = ("Nutrient A", "Nutrient B", "pH Down", "Boost / pH Up")
DOSING_DEFAULT_INPUTS = ("digital_1", "digital_2", "digital_3", "digital_4")

DOSING_LOW_ON_PERCENT = 80        # this much of a full window low -> Low
DOSING_LOW_OFF_PERCENT = 20       # this much or less -> ok; in between holds
DOSING_PERCENT_STEP = 10          # low_percent republished on a move this big
DOSING_FAULT_WINDOW_SECONDS = 3600
DOSING_FAULT_CLEAR_CHANGES = 5    # an hour this quiet clears Sensor Fault
DOSING_STALE_SECONDS = 60         # no reading this long -> unavailable
DOSING_CHECK_SECONDS = 2          # how often watch_dosing_tanks looks
DOSING_DAYS_CHECK_SECONDS = 3600  # Days Since Refill recomputed this often
# After a restart, wait this long after subscribing for the broker's retained
# memory before deciding there was none.
DOSING_RESTORE_WAIT_SECONDS = 3
DOSING_LOW_REPEAT_SECONDS = 12 * 3600
DOSING_FAULT_REPEAT_SECONDS = 24 * 3600
DOSING_BACKSTOP_REPEAT_SECONDS = 7 * 86400

# Never retained: an alert must not replay when Home Assistant reconnects.
DOSING_ALERT_TOPIC = f"{MQTT_BASE_TOPIC}/dosing_tanks/alert"
_DOSING_MEMORY_TIMES = ("last_refill", "last_low", "low_alert", "fault_alert",
                        "backstop_alert")

# Logged at start - logging is not set up yet while this module loads.
_DOSING_CONFIG_PROBLEMS = []


def _dosing_topic(slot: int, what: str) -> str:
    return f"{MQTT_BASE_TOPIC}/dosing_tank_{slot}_{what}"


def _dosing_available_topic(slot: int) -> str:
    return f"{MQTT_BASE_TOPIC}/dosing_tank_{slot}/available"


def _dosing_memory_topic(slot: int) -> str:
    return f"{MQTT_BASE_TOPIC}/dosing_tank_{slot}/memory"


def _dosing_env_list(var, defaults, parse):
    """A per-tank option: a JSON list (run.sh sends one) or comma-separated.
    An entry that is missing or unusable falls back to that tank's default."""
    raw = (os.environ.get(var) or "").strip()
    if raw in ("", "null"):
        return list(defaults)
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        items = [s.strip() for s in raw.split(",")]
    if not isinstance(items, list):
        items = [items]
    if len(items) != len(defaults):
        _DOSING_CONFIG_PROBLEMS.append(
            f"{var} has {len(items)} entries, expected {len(defaults)} - "
            f"a missing one takes its default")
    values = []
    for i, default in enumerate(defaults):
        try:
            values.append(parse(items[i]) if i < len(items) else default)
        except (TypeError, ValueError):
            _DOSING_CONFIG_PROBLEMS.append(
                f"{var} entry {i + 1} ({items[i]!r}) is not usable - using {default!r}")
            values.append(default)
    return values


def _dosing_env_int(var, default, lo, hi):
    raw = (os.environ.get(var) or "").strip()
    if raw in ("", "null"):
        return default
    try:
        value = int(raw)
        if lo <= value <= hi:
            return value
    except ValueError:
        pass
    _DOSING_CONFIG_PROBLEMS.append(f"{var}={raw!r} is not {lo}-{hi} - using {default}")
    return default


def _tank_name(value) -> str:
    name = str(value).strip()
    if not farm_slug(name):       # HA could build no entity ID from it
        raise ValueError(name)
    return name


def _tank_input(value) -> str:
    key = str(value).strip()
    if not re.fullmatch(r"digital_[0-9]+", key):
        raise ValueError(key)
    return key


def _tank_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise ValueError(value)


def _local_time(ts):
    return None if ts is None else time.strftime("%Y-%m-%d %H:%M %z", time.localtime(ts))


def _due(last, wall: float, every: float) -> bool:
    return last is None or wall - last >= every


@dataclass
class _DosingTank:
    slot: int                     # 1-4: decides unique_ids and topics
    name: str
    input_key: str
    samples: deque = field(default_factory=deque)   # (monotonic, low) in the window
    lows: int = 0                 # low samples in `samples`
    sampling_from: float = None   # first reading since start or since unavailable
    seen_at: float = None         # monotonic time of the latest reading
    last_raw: int = None
    changes: deque = field(default_factory=deque)   # monotonic times of raw changes
    low: bool = False             # debounced; starts ok
    fault: bool = False
    fault_since: float = None
    available: bool = None        # last availability published, this run
    pub_low: bool = None          # last value published, this run - None: not yet
    pub_fault: bool = None
    pub_percent: int = None
    pub_days: int = None
    pub_days_attrs: dict = None
    days_checked_at: float = None
    memory: dict = None           # wall-clock times, kept in the retained memory topic


class DosingMonitor:
    """Debounce, Sensor Fault, availability, backstop and alerts for the tanks.

    Frames and tick() run on the asyncio thread; Refilled presses and the
    retained memory arrive on paho's thread, so one lock guards it all.
    `now` is time.monotonic() and `wall` time.time(); tests pass their own.
    """

    def __init__(self, names, inputs, enabled, low_value=1, window_s=600,
                 fault_changes=20, silent_days=21, farm_name=FARM_NAME):
        self.slots = range(1, len(names) + 1)
        self.tanks = [_DosingTank(slot, name, key) for slot, (name, key, on)
                      in enumerate(zip(names, inputs, enabled), 1) if on]
        self.low_value = low_value
        self.window_s = window_s
        self.fault_changes = fault_changes
        self.silent_days = silent_days
        self.farm_name = farm_name
        self._lock = threading.Lock()
        self._started = None
        self._read_until = None
        self._memory_seen = set()

    def _tank(self, slot):
        return next((t for t in self.tanks if t.slot == slot), None)

    def log_config(self):
        for problem in _DOSING_CONFIG_PROBLEMS:
            log.warning("Dosing tank option: %s", problem)
        keys = [t.input_key for t in self.tanks]
        for key in sorted({k for k in keys if keys.count(k) > 1}):
            log.warning("Dosing tank option: %s is read by more than one tank", key)
        if not self.tanks:
            log.info("Dosing tank levels: every tank disabled")
            return
        log.info("Dosing tanks: %s. Low = %d, window %d s, Sensor Fault above %d "
                 "changes/h, backstop after %d days",
                 ", ".join(f"{t.name} <- {t.input_key}" for t in self.tanks),
                 self.low_value, self.window_s, self.fault_changes, self.silent_days)

    # --- MQTT wiring ---------------------------------------------------------

    def refilled_topics(self) -> dict:
        return {f"{_dosing_topic(t.slot, 'refilled')}/set": t.slot for t in self.tanks}

    def memory_topics(self) -> dict:
        return {_dosing_memory_topic(t.slot): t.slot for t in self.tanks}

    def publish_discovery(self, client, availability_topic: str):
        """An empty config for a disabled tank removes its entities from HA."""
        for slot in self.slots:
            t = self._tank(slot)
            for component, what, config in self._discovery(slot, t, availability_topic):
                client.publish(f"{DISCOVERY_PREFIX}/{component}/dosing_tank_{slot}_{what}/config",
                               json.dumps(config) if t else "", qos=1, retain=True)
            if t:
                log.info("Published discovery config for %s tank (%s)", t.name, t.input_key)

    def _discovery(self, slot, t, availability_topic):
        name = t.name if t else ""
        level = _dosing_topic(slot, "level")
        fault = _dosing_topic(slot, "sensor_fault")
        days = _dosing_topic(slot, "days_since_refill")
        # Level and Fault describe the sensor: unavailable without its data.
        # Days Since Refill and Refilled are the bridge's own clock.
        sensor_available = {
            "availability": [{"topic": availability_topic},
                             {"topic": _dosing_available_topic(slot)}],
            "availability_mode": "all",
        }
        return [
            ("binary_sensor", "level", {
                "name": f"{name} Level",
                "unique_id": f"greenery_s_dosing_tank_{slot}_level",
                "state_topic": f"{level}/state",
                "json_attributes_topic": f"{level}/attributes",
                "payload_on": "ON", "payload_off": "OFF",
                "device_class": "problem", "icon": "mdi:cup-water",
                **sensor_available, "device": DEVICE_INFO}),
            ("binary_sensor", "sensor_fault", {
                "name": f"{name} Sensor Fault",
                "unique_id": f"greenery_s_dosing_tank_{slot}_sensor_fault",
                "state_topic": f"{fault}/state",
                "payload_on": "ON", "payload_off": "OFF",
                "device_class": "problem", "entity_category": "diagnostic",
                **sensor_available, "device": DEVICE_INFO}),
            ("sensor", "days_since_refill", {
                "name": f"{name} Days Since Refill",
                "unique_id": f"greenery_s_dosing_tank_{slot}_days_since_refill",
                "state_topic": f"{days}/state",
                "json_attributes_topic": f"{days}/attributes",
                "unit_of_measurement": "d", "device_class": "duration",
                "icon": "mdi:calendar-clock",
                "availability_topic": availability_topic, "device": DEVICE_INFO}),
            ("button", "refilled", {
                "name": f"{name} Refilled",
                "unique_id": f"greenery_s_dosing_tank_{slot}_refilled",
                "command_topic": f"{_dosing_topic(slot, 'refilled')}/set",
                "payload_press": "PRESS", "icon": "mdi:water-plus",
                "availability_topic": availability_topic, "device": DEVICE_INFO}),
        ]

    def subscribe(self, client, now=None):
        """On every MQTT (re)connect: the Refilled buttons, and each tank's
        memory as the broker kept it from before a restart."""
        for topic in [*self.refilled_topics(), *self.memory_topics()]:
            client.subscribe(topic, qos=1)
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._read_until is None:
                self._read_until = now + DOSING_RESTORE_WAIT_SECONDS

    def memory_retained(self, slot: int, payload):
        """The broker's retained memory for one tank. Only the first counts:
        later deliveries are the bridge's own."""
        try:
            stored = json.loads(payload)
            mem = {k: stored.get(k) for k in _DOSING_MEMORY_TIMES}
            if not all(v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
                       for v in mem.values()) or mem["last_refill"] is None:
                raise ValueError("bad times")
            mem["refill_how"] = str(stored.get("refill_how") or "unknown")[:60]
        except (ValueError, TypeError, AttributeError):
            log.warning("Dosing tank %d: retained memory unreadable - its clocks restart", slot)
            return
        with self._lock:
            t = self._tank(slot)
            if slot in self._memory_seen or t is None:
                return
            self._memory_seen.add(slot)
            t.memory = mem

    def _memory(self, t, wall: float) -> dict:
        if t.memory is None:
            # First start, or no memory on the broker: the clocks start now.
            t.memory = {k: None for k in _DOSING_MEMORY_TIMES}
            t.memory.update(last_refill=wall, refill_how="first start")
            self._memory_seen.add(t.slot)
        return t.memory

    def _save(self, client, t):
        client.publish(_dosing_memory_topic(t.slot), json.dumps(t.memory), qos=1, retain=True)

    # --- Readings --------------------------------------------------------------

    def on_frame(self, client, payload, now=None, wall=None):
        """Every /farm-data frame: one reading per enabled tank."""
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        try:
            dev = payload["state"][DOSING_DEVICE_ID]
            inputs = dev["state"] if dev.get("connected") is not False else {}
        except (KeyError, TypeError, AttributeError):
            inputs = {}
        with self._lock:
            if self._started is None:
                self._started = now
            for t in self.tanks:
                raw = inputs.get(t.input_key) if isinstance(inputs, dict) else None
                # Missing is not a reading: tick() greys the tank out once
                # none has arrived for DOSING_STALE_SECONDS.
                if isinstance(raw, (int, float)) and math.isfinite(raw):
                    self._reading(client, t, int(round(raw)), now, wall)

    def _reading(self, client, t, raw: int, now: float, wall: float):
        if t.seen_at is None or now - t.seen_at > DOSING_STALE_SECONDS:
            # First reading since start, or back from unavailable: a new window.
            # A routine reconnect gap is far shorter and keeps the window.
            t.samples.clear()
            t.lows, t.sampling_from, t.last_raw = 0, now, None
        t.seen_at = now
        if t.available is not True:
            if t.available is False:
                log.info("%s tank level: readings back", t.name)
            t.available = True
            client.publish(_dosing_available_topic(t.slot), "online", qos=1, retain=True)

        low = raw == self.low_value
        if t.last_raw is not None and raw != t.last_raw:
            t.changes.append(now)
        t.last_raw = raw
        t.samples.append((now, low))
        t.lows += low
        while t.samples[0][0] <= now - self.window_s:
            t.lows -= t.samples.popleft()[1]

        self._update_fault(client, t, now)
        # Never decided from a partial window.
        if now - t.sampling_from >= self.window_s:
            self._update_level(client, t, round(100 * t.lows / len(t.samples)), wall)

    def _update_fault(self, client, t, now: float):
        while t.changes and t.changes[0] <= now - DOSING_FAULT_WINDOW_SECONDS:
            t.changes.popleft()
        n = len(t.changes)
        observed = now - t.sampling_from >= DOSING_FAULT_WINDOW_SECONDS
        if not t.fault and n > self.fault_changes:
            t.fault, t.fault_since = True, now
            log.warning("%s tank sensor unstable: %d changes in the last hour - "
                        "Sensor Fault on, level held", t.name, n)
        elif (t.fault and n <= DOSING_FAULT_CLEAR_CHANGES and observed
              and now - t.fault_since >= DOSING_FAULT_WINDOW_SECONDS):
            t.fault = False
            log.info("%s tank sensor steady again (%d changes in the last hour) - "
                     "Sensor Fault off", t.name, n)
        # Off is only known after a full hour of readings; on, at once.
        if (t.fault or observed) and t.pub_fault != t.fault:
            t.pub_fault = t.fault
            client.publish(f"{_dosing_topic(t.slot, 'sensor_fault')}/state",
                           "ON" if t.fault else "OFF", qos=1, retain=True)

    def _update_level(self, client, t, percent: int, wall: float):
        level = _dosing_topic(t.slot, "level")
        if t.pub_percent is None or abs(percent - t.pub_percent) >= DOSING_PERCENT_STEP:
            t.pub_percent = percent
            client.publish(f"{level}/attributes", json.dumps({"low_percent": percent}),
                           qos=1, retain=True)
        if t.fault:
            return        # a flickering sensor proves nothing: hold, no low alert

        low = (True if percent >= DOSING_LOW_ON_PERCENT
               else False if percent <= DOSING_LOW_OFF_PERCENT else t.low)
        changed = False
        if low != t.low:
            t.low = low
            mem = self._memory(t, wall)
            if low:
                mem["last_low"] = wall
                log.warning("%s tank LOW (%d%% of the last %d s)", t.name, percent, self.window_s)
            else:
                mem["last_refill"], mem["refill_how"] = wall, "sensor: low, then ok"
                log.info("%s tank ok again - refilled (%d%% low)", t.name, percent)
            changed = True
        if percent <= DOSING_LOW_OFF_PERCENT and t.memory and t.memory["low_alert"] is not None:
            t.memory["low_alert"] = None     # decisively ok: the next Low alerts at once
            changed = True
        if changed:
            self._save(client, t)
            self._publish_days(client, t, wall)
        if t.pub_low != t.low:
            t.pub_low = t.low
            client.publish(f"{level}/state", "ON" if t.low else "OFF", qos=1, retain=True)

    # --- Refilled, Days Since Refill, alerts -----------------------------------

    def refilled(self, client, slot: int, wall=None):
        """A Refilled press restarts the tank's refill and backstop clocks."""
        wall = time.time() if wall is None else wall
        with self._lock:
            t = self._tank(slot)
            if t is None:
                return
            mem = self._memory(t, wall)
            mem["last_refill"], mem["refill_how"] = wall, "Refilled button"
            self._save(client, t)
            self._publish_days(client, t, wall)
        log.info("%s tank: Refilled pressed - Days Since Refill reset", t.name)

    def _publish_days(self, client, t, wall: float):
        mem = t.memory
        days = int(max(0.0, wall - mem["last_refill"]) // 86400)
        attrs = {"last_refill": _local_time(mem["last_refill"]),
                 "refilled_by": mem["refill_how"],
                 "last_reported_low": _local_time(mem["last_low"])}
        base = _dosing_topic(t.slot, "days_since_refill")
        if attrs != t.pub_days_attrs:
            t.pub_days_attrs = attrs
            client.publish(f"{base}/attributes", json.dumps(attrs), qos=1, retain=True)
        if days != t.pub_days:
            t.pub_days = days
            client.publish(f"{base}/state", days, qos=1, retain=True)

    def tick(self, client, now=None, wall=None):
        """Every DOSING_CHECK_SECONDS, frames or not: availability, memory,
        alert repeats and Days Since Refill."""
        now = time.monotonic() if now is None else now
        wall = time.time() if wall is None else wall
        with self._lock:
            if self._started is None:
                self._started = now
            restored = self._read_until is not None and now >= self._read_until
            for t in self.tanks:
                last = t.seen_at if t.seen_at is not None else self._started
                if t.available is not False and now - last > DOSING_STALE_SECONDS:
                    t.available = False
                    log.warning("%s tank level: no %s reading from module %s for %ss - "
                                "unavailable", t.name, t.input_key, DOSING_DEVICE_ID,
                                DOSING_STALE_SECONDS)
                    client.publish(_dosing_available_topic(t.slot), "offline",
                                   qos=1, retain=True)
                if not restored:
                    continue      # still waiting to learn this tank's clocks
                if t.memory is None:
                    self._memory(t, wall)
                    self._save(client, t)
                if t.days_checked_at is None or now - t.days_checked_at >= DOSING_DAYS_CHECK_SECONDS:
                    t.days_checked_at = now
                    self._publish_days(client, t, wall)
                if t.available:       # no alert for unavailable
                    self._alerts(client, t, wall)

    def _alerts(self, client, t, wall: float):
        mem = t.memory
        sent = False
        if t.low and not t.fault and _due(mem["low_alert"], wall, DOSING_LOW_REPEAT_SECONDS):
            self._alert(client, t, "low", "⚠️ Dosing Tank Low",
                        f"{t.name} tank low. Refill it.", "normal")
            mem["low_alert"], sent = wall, True
        if t.fault and _due(mem["fault_alert"], wall, DOSING_FAULT_REPEAT_SECONDS):
            self._alert(client, t, "sensor_fault", "⚠️ Dosing Tank Sensor Unstable",
                        f"{t.name} tank sensor unstable. Check its connector.", "normal")
            mem["fault_alert"], sent = wall, True
        # A dead sensor reads ok forever. Low, a detected refill or a Refilled
        # press each prove the sensor and restart this clock.
        last_event = max(mem["last_refill"], mem["last_low"] or 0)
        silent = wall - last_event
        backstop = mem["backstop_alert"]
        if (not t.low and not t.fault and silent >= self.silent_days * 86400
                and (backstop is None or backstop < last_event
                     or wall - backstop >= DOSING_BACKSTOP_REPEAT_SECONDS)):
            self._alert(client, t, "silent", "ℹ️ Dosing Tank Sensor Silent",
                        f"{t.name} hasn't reported low in {int(silent // 86400)} days. "
                        f"Check the sensor and its connector, and press {t.name} "
                        f"Refilled after each refill.", "low")
            mem["backstop_alert"], sent = wall, True
        if sent:
            self._save(client, t)

    def _alert(self, client, t, kind: str, title: str, text: str, priority: str):
        # Sent once. While the broker is unreachable paho queues a QoS 1
        # message and delivers it on reconnect, so a retry here would page twice.
        message = f"{self.farm_name}: {text}"
        client.publish(DOSING_ALERT_TOPIC, json.dumps({
            "title": title, "message": message, "priority": priority,
            "notification_id": f"farm_dosing_tank_{t.slot}_{kind}",
            "tank": t.name, "kind": kind,
        }), qos=1, retain=False)
        log.info("Dosing alert: %s", message)


DOSING = DosingMonitor(
    names=_dosing_env_list("FARM_DOSING_TANK_NAMES", DOSING_DEFAULT_NAMES, _tank_name),
    inputs=_dosing_env_list("FARM_DOSING_TANK_INPUTS", DOSING_DEFAULT_INPUTS, _tank_input),
    enabled=_dosing_env_list("FARM_DOSING_TANKS_ENABLED", (True,) * 4, _tank_enabled),
    low_value=_dosing_env_int("FARM_DOSING_LEVEL_LOW_VALUE", 1, 0, 1),
    window_s=_dosing_env_int("FARM_DOSING_LEVEL_WINDOW_S", 600, 60, 3600),
    fault_changes=_dosing_env_int("FARM_DOSING_SENSOR_FAULT_CHANGES_PER_HOUR", 20, 1, 3600),
    silent_days=_dosing_env_int("FARM_DOSING_SILENT_DAYS", 21, 1, 365),
)


async def watch_dosing_tanks(client):
    """The time-driven half: runs with or without frames. Same rule as
    watch_board_data - a routine /farm-data reconnect (every ~60 s, a few
    seconds' gap) changes nothing; DOSING_STALE_SECONDS without a reading does."""
    while True:
        await asyncio.sleep(DOSING_CHECK_SECONDS)
        try:
            DOSING.tick(client)
        except Exception:
            log.exception("Dosing tank check failed")
# --- END dosing tank levels ---


# ---------------------------------------------------------------------------
# DUPLICATE BRIDGE DETECTION
#
# Two bridges on one broker filled a farm's database at ~10 million rows a
# day. Both connected as client ID "farm-bridge"; the broker allows one
# connection per ID, so each kicked the other off about once a second, and
# farm/bridge/status - every entity's availability - flapped with it.
#
# A client ID new to every start means two bridges can no longer kick each
# other off. Any one of three signals then turns Duplicate Bridge Detected on:
#   instance         another bridge's announcement on farm/bridge/instance
#   foreign_offline  farm/bridge/status = offline while this bridge is
#                    connected: another client's Last Will - a bridge stopped
#   disconnects      over 5 unexpected disconnects in 60 s
# It clears after 10 minutes with none of them and at most 1 disconnect.
#
# Both bridges write the one sensor. When one of them is the Home Assistant
# app, only the app does, so it names the computer that is not Home Assistant.
# ---------------------------------------------------------------------------

BRIDGE_STATUS_TOPIC = f"{MQTT_BASE_TOPIC}/bridge/status"
BRIDGE_INSTANCE_TOPIC = f"{MQTT_BASE_TOPIC}/bridge/instance"
DUPLICATE_BRIDGE_TOPIC = f"{MQTT_BASE_TOPIC}/duplicate_bridge/state"
DUPLICATE_BRIDGE_ATTRIBUTES_TOPIC = f"{MQTT_BASE_TOPIC}/duplicate_bridge/attributes"
DUPLICATE_CLEAR_SECONDS = 600       # this quiet clears it; also the log repeat
DUPLICATE_REPUBLISH_SECONDS = 60    # our announcement re-sent at most this often
DUPLICATE_DISCONNECTS = 5           # more than this many unexpected disconnects...
DUPLICATE_DISCONNECT_WINDOW = 60    # ...in this many seconds
DUPLICATE_CHECK_SECONDS = 5         # how often watch_duplicate_bridge looks

# Supervisor gives every app this token: set, this bridge is the app.
RUNNING_AS_APP = bool(os.environ.get("SUPERVISOR_TOKEN"))


def make_client_id() -> str:
    """This start's MQTT client ID. Never a fixed one: the broker drops a
    connection when another client connects with its ID."""
    return f"greenery-bridge-{farm_slug(FARM_NAME)}-{secrets.token_hex(3)}"


def bridge_version() -> str:
    """The app's version, set in its image; run from a checkout, the
    checkout's greenery-bridge/config.yaml."""
    version = (os.environ.get("FARM_BRIDGE_VERSION") or "").strip()
    if version:
        return version
    cfg = Path(__file__).resolve().parent / "greenery-bridge" / "config.yaml"
    try:
        m = re.search(r'^version:\s*"?([\w.-]+)', cfg.read_text(encoding="utf-8"), re.M)
    except OSError:
        m = None
    return m.group(1) if m else "unknown"


class DuplicateBridgeMonitor:
    """Duplicate Bridge Detected, from the three signals above.

    Messages and disconnects arrive on paho's thread and tick() on the asyncio
    thread, so one lock guards it all. `now` is time.monotonic(); tests pass
    their own.
    """

    def __init__(self, client_id, host, version, started, app=False):
        self.client_id = client_id
        self.app = app
        self.instance = json.dumps({"id": client_id, "host": host, "version": version,
                                    "started": started, "app": app})
        self._lock = threading.Lock()
        self.on = False
        self.reason = None
        self.other = {}               # other_id/_host/_version from its announcement
        self.other_app = False        # ...and whether it is the app
        self.signal_at = None         # the latest signal
        self.disconnects = deque()    # unexpected ones, last DUPLICATE_CLEAR_SECONDS
        self.established = False      # a disconnect counts only after a CONNACK 0
        self.republish_due = False
        self.republished_at = None
        self.logged_at = {}           # reason -> last ERROR line
        self.pub_attributes = None    # last sent, this connection
        self.pub_state = None

    # --- paho's thread ---------------------------------------------------------

    def connected(self, client):
        """On every connect, BEFORE subscribing: the broker then replays this
        bridge's own announcement, never the one an earlier start left. The
        sensor is restated: a publish while disconnected may not have gone."""
        client.publish(BRIDGE_INSTANCE_TOPIC, self.instance, qos=1, retain=True)
        with self._lock:
            self.established = True
            self.pub_attributes = self.pub_state = None
            self._publish(client)
        client.subscribe(BRIDGE_INSTANCE_TOPIC, qos=1)
        client.subscribe(BRIDGE_STATUS_TOPIC, qos=1)

    def instance_message(self, client, payload, now=None):
        try:
            msg = json.loads(payload)
            other_id = str(msg["id"])
        except (ValueError, TypeError, KeyError):
            return
        if other_id == self.client_id:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            self.other = {"other_id": other_id, "other_host": msg.get("host"),
                          "other_version": msg.get("version")}
            self.other_app = msg.get("app") is True
            if self._log_due("instance", now):
                log.error("Another Greenery bridge is running on this broker: "
                          "id=%s, host=%s, version=%s",
                          other_id, msg.get("host"), msg.get("version"))
            self.republish_due = True
            self._republish(client, now)
            self._signal(client, "instance", now)

    def status_message(self, client, payload, now=None):
        """This bridge's own Last Will fires only once it is gone, so an
        offline it receives is another client's. Set straight back to online,
        or every entity stays unavailable."""
        if payload != b"offline":
            return
        client.publish(BRIDGE_STATUS_TOPIC, "online", qos=1, retain=True)
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._log_due("foreign_offline", now):
                log.error("%s went offline while this bridge is connected: another "
                          "bridge's Last Will. Set back to online", BRIDGE_STATUS_TOPIC)
            self._signal(client, "foreign_offline", now)

    def disconnected(self, client, rc, now=None):
        """A refused connect (bad password) also ends in a disconnect, with
        paho's back-off; only the loss of an established connection counts."""
        with self._lock:
            established, self.established = self.established, False
            if rc == 0 or not established:
                return
            now = time.monotonic() if now is None else now
            self.disconnects.append(now)
            self._forget_disconnects(now)
            recent = sum(1 for t in self.disconnects if now - t < DUPLICATE_DISCONNECT_WINDOW)
            if recent <= DUPLICATE_DISCONNECTS:
                return
            if self._log_due("disconnects", now):
                log.error("%d unexpected MQTT disconnects in %d s: another bridge is "
                          "taking this connection over, or the broker or network is "
                          "failing", recent, DUPLICATE_DISCONNECT_WINDOW)
            self._signal(client, "disconnects", now)

    # --- asyncio thread --------------------------------------------------------

    def tick(self, client, now=None):
        """A held-back re-announcement, and the 10-minute clear."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._republish(client, now)
            self._forget_disconnects(now)
            if (not self.on or now - self.signal_at < DUPLICATE_CLEAR_SECONDS
                    or len(self.disconnects) > 1):
                return
            self.on, self.reason, self.other, self.other_app = False, None, {}, False
            log.info("Duplicate Bridge Detected cleared: no sign of another bridge "
                     "for %d min", DUPLICATE_CLEAR_SECONDS // 60)
            self._publish(client)

    def publish_discovery(self, client):
        # No availability_topic: farm/bridge/status is what a second bridge
        # corrupts, and this sensor must show through that.
        client.publish(
            f"{DISCOVERY_PREFIX}/binary_sensor/duplicate_bridge_detected/config",
            json.dumps({
                "name": "Duplicate Bridge Detected",
                "unique_id": "greenery_s_duplicate_bridge_detected",
                "state_topic": DUPLICATE_BRIDGE_TOPIC,
                "json_attributes_topic": DUPLICATE_BRIDGE_ATTRIBUTES_TOPIC,
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "problem",
                "entity_category": "diagnostic",
                "device": DEVICE_INFO,
            }),
            qos=1, retain=True,
        )
        log.info("Published discovery config for Duplicate Bridge Detected")

    # --- under the lock --------------------------------------------------------

    def _signal(self, client, reason, now):
        self.on, self.reason, self.signal_at = True, reason, now
        # Sent even unchanged: the other bridge may have overwritten it - with
        # its own OFF as it started, for one.
        self._publish(client, force=True)

    def _republish(self, client, now):
        """Ours, answering another's - at most once per DUPLICATE_REPUBLISH_SECONDS.
        Held back, not dropped: two bridges then keep seeing each other at that
        pace, and neither clears while both run."""
        if not self.republish_due or (self.republished_at is not None and
                                      now - self.republished_at < DUPLICATE_REPUBLISH_SECONDS):
            return
        self.republish_due, self.republished_at = False, now
        client.publish(BRIDGE_INSTANCE_TOPIC, self.instance, qos=1, retain=True)

    def _forget_disconnects(self, now):
        while self.disconnects and now - self.disconnects[0] >= DUPLICATE_CLEAR_SECONDS:
            self.disconnects.popleft()

    def _log_due(self, reason, now) -> bool:
        last = self.logged_at.get(reason)
        if last is not None and now - last < DUPLICATE_CLEAR_SECONDS:
            return False
        self.logged_at[reason] = now
        return True

    def _publish(self, client, force=False):
        if self.other_app and not self.app:
            return
        attributes = {"other_id": None, "other_host": None, "other_version": None,
                      **self.other, "reason": self.reason}
        state = "ON" if self.on else "OFF"
        # Attributes first, so the alert sees the other host when it turns ON.
        if force or attributes != self.pub_attributes:
            self.pub_attributes = attributes
            client.publish(DUPLICATE_BRIDGE_ATTRIBUTES_TOPIC, json.dumps(attributes),
                           qos=1, retain=True)
        if force or state != self.pub_state:
            self.pub_state = state
            client.publish(DUPLICATE_BRIDGE_TOPIC, state, qos=1, retain=True)


MQTT_CLIENT_ID = make_client_id()   # once per start
DUPLICATE = DuplicateBridgeMonitor(MQTT_CLIENT_ID, socket.gethostname(),
                                   bridge_version(), int(time.time()), RUNNING_AS_APP)


async def watch_duplicate_bridge(client):
    while True:
        await asyncio.sleep(DUPLICATE_CHECK_SECONDS)
        try:
            DUPLICATE.tick(client)
        except Exception:
            log.exception("Duplicate bridge check failed")


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
    client = mqtt.Client(client_id=MQTT_CLIENT_ID, clean_session=True)
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
    dosing_refilled_topics = DOSING.refilled_topics()
    dosing_memory_topics = DOSING.memory_topics()

    def on_connect(c, userdata, flags, rc):
        msg = CONNACK_CODES.get(rc, f"Unknown return code {rc}")
        if rc == 0:
            log.info(f"MQTT connected: {msg} (client ID {MQTT_CLIENT_ID})")
            c.publish(availability_topic, "online", qos=1, retain=True)
            DUPLICATE.connected(c)
            for topic in [*button_handlers, *output_set_topics, *fh24_set_topics]:
                c.subscribe(topic, qos=1)
            # Read back, not acted on: whether a cool-down was running when
            # the bridge last stopped. See cool_down_on_frame.
            c.subscribe(COOL_DOWN_ACTIVE_TOPIC, qos=1)
            cool_down_subscribed()
            # Refilled buttons, and each tank's retained memory read back.
            DOSING.subscribe(c)
            # Read back, so a restart does not publish the same photos again.
            c.subscribe(f"{CAMERA_TOPIC}/+/photo_time", qos=1)
        else:
            log.error(f"MQTT connection FAILED: {msg}")

    def on_disconnect(c, userdata, rc):
        if rc != 0:
            log.warning(f"MQTT disconnected unexpectedly (rc={rc}); paho will auto-reconnect")
        DUPLICATE.disconnected(c, rc)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    def on_message(c, userdata, msg):
        topic = msg.topic
        if topic == BRIDGE_INSTANCE_TOPIC:
            DUPLICATE.instance_message(c, msg.payload)
            return
        if topic == availability_topic:
            DUPLICATE.status_message(c, msg.payload)
            return
        if topic == COOL_DOWN_ACTIVE_TOPIC:
            if msg.retain:
                cool_down_retained(msg.payload == b"ON")
            return
        if topic in dosing_memory_topics:
            if msg.retain:
                DOSING.memory_retained(dosing_memory_topics[topic], msg.payload)
            return
        if topic.startswith(f"{CAMERA_TOPIC}/") and topic.endswith("/photo_time"):
            if msg.retain:
                camera_retained(topic.split("/")[-2], msg.payload)
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
        if topic in dosing_refilled_topics:
            # Local bookkeeping only - nothing goes to the farm.
            DOSING.refilled(c, dosing_refilled_topics[topic])
        elif topic in button_handlers:
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
    DOSING.publish_discovery(client, availability_topic)
    publish_camera_discovery(client, availability_topic)
    DUPLICATE.publish_discovery(client)


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
    try:
        DOSING.on_frame(client, payload)
    except Exception:
        # Never let the tank levels stop the farm's other sensors.
        log.exception("Dosing tank levels: frame not processed")

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
# own page on every load. Those, and get_last_images_local_only for the
# cameras (below), are the only things it sends unprompted.
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
        update_camera_names(response.get("camera_aliases"))
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


async def _sse_events(content, label="farm-monitoring"):
    """Yield each SSE event's data, multi-line data: fields joined by newlines.

    Reads raw chunks rather than aiohttp's readline, whose line limit a large
    reply - or a camera photo - would hit.
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
                    log.warning("%s: event over %d bytes skipped",
                                label, MONITORING_MAX_EVENT_BYTES)
                    data, skipping = [], True
        del buf[:start]
        if len(buf) > MONITORING_MAX_EVENT_BYTES:
            log.warning("%s: event over %d bytes skipped",
                        label, MONITORING_MAX_EVENT_BYTES)
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


# ---------------------------------------------------------------------------
# CAMERAS - LATEST PHOTO ONLY
#
# Verified 2026-10-16 with tools/farmhand/dump_cameras.py (captures in
# tests/fixtures/farmhand/):
#     POST {"command": "get_last_images_local_only"}  ->  "SUCCESS" on
#     /farm-monitoring, then one event per camera, about 2 s apart, on
#     /farm-images:
#     {"camera": "<module id>", "image": "<base64 JPEG>", "last_update": <epoch s>}
# Read-only, and farmhand's own page sends it on every load. farmhand takes a
# photo every image_cycle_time (10800 s); the bridge asks every
# CAMERA_POLL_SECONDS and publishes a photo only when its last_update is new.
#
# Like the farmhand mobile app, only the latest photo per camera exists. It
# goes from the event straight to the camera's RETAINED MQTT topic, so the
# broker holds one photo per camera and HA shows it after a restart. Nothing
# is written to disk, and the image never goes into a sensor state or
# attribute - the recorder would keep every one.
# ---------------------------------------------------------------------------

# Follows the farm's host and port, like MONITORING_URL does.
IMAGES_URL = os.environ.get("FARM_IMAGES_URL",
                            SSE_URL.rsplit("/", 1)[0] + "/farm-images")

CMD_GET_LAST_IMAGES = {"command": "get_last_images_local_only"}

CAMERA_POLL_SECONDS = 1800
CAMERA_REPLY_SECONDS = 60          # all six photos arrive well inside this
CAMERA_DEFAULT_CYCLE_SECONDS = 10800   # image_cycle_time until get_farm_info says
CAMERA_STALE_CYCLES = 2.5          # missed photo cycles before Camera Photos Stale

# This farm's cameras: alias -> (module id, name). Used only until
# get_farm_info's camera_aliases arrives, which carries the same three fields
# for any farm. The alias is farmhand's own key: it decides the unique_id and
# the MQTT topics, so it must never change.
CAMERA_FALLBACK = {
    "nursery_top":              ("E45F01CD2C40", "Nursery Top"),
    "nursery_bottom":           ("E45F01CD2C43", "Nursery Bottom"),
    "cultivation_left":         ("E45F01CD2C8E", "Cultivation Left"),
    "cultivation_left_center":  ("E45F01CD2C73", "Cultivation Left Middle"),
    "cultivation_right_center": ("E45F01CD2C6D", "Cultivation Right Middle"),
    "cultivation_right":        ("E45F01CD2C88", "Cultivation Right"),
}

CAMERA_TOPIC = f"{MQTT_BASE_TOPIC}/camera"     # .../<alias>/image, .../<alias>/photo_time
CAMERA_STALE_TOPIC = f"{MQTT_BASE_TOPIC}/camera_photos_stale/state"
CAMERA_STALE_ATTRIBUTES_TOPIC = f"{MQTT_BASE_TOPIC}/camera_photos_stale/attributes"


@dataclass
class _CameraState:
    names: dict = field(default_factory=dict)       # module id -> (alias, name), from camera_aliases
    photo_time: dict = field(default_factory=dict)  # alias -> Photo Time of the photo on the broker
    taken: dict = field(default_factory=dict)       # alias -> that photo's last_update
    discovered: dict = field(default_factory=dict)  # alias -> name announced to HA this run
    unknown: set = field(default_factory=set)       # module ids already warned about
    connected: bool = False                         # /farm-images is open
    photos: int = 0                                 # photo events received
    drops: int = 0                                  # /farm-images disconnects
    requested_until: float = None                   # monotonic end of the last request's replies
    unprompted_logged: bool = False
    none_unprompted_logged: bool = False
    stale_published: tuple = None
    started: float = field(default_factory=time.time)


_CAM = _CameraState()
# photo_time and taken are also written from paho's thread (camera_retained).
_CAMERA_LOCK = threading.Lock()


def update_camera_names(aliases):
    """get_farm_info's camera_aliases, e.g.
    {"nursery_top": {"module": "E45F01CD2C40", "name": "Nursery Top", ...}, ...}"""
    names = {}
    if isinstance(aliases, dict):
        for raw, info in aliases.items():
            alias = farm_slug(str(raw))
            module = info.get("module") if isinstance(info, dict) else None
            if alias and isinstance(module, str) and module:
                name = str(info.get("name") or "").strip() or alias.replace("_", " ").title()
                names[module] = (alias, name)
    if names and names != _CAM.names:
        log.info("Cameras from farmhand: %s", ", ".join(n for _a, n in names.values()))
        _CAM.names = names


def _camera_map() -> dict:
    """module id -> (alias, name): farmhand's camera_aliases, or the fallback."""
    return _CAM.names or {m: (a, n) for a, (m, n) in CAMERA_FALLBACK.items()}


def _camera_name(alias: str) -> str:
    for a, n in _camera_map().values():
        if a == alias:
            return n
    return alias.replace("_", " ").title()


def _camera_cycle_seconds() -> float:
    with _FH_REPLIES:
        cycle = LATEST_FH_SETTINGS.get("image_cycle_time")
    if isinstance(cycle, bool) or not isinstance(cycle, (int, float)) or cycle <= 0:
        return CAMERA_DEFAULT_CYCLE_SECONDS
    return float(cycle)


def camera_retained(alias: str, payload: bytes):
    """A Photo Time left on the broker by the last run: its photo is still
    there, retained, so the same photo is not published again."""
    try:
        photo_time = payload.decode("utf-8")
        taken = datetime.fromisoformat(photo_time).timestamp()
    except ValueError:
        return
    with _CAMERA_LOCK:
        if alias not in _CAM.photo_time:
            _CAM.photo_time[alias] = photo_time
            _CAM.taken[alias] = taken


def _publish_camera_entities(client, alias: str, name: str):
    availability_topic = f"{MQTT_BASE_TOPIC}/bridge/status"
    client.publish(
        f"{DISCOVERY_PREFIX}/camera/{alias}/config",
        json.dumps({
            "name": f"{name} Camera",
            "unique_id": f"greenery_s_camera_{alias}",
            "topic": f"{CAMERA_TOPIC}/{alias}/image",
            "image_encoding": "b64",
            "availability_topic": availability_topic,
            "device": DEVICE_INFO,
        }),
        qos=1, retain=True,
    )
    client.publish(
        f"{DISCOVERY_PREFIX}/sensor/camera_{alias}_photo_time/config",
        json.dumps({
            "name": f"{name} Photo Time",
            "unique_id": f"greenery_s_camera_{alias}_photo_time",
            "state_topic": f"{CAMERA_TOPIC}/{alias}/photo_time",
            "device_class": "timestamp",
            "entity_category": "diagnostic",
            "availability_topic": availability_topic,
            "icon": "mdi:camera-timer",
            "device": DEVICE_INFO,
        }),
        qos=1, retain=True,
    )
    log.info(f"Published discovery config for {name} Camera and Photo Time")


def _note_unprompted(module: str):
    if _CAM.requested_until is not None and time.monotonic() <= _CAM.requested_until:
        return
    if not _CAM.unprompted_logged:
        _CAM.unprompted_logged = True
        log.info("Camera photo arrived WITHOUT a request from this app (camera %s): "
                 "farmhand sends photos unasked - unless farmhand's own page was "
                 "open, which asks on every load", module)


def handle_camera_event(client, msg):
    """One /farm-images event. Publishes the photo only if its last_update is new."""
    module = msg.get("camera") if isinstance(msg, dict) else None
    image = msg.get("image") if isinstance(msg, dict) else None
    taken = msg.get("last_update") if isinstance(msg, dict) else None
    if not (isinstance(module, str) and isinstance(image, str) and image
            and isinstance(taken, (int, float)) and not isinstance(taken, bool)):
        log.debug("farm-images: event without camera, image and last_update ignored")
        return
    _CAM.photos += 1
    _note_unprompted(module)

    entry = _camera_map().get(module)
    if entry is None:
        if module not in _CAM.unknown:
            _CAM.unknown.add(module)
            log.warning("Camera %s is not in farmhand's camera_aliases - its photos "
                        "are not published", module)
        return
    alias, name = entry
    if _CAM.discovered.get(alias) != name:
        _publish_camera_entities(client, alias, name)
        _CAM.discovered[alias] = name

    if not client.is_connected():
        # paho would hold the photo in memory until the broker is back; the
        # next request brings it again instead.
        return
    photo_time = datetime.fromtimestamp(taken, timezone.utc).isoformat(timespec="seconds")
    with _CAMERA_LOCK:
        if _CAM.photo_time.get(alias) == photo_time:
            return
        _CAM.photo_time[alias] = photo_time
        _CAM.taken[alias] = float(taken)
    # Image first: a Photo Time on the broker always has its photo there too.
    client.publish(f"{CAMERA_TOPIC}/{alias}/image", image, qos=1, retain=True)
    client.publish(f"{CAMERA_TOPIC}/{alias}/photo_time", photo_time, qos=1, retain=True)
    log.info("New photo: %s, taken %s", name, photo_time)
    # Camera Photos Stale is decided once the whole batch is in (poll_cameras):
    # deciding per photo would alert on the first stale one and miss the rest.


def publish_camera_stale(client):
    """Camera Photos Stale: ON while any camera's photo is older than
    CAMERA_STALE_CYCLES photo cycles. Published only when it changes."""
    limit = CAMERA_STALE_CYCLES * _camera_cycle_seconds()
    now = time.time()
    with _CAMERA_LOCK:
        taken = dict(_CAM.taken)
    # Every camera farmhand lists; one silent since this start counts from the
    # start. Until its list arrives, only fallback cameras that sent a photo -
    # never a camera merely left on the broker by an earlier setup.
    aliases = [a for a, _n in _camera_map().values() if _CAM.names or a in taken]
    stale = [_camera_name(a) for a in aliases if now - taken.get(a, _CAM.started) > limit]

    state = ("ON" if stale else "OFF", tuple(stale), round(limit / 3600, 1))
    if state == _CAM.stale_published:
        return
    _CAM.stale_published = state
    # Attributes first, so the alert sees the names when the state turns ON.
    client.publish(CAMERA_STALE_ATTRIBUTES_TOPIC,
                   json.dumps({"stale_cameras": stale, "stale_after_hours": state[2]}),
                   qos=1, retain=True)
    client.publish(CAMERA_STALE_TOPIC, state[0], qos=1, retain=True)
    if stale:
        log.warning("Camera photos stale - none newer than %s h from: %s",
                    state[2], ", ".join(stale))


def publish_camera_discovery(client, availability_topic: str):
    """Camera Photos Stale. Each camera and its Photo Time are announced when
    its first photo arrives, once its alias is known - see handle_camera_event."""
    client.publish(
        f"{DISCOVERY_PREFIX}/binary_sensor/camera_photos_stale/config",
        json.dumps({
            "name": "Camera Photos Stale",
            "unique_id": "greenery_s_camera_photos_stale",
            "state_topic": CAMERA_STALE_TOPIC,
            "json_attributes_topic": CAMERA_STALE_ATTRIBUTES_TOPIC,
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "problem",
            "availability_topic": availability_topic,
            "device": DEVICE_INFO,
        }),
        qos=1, retain=True,
    )
    log.info("Published discovery config for Camera Photos Stale")


async def _wait_until(ready, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not ready():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.5)
    return True


async def _request_camera_photos() -> bool:
    """Ask for every camera's latest photo. False means ask again soon."""
    # A photo sent while the stream is reconnecting is lost.
    if not await _wait_until(lambda: _CAM.connected, RECONNECT_DELAY_SECONDS + 5):
        log.warning("Not connected to %s - photos not requested", IMAGES_URL)
        return False
    photos, drops = _CAM.photos, _CAM.drops
    _CAM.requested_until = time.monotonic() + CAMERA_REPLY_SECONDS
    try:
        await asyncio.to_thread(farmhand_command, CMD_GET_LAST_IMAGES)
    except Exception as e:
        log.warning("get_last_images_local_only not sent: %s: %s", type(e).__name__, e)
        return False

    expected = len(_camera_map())
    await _wait_until(lambda: _CAM.photos - photos >= expected or _CAM.drops != drops,
                      CAMERA_REPLY_SECONDS)
    got = _CAM.photos - photos
    if got >= expected:
        return True
    if _CAM.drops != drops or not got:
        log.warning("%d of %d camera photos arrived%s", got, expected,
                    " before farmhand dropped the photo stream" if _CAM.drops != drops
                    else f" in {CAMERA_REPLY_SECONDS}s")
        return False
    # A camera that never answers is Camera Photos Stale's job; asking every
    # minute would pull ~1 MB per working camera each time.
    log.info("%d of %d cameras sent a photo", got, expected)
    return True


def _log_no_unprompted_photos():
    """Once per run: a full photo cycle passed and no photo came unasked."""
    if _CAM.unprompted_logged or _CAM.none_unprompted_logged:
        return
    cycle = _camera_cycle_seconds()
    if time.time() - _CAM.started >= cycle + CAMERA_POLL_SECONDS:
        _CAM.none_unprompted_logged = True
        log.info("No camera photo arrived without a request in the first %.1f h: "
                 "farmhand sends photos only when asked",
                 (cycle + CAMERA_POLL_SECONDS) / 3600)


async def poll_cameras(client):
    """get_last_images_local_only at start and every CAMERA_POLL_SECONDS."""
    # Names come from get_farm_info, which poll_farmhand sends at start.
    await _wait_until(lambda: _CAM.names, 2 * FH_REPLY_TIMEOUT)
    while True:
        delay = CAMERA_POLL_SECONDS
        if _FARM_COMMAND_LOCK.locked():
            # A command is mid-verify; leave farmhand to it.
            delay = FH_POLL_RETRY_SECONDS
        elif not await _request_camera_photos():
            delay = FH_POLL_RETRY_SECONDS
        publish_camera_stale(client)
        _log_no_unprompted_photos()
        await asyncio.sleep(delay)


async def stream_farm_images(client):
    timeout = aiohttp.ClientTimeout(total=None, sock_read=SSE_READ_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        log.debug("Connecting to photo stream: %s", IMAGES_URL)
        async with session.get(IMAGES_URL,
                               headers={"Accept": "text/event-stream"}) as response:
            response.raise_for_status()
            _CAM.connected = True
            try:
                async for text in _sse_events(response.content, "farm-images"):
                    try:
                        msg = json.loads(text)
                    except json.JSONDecodeError:
                        log.debug("farm-images: non-JSON event skipped")
                        continue
                    try:
                        handle_camera_event(client, msg)
                    except Exception:
                        log.exception("farm-images: event handler failed")
            finally:
                _CAM.connected = False
                _CAM.drops += 1


async def run_farm_images(client):
    """Same reconnect loop as /farm-data and /farm-monitoring, beside them."""
    while True:
        try:
            await stream_farm_images(client)
            log.debug("farm-images stream ended; reconnecting")
        except asyncio.TimeoutError:
            # Nothing for SSE_READ_TIMEOUT_SECONDS: normal between requests.
            log.debug("farm-images stream idle; reconnecting")
        except aiohttp.ClientError as e:
            log.warning(f"farm-images stream dropped ({e}); reconnecting in "
                        f"{RECONNECT_DELAY_SECONDS}s")
        except Exception as e:
            log.error(f"farm-images: unexpected error: {e}; reconnecting in "
                      f"{RECONNECT_DELAY_SECONDS}s")
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


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
    DOSING.log_config()
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
                        asyncio.create_task(watch_board_data(mqtt_client)),
                        asyncio.create_task(watch_dosing_tanks(mqtt_client)),
                        asyncio.create_task(run_farm_images(mqtt_client)),
                        asyncio.create_task(poll_cameras(mqtt_client)),
                        asyncio.create_task(watch_duplicate_bridge(mqtt_client))]

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
