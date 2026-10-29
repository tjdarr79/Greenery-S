"""
Unit tests for the dosing tank level sensors in farm_bridge.py.

    python -m unittest discover -s tests -q        (from the repo root)
    pytest tests -q

Standard library only. aiohttp, paho-mqtt and python-dotenv are stubbed when
they are not installed: nothing here touches the network or a broker. Every
test runs the monitor on a fake clock, one reading per `step` seconds.
"""

import json
import logging
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _stub_missing_modules():
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        sys.modules["aiohttp"] = types.ModuleType("aiohttp")
    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        paho = types.ModuleType("paho")
        paho.mqtt = types.ModuleType("paho.mqtt")
        paho.mqtt.client = types.ModuleType("paho.mqtt.client")
        paho.mqtt.client.Client = object
        sys.modules.update({"paho": paho, "paho.mqtt": paho.mqtt,
                            "paho.mqtt.client": paho.mqtt.client})
    try:
        import dotenv  # noqa: F401
    except ImportError:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *a, **k: None
        sys.modules["dotenv"] = dotenv


_stub_missing_modules()
sys.path.insert(0, str(ROOT))
import farm_bridge as fb  # noqa: E402


def setUpModule():
    logging.disable(logging.CRITICAL)     # the bridge's own log lines


def tearDownModule():
    logging.disable(logging.NOTSET)


DAY = 86400
HOUR = 3600
WALL0 = 1_800_000_000.0
DEV = fb.DOSING_DEVICE_ID


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.published = []      # (topic, payload, retain)
        self.subscribed = []

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, retain))
        return types.SimpleNamespace(rc=0)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    # Enough of paho's Client for build_mqtt_client().
    def username_pw_set(self, *args):
        pass

    def will_set(self, *args, **kwargs):
        pass

    def connect(self, *args, **kwargs):
        pass

    def loop_start(self):
        pass

    def payloads(self, topic):
        return [p for t, p, _r in self.published if t == topic]

    def alerts(self, kind=None):
        found = [json.loads(p) for p in self.payloads(fb.DOSING_ALERT_TOPIC)]
        return [a for a in found if kind is None or a["kind"] == kind]


def frame(d1=0, d2=0, d3=0, d4=0, module=True):
    state = {"94E68607D090": {"connected": True, "state": {"temp": 21.0}}}
    if module:
        state[DEV] = {"connected": True, "state": {
            "digital_1": d1, "digital_2": d2, "digital_3": d3, "digital_4": d4,
            "analog_1": 0.0}}
    return {"state": state}


def level(slot=1):
    return f"farm/dosing_tank_{slot}_level/state"


def fault(slot=1):
    return f"farm/dosing_tank_{slot}_sensor_fault/state"


def days(slot=1):
    return f"farm/dosing_tank_{slot}_days_since_refill/state"


def available(slot=1):
    return f"farm/dosing_tank_{slot}/available"


def memory(slot=1):
    return f"farm/dosing_tank_{slot}/memory"


class Farm:
    """A DosingMonitor on a fake clock, as the bridge runs it: subscribe on
    connect, then a frame and a tick per step."""

    def __init__(self, enabled=(True, True, True, True), t=0.0, memories=None, **kw):
        self.client = FakeClient()
        self.mon = fb.DosingMonitor(list(fb.DOSING_DEFAULT_NAMES),
                                    list(fb.DOSING_DEFAULT_INPUTS),
                                    list(enabled), farm_name="Test Farm", **kw)
        self.t = t
        self.mon.subscribe(self.client, now=t)
        for slot, payload in (memories or {}).items():
            self.mon.memory_retained(slot, payload)

    @property
    def wall(self):
        return WALL0 + self.t

    def run(self, seconds, d1=0, others=0, step=1.0, frames=True, module=True):
        """d1: tank 1's raw value, or a function of the reading's index."""
        end, i = self.t + seconds, 0
        while self.t < end - 1e-9:
            self.t += step
            if frames:
                v = d1(i) if callable(d1) else d1
                self.mon.on_frame(self.client, frame(v, others, others, others, module),
                                  now=self.t, wall=self.wall)
            self.mon.tick(self.client, now=self.t, wall=self.wall)
            i += 1

    def tank(self, slot=1):
        return self.mon._tank(slot)


def flicker(ones_in_five):
    return lambda i: 1 if i % 5 < ones_in_five else 0


class DebounceTests(unittest.TestCase):

    def test_1_flicker_while_full_stays_ok_and_raises_fault(self):
        for ones in (1, 2):                       # 20 % and 40 % ones
            with self.subTest(percent=ones * 20):
                farm = Farm()
                farm.run(30, d1=flicker(ones))    # 12 changes so far
                self.assertEqual(farm.client.payloads(fault()), [])
                farm.run(15 * 60 - 30, d1=flicker(ones))
                self.assertEqual(farm.client.payloads(fault()), ["ON"])
                self.assertNotIn("ON", farm.client.payloads(level()))
                self.assertFalse(farm.tank().low)
                self.assertEqual(farm.client.alerts("low"), [])
                self.assertEqual(len(farm.client.alerts("sensor_fault")), 1)

    def test_2_full_low_once_then_ok_within_one_window(self):
        farm = Farm()
        farm.run(599, d1=1)
        self.assertEqual(farm.client.payloads(level()), [], "decided from a partial window")
        farm.run(2, d1=1)
        self.assertEqual(farm.client.payloads(level()), ["ON"])
        self.assertEqual(len(farm.client.alerts("low")), 1)
        farm.run(HOUR, d1=1)
        self.assertEqual(farm.client.payloads(level()), ["ON"], "flapped")
        self.assertEqual(len(farm.client.alerts("low")), 1)
        farm.run(600, d1=0)
        self.assertEqual(farm.client.payloads(level()), ["ON", "OFF"])
        self.assertEqual(farm.client.payloads(fault()), ["OFF"])

    def test_3_half_low_holds_either_way(self):
        ok = Farm()
        ok.run(601, d1=0)
        ok.run(300, d1=1)
        self.assertEqual(ok.client.payloads(level()), ["OFF"])
        low = Farm()
        low.run(601, d1=1)
        low.run(300, d1=0)
        self.assertEqual(low.client.payloads(level()), ["ON"])
        self.assertTrue(low.tank().low)

    def test_4_restart_mid_sequence_waits_for_a_full_window(self):
        before = Farm()
        before.run(601, d1=0)
        before.run(300, d1=1)
        after = Farm(t=before.t + 30, memories={1: before.client.payloads(memory())[-1]})
        after.run(599, d1=1)
        self.assertEqual(after.client.payloads(level()), [])
        after.run(2, d1=1)
        self.assertEqual(after.client.payloads(level()), ["ON"])

    def test_restart_while_low_does_not_alert_again(self):
        before = Farm()
        before.run(602, d1=1)
        self.assertEqual(len(before.client.alerts("low")), 1)
        after = Farm(t=before.t + 30, memories={1: before.client.payloads(memory())[-1]})
        after.run(602, d1=1)
        self.assertEqual(after.client.payloads(level()), ["ON"])
        self.assertEqual(after.client.alerts(), [])

    def test_restart_while_ok_sends_nothing(self):
        before = Farm()
        before.run(HOUR + 5, d1=0)
        mems = {s: before.client.payloads(memory(s))[-1] for s in (1, 2, 3, 4)}
        after = Farm(t=before.t + 30, memories=mems)
        after.run(HOUR + 5, d1=0)
        self.assertEqual(after.client.alerts(), [])
        self.assertEqual(after.client.payloads(level()), ["OFF"])

    def test_polarity_flip(self):
        farm = Farm(low_value=0)
        farm.run(602, d1=0, others=1)
        self.assertEqual(farm.client.payloads(level()), ["ON"])
        self.assertEqual(len(farm.client.alerts("low")), 1)

    def test_low_percent_published_on_ten_point_moves_only(self):
        farm = Farm()
        farm.run(601, d1=0)
        farm.run(600, d1=lambda i: 1 if i < 33 else 0)   # creeps up to ~5 %
        attrs = [json.loads(p)["low_percent"]
                 for p in farm.client.payloads("farm/dosing_tank_1_level/attributes")]
        self.assertEqual(attrs, [0])
        farm.run(120, d1=1)                               # ~25 %, then more
        attrs = [json.loads(p)["low_percent"]
                 for p in farm.client.payloads("farm/dosing_tank_1_level/attributes")]
        self.assertTrue(all(b - a >= 10 for a, b in zip(attrs, attrs[1:])), attrs)


class FaultTests(unittest.TestCase):

    def test_fault_holds_the_level_and_silences_low_alerts(self):
        farm = Farm()
        farm.run(602, d1=1)
        farm.run(120, d1=lambda i: i % 2)         # flicker: fault on
        self.assertEqual(farm.client.payloads(fault()), ["ON"])
        farm.run(13 * HOUR, d1=lambda i: i % 2, step=30)
        self.assertEqual(farm.client.payloads(level()), ["ON"])
        self.assertEqual(len(farm.client.alerts("low")), 1, "low alert repeated during a fault")

    def test_fault_clears_after_a_quiet_hour(self):
        farm = Farm()
        farm.run(120, d1=lambda i: i % 2)
        farm.run(HOUR - 60, d1=0)
        self.assertEqual(farm.client.payloads(fault()), ["ON"])
        farm.run(HOUR, d1=0)
        self.assertEqual(farm.client.payloads(fault()), ["ON", "OFF"])

    def test_fault_alerts_at_most_every_24_hours(self):
        farm = Farm(enabled=(True, False, False, False))
        farm.run(120, d1=lambda i: i % 2)
        farm.run(2 * HOUR + 60, d1=0, step=30)    # clears
        farm.run(120, d1=lambda i: i % 2)          # on again
        self.assertEqual(farm.client.payloads(fault()), ["ON", "OFF", "ON"])
        self.assertEqual(len(farm.client.alerts("sensor_fault")), 1)
        farm.run(DAY, d1=lambda i: i % 2, step=30)
        self.assertEqual(len(farm.client.alerts("sensor_fault")), 2)


class AvailabilityTests(unittest.TestCase):

    def test_5_module_missing_over_60s_is_unavailable_never_low(self):
        farm = Farm()
        farm.run(100, d1=0)
        self.assertEqual(farm.client.payloads(available()), ["online"])
        farm.run(59, module=False)
        self.assertEqual(farm.client.payloads(available()), ["online"])
        farm.run(3, module=False)
        self.assertEqual(farm.client.payloads(available()), ["online", "offline"])
        self.assertNotIn("ON", farm.client.payloads(level()))
        farm.run(1, d1=0)
        self.assertEqual(farm.client.payloads(available()), ["online", "offline", "online"])
        self.assertEqual(farm.client.alerts(), [])

    def test_5_short_reconnect_gap_changes_nothing(self):
        farm = Farm()
        farm.run(700, d1=0)
        before = list(farm.client.published)
        farm.run(5, frames=False)
        farm.run(10, d1=0)
        self.assertEqual(farm.client.published, before)

    def test_no_alert_while_unavailable(self):
        farm = Farm(enabled=(True, False, False, False))
        farm.run(602, d1=1)
        farm.run(13 * HOUR, module=False, step=30)
        self.assertEqual(len(farm.client.alerts("low")), 1)


class BackstopTests(unittest.TestCase):

    def test_6_silent_21_days_alerts_once_weekly_and_refilled_resets(self):
        farm = Farm(enabled=(True, False, False, False))
        farm.run(21 * DAY - 120, d1=0, step=55)
        self.assertEqual(farm.client.alerts(), [])
        farm.run(240, d1=0, step=55)
        silent = farm.client.alerts("silent")
        self.assertEqual(len(silent), 1)
        self.assertEqual(silent[0]["priority"], "low")
        self.assertIn("Nutrient A hasn't reported low in 21 days", silent[0]["message"])
        farm.run(6 * DAY, d1=0, step=55)
        self.assertEqual(len(farm.client.alerts("silent")), 1)
        farm.run(DAY + 120, d1=0, step=55)
        self.assertEqual(len(farm.client.alerts("silent")), 2, "weekly repeat")

        farm.mon.refilled(farm.client, 1, wall=farm.wall)
        self.assertEqual(farm.client.payloads(days())[-1], 0)
        farm.run(20 * DAY, d1=0, step=55)
        self.assertEqual(len(farm.client.alerts("silent")), 2)
        farm.run(DAY + 120, d1=0, step=55)
        self.assertEqual(len(farm.client.alerts("silent")), 3)

    def test_low_and_refill_restart_the_backstop(self):
        farm = Farm(enabled=(True, False, False, False), silent_days=3)
        farm.run(2 * DAY, d1=0, step=55)
        farm.run(700, d1=1)
        farm.run(700, d1=0)              # ok again ~480 s in: refill detected
        self.assertEqual(farm.client.payloads(level()), ["OFF", "ON", "OFF"])
        self.assertEqual(farm.client.payloads(days())[-1], 0)
        farm.run(3 * DAY - 400, d1=0, step=55)
        self.assertEqual(farm.client.alerts("silent"), [])
        farm.run(800, d1=0, step=55)
        self.assertEqual(len(farm.client.alerts("silent")), 1)

    def test_refilled_press_zeroes_days_since_refill(self):
        farm = Farm(enabled=(True, False, False, False))
        # Recomputed hourly, so it may read 2 for up to an hour past day 3.
        farm.run(3 * DAY + 2 * HOUR, d1=0, step=55)
        self.assertEqual(farm.client.payloads(days())[-1], 3)
        farm.mon.refilled(farm.client, 1, wall=farm.wall)
        self.assertEqual(farm.client.payloads(days())[-1], 0)
        mem = json.loads(farm.client.payloads(memory())[-1])
        self.assertEqual(mem["refill_how"], "Refilled button")

    def test_low_alert_repeats_every_12_hours(self):
        farm = Farm(enabled=(True, False, False, False))
        farm.run(602, d1=1)
        farm.run(12 * HOUR - 120, d1=1, step=30)
        self.assertEqual(len(farm.client.alerts("low")), 1)
        farm.run(240, d1=1, step=30)
        alerts = farm.client.alerts("low")
        self.assertEqual(len(alerts), 2)
        self.assertEqual(alerts[0]["message"], "Test Farm: Nutrient A tank low. Refill it.")
        self.assertEqual(alerts[0]["priority"], "normal")


class EntityTests(unittest.TestCase):

    def test_7_disabled_tank_gets_no_entities(self):
        farm = Farm(enabled=(True, True, True, False))
        farm.mon.publish_discovery(farm.client, "farm/bridge/status")
        farm.run(HOUR + 5, d1=0, others=1)
        tank4 = [(t, p) for t, p, _r in farm.client.published if "dosing_tank_4" in t]
        self.assertEqual(len(tank4), 4)
        self.assertTrue(all(t.endswith("/config") and p == "" for t, p in tank4), tank4)
        self.assertNotIn("farm/dosing_tank_4_refilled/set", farm.mon.refilled_topics())

    def test_discovery_names_and_classes(self):
        farm = Farm()
        farm.mon.publish_discovery(farm.client, "farm/bridge/status")
        cfg = {t: json.loads(p) for t, p, r in farm.client.published if t.endswith("/config")}
        lvl = cfg["homeassistant/binary_sensor/dosing_tank_4_level/config"]
        self.assertEqual(lvl["name"], "Boost / pH Up Level")
        self.assertEqual(lvl["unique_id"], "greenery_s_dosing_tank_4_level")
        self.assertEqual((lvl["device_class"], lvl["icon"]), ("problem", "mdi:cup-water"))
        self.assertEqual(lvl["device"]["identifiers"], ["greenery_s_farm"])
        flt = cfg["homeassistant/binary_sensor/dosing_tank_2_sensor_fault/config"]
        self.assertEqual((flt["name"], flt["entity_category"]),
                         ("Nutrient B Sensor Fault", "diagnostic"))
        self.assertEqual(cfg["homeassistant/sensor/dosing_tank_3_days_since_refill/config"]["name"],
                         "pH Down Days Since Refill")
        self.assertEqual(cfg["homeassistant/button/dosing_tank_1_refilled/config"]["name"],
                         "Nutrient A Refilled")
        names = [c["name"] for c in cfg.values()]
        self.assertEqual(len(names), 16)

    def test_alerts_are_never_retained(self):
        farm = Farm()
        farm.run(602, d1=1)
        self.assertTrue(farm.client.alerts())
        self.assertFalse(any(r for t, _p, r in farm.client.published
                             if t == fb.DOSING_ALERT_TOPIC))

    def test_alert_sent_once_while_broker_unreachable(self):
        # paho queues a QoS 1 publish while disconnected and returns
        # MQTT_ERR_NO_CONN; a retry would deliver the alert twice on reconnect.
        farm = Farm(enabled=(True, False, False, False))
        record = farm.client.publish
        farm.client.publish = lambda *a, **k: (record(*a, **k), types.SimpleNamespace(rc=4))[1]
        farm.run(700, d1=1)
        self.assertEqual(len(farm.client.alerts("low")), 1)

    def test_unusable_readings_are_skipped(self):
        farm = Farm()
        bad = frame()
        bad["state"][DEV]["state"].update(digital_1=float("nan"), digital_2="x", digital_3=None)
        farm.mon.on_frame(farm.client, bad, now=1.0, wall=WALL0 + 1)
        self.assertIsNone(farm.tank(1).seen_at)
        self.assertIsNone(farm.tank(3).seen_at)
        self.assertEqual(farm.tank(4).seen_at, 1.0)

    def test_recorder_budget_full_tanks(self):
        farm = Farm()
        farm.run(2 * HOUR, d1=0)
        settled = len(farm.client.published)
        farm.run(4 * HOUR, d1=0)
        # Nothing at all while the tanks sit full - not even memory.
        self.assertEqual(farm.client.published[settled:], [])

    def test_recorder_budget_flickering_tank(self):
        farm = Farm()
        farm.run(2 * HOUR, d1=flicker(1))
        settled = len(farm.client.published)
        farm.run(4 * HOUR, d1=flicker(1))
        rows = [t for t, _p, _r in farm.client.published[settled:]
                if t != fb.DOSING_ALERT_TOPIC and not t.endswith("/memory")]
        self.assertLess(len(rows) / 4, 10)

    def test_retained_memory_only_first_counts(self):
        farm = Farm(t=0)
        good = json.dumps({"last_refill": WALL0 - 5 * DAY, "refill_how": "Refilled button",
                           "last_low": None, "low_alert": None, "fault_alert": None,
                           "backstop_alert": None})
        later = json.dumps({"last_refill": WALL0, "refill_how": "x"})
        farm.mon.memory_retained(1, good)
        farm.mon.memory_retained(1, later)
        farm.mon.memory_retained(2, b"not json")
        farm.run(5, d1=0)
        self.assertEqual(farm.client.payloads(days(1))[-1], 5)
        self.assertEqual(farm.client.payloads(days(2))[-1], 0)


class ConfigTests(unittest.TestCase):

    def setUp(self):
        fb._DOSING_CONFIG_PROBLEMS.clear()

    def env(self, **values):
        return mock.patch.dict(os.environ, values)

    def test_json_and_comma_lists(self):
        with self.env(X='["Form", "Grow", "pH Down", "Boost"]'):
            self.assertEqual(fb._dosing_env_list("X", fb.DOSING_DEFAULT_NAMES, fb._tank_name),
                             ["Form", "Grow", "pH Down", "Boost"])
        with self.env(X="digital_2, digital_1, digital_3, digital_4"):
            self.assertEqual(fb._dosing_env_list("X", fb.DOSING_DEFAULT_INPUTS, fb._tank_input),
                             ["digital_2", "digital_1", "digital_3", "digital_4"])
        with self.env(X="[true, true, true, false]"):
            self.assertEqual(fb._dosing_env_list("X", (True,) * 4, fb._tank_enabled),
                             [True, True, True, False])
        self.assertEqual(fb._DOSING_CONFIG_PROBLEMS, [])

    def test_bad_entries_fall_back_to_defaults(self):
        with self.env(X='["digital_9", "analog_1"]'):
            self.assertEqual(fb._dosing_env_list("X", fb.DOSING_DEFAULT_INPUTS, fb._tank_input),
                             ["digital_9", "digital_2", "digital_3", "digital_4"])
        with self.env(X='["Form", "   ", "pH Down", "Boost / pH Up"]'):
            self.assertEqual(fb._dosing_env_list("X", fb.DOSING_DEFAULT_NAMES, fb._tank_name)[1],
                             "Nutrient B")
        with self.env(X="7"):
            self.assertEqual(fb._dosing_env_int("X", 1, 0, 1), 1)
        with self.env(X="null"):
            self.assertEqual(fb._dosing_env_int("X", 600, 60, 3600), 600)
        self.assertEqual(len(fb._DOSING_CONFIG_PROBLEMS), 4)


class MqttWiringTests(unittest.TestCase):
    """build_mqtt_client's handlers, against a fake paho client."""

    def setUp(self):
        self.mon = fb.DosingMonitor(list(fb.DOSING_DEFAULT_NAMES), list(fb.DOSING_DEFAULT_INPUTS),
                                    [True] * 4, farm_name="Test Farm")
        patches = [mock.patch.object(fb.mqtt, "Client", FakeClient),
                   mock.patch.object(fb, "DOSING", self.mon)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = fb.build_mqtt_client()
        self.client.on_connect(self.client, None, {}, 0)

    def message(self, topic, payload, retain=False):
        self.client.on_message(self.client, None,
                               types.SimpleNamespace(topic=topic, payload=payload, retain=retain))

    def test_subscribes_to_refilled_and_memory(self):
        for slot in (1, 2, 3, 4):
            self.assertIn(f"farm/dosing_tank_{slot}_refilled/set", self.client.subscribed)
            self.assertIn(memory(slot), self.client.subscribed)

    def test_refilled_press_and_retained_replay(self):
        self.message("farm/dosing_tank_2_refilled/set", b"PRESS", retain=True)
        self.assertEqual(self.client.payloads(days(2)), [], "retained command replayed")
        self.message("farm/dosing_tank_2_refilled/set", b"PRESS")
        self.assertEqual(self.client.payloads(days(2)), [0])

    def test_retained_memory_restored_live_echo_ignored(self):
        mem = json.dumps({"last_refill": 1.0, "refill_how": "Refilled button"})
        self.message(memory(3), mem.encode(), retain=False)
        self.assertIsNone(self.mon._tank(3).memory)
        self.message(memory(3), mem.encode(), retain=True)
        self.assertEqual(self.mon._tank(3).memory["last_refill"], 1.0)


if __name__ == "__main__":
    unittest.main()
