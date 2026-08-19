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
