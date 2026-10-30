"""Duplicate bridge protection (1.5.1).

Two bridges on one broker, both with client ID "farm-bridge", kicked each
other off about once a second and flooded Home Assistant's database. Run:
pytest -q tests
"""

import json
import logging
import os
import re
import subprocess
import sys

import pytest
import yaml

from conftest import ROOT, FakeMqtt, fb

OURS = "greenery-bridge-greenery_s_farm-aaaaaa"
STATE = fb.DUPLICATE_BRIDGE_TOPIC
ATTRS = fb.DUPLICATE_BRIDGE_ATTRIBUTES_TOPIC
T0 = 1000.0


@pytest.fixture
def mon():
    return fb.DuplicateBridgeMonitor(OURS, "homeassistant", "1.5.1", 1761800000)


def connect(mon, mqtt, now=T0 - 100):
    mon.connected(mqtt, now=now)
    mqtt.published.clear()


def instance(id_, host="FARM-PC", version="1.5.1", app=False):
    return json.dumps({"id": id_, "host": host, "version": version, "started": 1,
                       "app": app}).encode()


def state(mqtt):
    return mqtt.payloads(STATE)[-1] if mqtt.payloads(STATE) else None


def turned_on(mqtt):
    """OFF -> ON transitions Home Assistant sees: one alert each."""
    seen = ["OFF", *mqtt.payloads(STATE)]
    return sum(1 for a, b in zip(seen, seen[1:]) if (a, b) == ("OFF", "ON"))


def attrs(mqtt):
    return json.loads(mqtt.payloads(ATTRS)[-1])


# --- 1. client ID -----------------------------------------------------------------

def test_client_id_is_new_every_start():
    prefix = f"greenery-bridge-{fb.FARM_SLUG}-"
    a, b = fb.make_client_id(), fb.make_client_id()
    assert a != b
    assert re.fullmatch(re.escape(prefix) + "[0-9a-f]{6}", a)
    assert re.fullmatch(re.escape(prefix) + "[0-9a-f]{6}", b)

    # Separate runs: what the bridge actually connects with.
    code = "import farm_bridge; print(farm_bridge.MQTT_CLIENT_ID)"
    runs = [subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=os.environ.copy(),
                           capture_output=True, text=True, check=True).stdout.strip()
            for _ in range(2)]
    assert runs[0] != runs[1]
    assert all(r.startswith(prefix) for r in runs)


class FakePaho:
    """paho's Client, recording every call in order."""

    def __init__(self, client_id=None, clean_session=None):
        self.client_id = client_id
        self.calls = []

    def username_pw_set(self, *a):
        pass

    def will_set(self, topic, payload=None, qos=0, retain=False):
        self.calls.append(("will", topic, payload))

    def connect(self, *a, **k):
        pass

    def loop_start(self):
        pass

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.calls.append(("publish", topic, payload))

    def subscribe(self, topic, qos=0):
        self.calls.append(("subscribe", topic))


class Msg:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, payload, retain


@pytest.fixture
def paho(monkeypatch, mon):
    monkeypatch.setattr(fb.mqtt, "Client", FakePaho)
    monkeypatch.setattr(fb, "MQTT_CLIENT_ID", OURS)
    monkeypatch.setattr(fb, "DUPLICATE", mon)
    # on_connect also starts the Cool-Down and dosing read-back windows.
    monkeypatch.setattr(fb, "_CD", fb._CoolDownState())
    monkeypatch.setattr(fb.DOSING, "_read_until", fb.DOSING._read_until)
    return fb.build_mqtt_client()


def test_bridge_connects_with_its_own_client_id(paho):
    assert paho.client_id == OURS
    assert paho.client_id != "farm-bridge"


def test_announces_before_subscribing_so_a_restart_is_quiet(paho, mon, caplog):
    caplog.set_level(logging.INFO, logger="farm_bridge")
    paho.on_connect(paho, None, {}, 0)
    assert OURS in caplog.text

    calls = paho.calls
    online = calls.index(("publish", "farm/bridge/status", "online"))
    announce = next(i for i, c in enumerate(calls) if c[:2] == ("publish", "farm/bridge/instance"))
    subs = [calls.index(("subscribe", t)) for t in ("farm/bridge/instance", "farm/bridge/status")]
    assert online < min(subs) and announce < min(subs)
    assert json.loads(calls[announce][2])["id"] == OURS

    # What the broker replays on subscribe after a restart: this start's own.
    paho.on_message(paho, None, Msg("farm/bridge/instance", calls[announce][2].encode(), True))
    paho.on_message(paho, None, Msg("farm/bridge/status", b"online", True))
    assert not mon.on
    assert ("publish", STATE, "OFF") in calls
    assert ("publish", STATE, "ON") not in calls


def test_messages_reach_the_monitor(paho, mon):
    paho.on_connect(paho, None, {}, 0)
    paho.on_message(paho, None, Msg("farm/bridge/instance", instance("greenery-bridge-x-bbbbbb")))
    assert mon.on and mon.reason == "instance"


# --- 2a. another bridge's announcement ---------------------------------------------

def test_foreign_instance_turns_it_on(mon, mqtt, caplog):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("greenery-bridge-greenery_s_farm-bbbbbb"), now=T0)
    assert state(mqtt) == "ON"
    assert attrs(mqtt) == {"other_id": "greenery-bridge-greenery_s_farm-bbbbbb",
                           "other_host": "FARM-PC", "other_version": "1.5.1",
                           "reason": "instance"}
    assert ("Another Greenery bridge is running on this broker: "
            "id=greenery-bridge-greenery_s_farm-bbbbbb, host=FARM-PC, version=1.5.1") in caplog.text


def test_own_instance_does_not(mon, mqtt):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance(OURS), now=T0)
    mon.instance_message(mqtt, b"", now=T0)          # a cleared retained message
    mon.instance_message(mqtt, b"not json", now=T0)
    assert not mon.on
    assert mqtt.published == []


def test_error_logged_once_per_10_minutes(mon, mqtt, caplog):
    connect(mon, mqtt)
    for t in (0, 60, 300, 599):
        mon.instance_message(mqtt, instance("other"), now=T0 + t)
    assert caplog.text.count("Another Greenery bridge") == 1
    mon.instance_message(mqtt, instance("other"), now=T0 + 600)
    assert caplog.text.count("Another Greenery bridge") == 2


# --- 2b. a stray offline -------------------------------------------------------------

def test_foreign_offline_sets_online_at_once_and_turns_it_on(mon, mqtt):
    connect(mon, mqtt)
    mon.status_message(mqtt, b"offline", now=T0)
    assert mqtt.published[0] == ("farm/bridge/status", "online", 1, True)
    assert state(mqtt) == "ON"
    assert attrs(mqtt)["reason"] == "foreign_offline"


def test_own_online_is_not_another_bridge(mon, mqtt):
    connect(mon, mqtt, now=T0)
    mon.status_message(mqtt, b"online", retain=True, now=T0 + 50)   # replay on subscribe
    mon.status_message(mqtt, b"online", now=T0 + 1)                 # paho's resend on reconnect
    mon.status_message(mqtt, b"offline", now=T0 + 100)              # a Last Will...
    mon.status_message(mqtt, b"online", now=T0 + 100.05)            # ...our answer's echo
    mon.tick(mqtt, now=T0 + 130)
    assert mon.reason == "foreign_offline"
    assert mqtt.payloads("farm/bridge/status") == ["online"]       # no answer to an online


def test_an_old_bridge_starting_just_after_our_answer_is_seen(mon, mqtt):
    """Caught end to end: a 1.5.0 bridge connected 2.3 s after this bridge
    answered a Last Will, and a time window took its online for our echo."""
    connect(mon, mqtt)
    mon.status_message(mqtt, b"offline", now=T0)
    mon.status_message(mqtt, b"online", now=T0 + 0.01)             # our echo
    mon.status_message(mqtt, b"online", now=T0 + 2.3)              # the 1.5.0 bridge
    mon.tick(mqtt, now=T0 + 15)
    assert mon.reason == "foreign_online"


def test_unannounced_bridge_is_not_given_an_earlier_bridges_host(mon, mqtt):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("pc-bridge", host="HYDRO"), now=T0)
    mon.status_message(mqtt, b"online", now=T0 + 120)             # a 1.5.0 bridge, later
    mon.tick(mqtt, now=T0 + 135)
    assert attrs(mqtt) == {"other_id": None, "other_host": None, "other_version": None,
                           "reason": "foreign_online"}


# --- 2d. a bridge that never announces itself (before 1.5.1) ---------------------------

def test_unannounced_bridge_turns_it_on_until_it_stops(mon, mqtt, caplog):
    connect(mon, mqtt)
    mon.status_message(mqtt, b"online", now=T0)        # the old bridge connects
    mon.tick(mqtt, now=T0 + 9)
    assert not mon.on                                  # a 1.5.1 bridge would announce first
    mon.tick(mqtt, now=T0 + 10)
    assert state(mqtt) == "ON"
    assert attrs(mqtt) == {"other_id": None, "other_host": None, "other_version": None,
                           "reason": "foreign_online"}
    assert "never announced itself" in caplog.text
    mon.tick(mqtt, now=T0 + 6 * 3600)                  # it shows nothing else while it runs
    assert mon.on
    mon.status_message(mqtt, b"offline", now=T0 + 7 * 3600)   # its Last Will
    assert attrs(mqtt)["reason"] == "foreign_offline"
    mon.tick(mqtt, now=T0 + 7 * 3600 + 599)
    assert mon.on
    mon.tick(mqtt, now=T0 + 7 * 3600 + 600)
    assert not mon.on
    assert turned_on(mqtt) == 1


def test_announced_bridge_online_is_not_unannounced(mon, mqtt):
    connect(mon, mqtt)
    mon.status_message(mqtt, b"online", now=T0)        # a 1.5.1 bridge: online...
    mon.instance_message(mqtt, instance("new-bridge"), now=T0 + 0.01)   # ...then its announcement
    mon.tick(mqtt, now=T0 + 20)
    assert attrs(mqtt)["reason"] == "instance"
    mon.tick(mqtt, now=T0 + 601)
    assert not mon.on                                  # not held: it announces while it runs


def test_offline_after_an_announcement_keeps_the_other_host(mon, mqtt):
    """The supervised test: the PC's bridge announces, then Ctrl+C."""
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("pc-bridge", host="HYDRO"), now=T0)
    mon.status_message(mqtt, b"offline", now=T0 + 120)
    assert turned_on(mqtt) == 1                         # one alert
    assert attrs(mqtt)["other_host"] == "HYDRO"
    assert attrs(mqtt)["reason"] == "foreign_offline"


# --- 2c. disconnects -----------------------------------------------------------------

def drop(mon, mqtt, now, rc=7):
    mon.disconnected(mqtt, rc, now=now)
    mon.connected(mqtt)                                # paho reconnects


def test_six_disconnects_in_60_s_turn_it_on(mon, mqtt):
    connect(mon, mqtt)
    for i in range(5):
        drop(mon, mqtt, T0 + i * 10)
    assert not mon.on
    drop(mon, mqtt, T0 + 50)
    assert mon.on and attrs(mqtt)["reason"] == "disconnects"


def test_one_disconnect_a_minute_does_not(mon, mqtt):
    connect(mon, mqtt)
    for i in range(20):
        drop(mon, mqtt, T0 + i * 60)
    assert not mon.on
    assert "ON" not in mqtt.payloads(STATE)


def test_clean_disconnects_and_refused_connects_do_not_count(mon, mqtt):
    connect(mon, mqtt)
    for i in range(10):
        drop(mon, mqtt, T0 + i, rc=0)
    assert not mon.on
    # Bad password: paho retries with back-off, never connected.
    mon.disconnected(mqtt, 7, now=T0 + 20)
    for t in (21, 23, 27, 35, 51, 55):
        mon.disconnected(mqtt, 5, now=T0 + t)
    assert not mon.on


# --- clearing ------------------------------------------------------------------------

def test_clears_after_10_quiet_minutes(mon, mqtt):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("other", host="FARM-PC"), now=T0)
    mon.tick(mqtt, now=T0 + 599)
    assert mon.on
    mon.tick(mqtt, now=T0 + 600)
    assert state(mqtt) == "OFF"
    assert attrs(mqtt) == {"other_id": None, "other_host": None, "other_version": None,
                           "reason": None}


def test_each_signal_restarts_the_10_minutes(mon, mqtt):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("other"), now=T0)
    mon.status_message(mqtt, b"offline", now=T0 + 300)
    mon.tick(mqtt, now=T0 + 899)
    assert mon.on
    mon.tick(mqtt, now=T0 + 900)
    assert not mon.on


def test_does_not_clear_while_disconnects_continue(mon, mqtt):
    connect(mon, mqtt)
    for i in range(6):
        drop(mon, mqtt, T0 + i)
    for i in range(1, 12):
        drop(mon, mqtt, T0 + i * 60)                   # one a minute keeps it on
    mon.tick(mqtt, now=T0 + 11 * 60 + 1)
    assert mon.on
    mon.tick(mqtt, now=T0 + 11 * 60 + 599)            # only the last one is recent
    assert not mon.on


# --- re-announcement -----------------------------------------------------------------

def announcements(mqtt):
    return mqtt.payloads(fb.BRIDGE_INSTANCE_TOPIC)


def test_reannouncement_at_most_once_per_60_s(mon, mqtt):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("other"), now=T0)
    assert announcements(mqtt) == [mon.instance]
    for t in (1, 10, 30, 59):
        mon.instance_message(mqtt, instance("other"), now=T0 + t)
        mon.tick(mqtt, now=T0 + t)
    assert len(announcements(mqtt)) == 1
    # Held back, not dropped: two bridges keep seeing each other.
    mon.tick(mqtt, now=T0 + 60)
    assert len(announcements(mqtt)) == 2
    mon.tick(mqtt, now=T0 + 200)
    assert len(announcements(mqtt)) == 2              # nothing new to answer


def test_app_alone_writes_the_sensor_so_it_names_the_pc():
    """The supervised test: the PC's bridge sees the app and leaves the sensor
    to it, so other_host stays the PC."""
    app = fb.DuplicateBridgeMonitor("app", "a0d7b954-greenery-bridge", "1.5.1", 1, app=True)
    pc = fb.DuplicateBridgeMonitor("pc", "HYDRO", "1.5.1", 2)
    broker = FakeMqtt()
    app.connected(broker)
    pc.connected(broker)                              # its own OFF, before it knows
    app.instance_message(broker, pc.instance.encode(), now=T0)
    pc.instance_message(broker, app.instance.encode(), now=T0)
    pc.status_message(broker, b"offline", now=T0 + 1)  # the app's Last Will, say
    assert pc.on and app.on
    assert state(broker) == "ON"
    assert attrs(broker)["other_host"] == "HYDRO"
    # Even when the PC's bridge started after the app's alarm was on.
    broker.publish(STATE, "OFF")                       # a fresh PC start's OFF
    app.instance_message(broker, pc.instance.encode(), now=T0 + 60)
    assert state(broker) == "ON" and attrs(broker)["other_host"] == "HYDRO"


def test_a_signal_rewrites_the_sensor_another_bridge_overwrote(mon, mqtt):
    connect(mon, mqtt)
    mon.instance_message(mqtt, instance("other"), now=T0)
    mqtt.publish(STATE, "OFF")                         # the other bridge's start
    mon.instance_message(mqtt, instance("other"), now=T0 + 60)
    assert state(mqtt) == "ON"


def test_two_bridges_stay_on_while_both_run():
    """Two monitors wired to one fake broker: neither clears while both run."""
    a = fb.DuplicateBridgeMonitor("bridge-a", "HA", "1.5.1", 1)
    b = fb.DuplicateBridgeMonitor("bridge-b", "PC", "1.5.1", 2)

    class Broker(FakeMqtt):
        def __init__(self):
            super().__init__()
            self.queue = []

        def publish(self, topic, payload=None, qos=0, retain=False):
            super().publish(topic, payload, qos, retain)
            if topic == fb.BRIDGE_INSTANCE_TOPIC:
                self.queue.append(payload)

    broker = Broker()
    a.connected(broker)
    b.connected(broker)
    for second in range(0, 3600, fb.DUPLICATE_CHECK_SECONDS):
        while broker.queue:
            payload = broker.queue.pop(0).encode()
            a.instance_message(broker, payload, now=T0 + second)
            b.instance_message(broker, payload, now=T0 + second)
        a.tick(broker, now=T0 + second)
        b.tick(broker, now=T0 + second)
        assert a.on and b.on, f"cleared at {second} s"
    sent = broker.payloads(fb.BRIDGE_INSTANCE_TOPIC)
    assert len(sent) <= 2 + 2 * 3600 / fb.DUPLICATE_REPUBLISH_SECONDS + 2


# --- discovery and the alert -----------------------------------------------------------

def test_discovery(mon, mqtt):
    mon.publish_discovery(mqtt)
    topic, payload, _qos, retain = mqtt.published[0]
    assert topic == "homeassistant/binary_sensor/duplicate_bridge_detected/config" and retain
    assert json.loads(payload) == {
        "name": "Duplicate Bridge Detected",
        "unique_id": "greenery_s_duplicate_bridge_detected",
        "state_topic": STATE,
        "json_attributes_topic": ATTRS,
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "problem",
        "entity_category": "diagnostic",
        "device": fb.DEVICE_INFO,
    }


def test_alert_automation_watches_this_sensor():
    auto = yaml.safe_load((ROOT / "automations/25-duplicate-bridge.yaml").read_text(encoding="utf-8"))
    entity = "binary_sensor." + fb.farm_slug(fb.LEGACY_FARM_NAME) + "_" + fb.farm_slug(
        "Duplicate Bridge Detected")
    assert auto["alias"] == "Farm Duplicate Bridge"
    assert auto["triggers"] == [{"entity_id": entity, "from": "off", "to": "on",
                                 "trigger": "state"}]
    data = auto["actions"][0]["data"]
    assert auto["actions"][0]["action"] == "script.farm_alerts"
    assert data["critical"] is True
    assert "other_host" in data["message"]
