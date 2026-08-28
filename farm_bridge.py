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
import time
from dataclasses import dataclass

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
MQTT_PORT = 1883
# Credentials pulled from environment, NOT hardcoded - see note below on setting these.
MQTT_USERNAME = os.environ.get("FARM_MQTT_USER")
MQTT_PASSWORD = os.environ.get("FARM_MQTT_PASS")

MQTT_BASE_TOPIC = "farm"          # state topics published under farm/<node>/<field>
DISCOVERY_PREFIX = "homeassistant"  # HA's default discovery prefix - don't change
                                     # unless you customized it in HA's MQTT integration

RECONNECT_DELAY_SECONDS = 5       # wait before retrying SSE after a drop
SSE_READ_TIMEOUT_SECONDS = 90     # stream aborts ~60s server-side; this is our
                                   # client-side ceiling before we force a reconnect

LOG_LEVEL = logging.INFO

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
]


def _output_board(payload: dict) -> dict:
    try:
        return payload["state"][OUTPUT_DEVICE_ID]
    except (KeyError, TypeError):
        return {}


def publish_binary_states(client, payload: dict):
    board = _output_board(payload)
    if not board:
        log.warning("Output board %s missing from frame", OUTPUT_DEVICE_ID)
        return

    outputs = board.get("state") or {}
    modes = board.get("mode") or {}
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
            "device": {
                "identifiers": ["greenery_s_farm"],
                "name": "Greenery S Farm",
                "manufacturer": "Freight Farms",
                "model": "Greenery S",
            },
        }
        if bsensor.device_class:
            payload["device_class"] = bsensor.device_class
        if bsensor.entity_category:
            payload["entity_category"] = bsensor.entity_category
        client.publish(config_topic, json.dumps(payload), qos=1, retain=True)
        log.info(f"Published discovery config for {bsensor.name}")
# --- END relay output board mapping ---


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

    def on_connect(c, userdata, flags, rc):
        msg = CONNACK_CODES.get(rc, f"Unknown return code {rc}")
        if rc == 0:
            log.info(f"MQTT connected: {msg}")
            c.publish(availability_topic, "online", qos=1, retain=True)
        else:
            log.error(f"MQTT connection FAILED: {msg}")

    def on_disconnect(c, userdata, rc):
        if rc != 0:
            log.warning(f"MQTT disconnected unexpectedly (rc={rc}); paho will auto-reconnect")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

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
        "device": {
            "identifiers": ["greenery_s_farm"],
            "name": "Greenery S Farm",
            "manufacturer": "Freight Farms",
            "model": "Greenery S",
        },
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
            "device": {
                "identifiers": ["greenery_s_farm"],
                "name": "Greenery S Farm",
                "manufacturer": "Freight Farms",
                "model": "Greenery S",
            },
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


async def run_forever():
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

    while True:
        try:
            await stream_farm_data(mqtt_client)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning(f"SSE stream dropped ({e}); reconnecting in {RECONNECT_DELAY_SECONDS}s")
        except Exception as e:
            log.error(f"Unexpected error: {e}; reconnecting in {RECONNECT_DELAY_SECONDS}s")
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
