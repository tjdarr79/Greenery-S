"""Cameras, latest photo only - tested against real farmhand captures.

The fixtures (tests/fixtures/farmhand/) are what farmhand 2.0.26 sent on the
owner's farm, with the photos shrunk to thumbnails. Run:  pytest -q tests
"""

import asyncio
import json
import logging
from datetime import datetime

import yaml
from aiohttp import web

from conftest import FIXTURES, ROOT, FakeMqtt, fb, image_events, load_json, sse_blocks

ALIASES = list(fb.CAMERA_FALLBACK)
STALE_STATE = fb.CAMERA_STALE_TOPIC
STALE_ATTRS = fb.CAMERA_STALE_ATTRIBUTES_TOPIC


def image_topic(alias):
    return f"farm/camera/{alias}/image"


def time_topic(alias):
    return f"farm/camera/{alias}/photo_time"


def feed(mqtt, events):
    for ev in events:
        fb.handle_camera_event(mqtt, ev)


def farm_info():
    """The real get_farm_info reply, as /farm-monitoring delivers it."""
    return load_json("farmhand_replies_2026-10-15.json")["get_farm_info"]


# --- names --------------------------------------------------------------------

def test_fallback_table_is_what_farmhand_reports():
    fb.update_camera_names(load_json("camera_aliases.json"))
    assert fb._CAM.names == {m: (a, n) for a, (m, n) in fb.CAMERA_FALLBACK.items()}
    assert [a for a, _n in fb._CAM.names.values()] == ALIASES


def test_names_read_from_get_farm_info(mqtt):
    fb.handle_monitoring_event(mqtt, farm_info())
    assert fb._CAM.names["E45F01CD2C73"] == ("cultivation_left_center", "Cultivation Left Middle")
    assert fb._camera_cycle_seconds() == 10800


def test_renamed_camera_keeps_its_identity(mqtt):
    aliases = load_json("camera_aliases.json")
    aliases["nursery_top"]["name"] = "Seedlings Top"
    fb.update_camera_names(aliases)
    feed(mqtt, image_events()[:1])
    cfg = json.loads(mqtt.payloads("homeassistant/camera/nursery_top/config")[0])
    assert cfg["name"] == "Seedlings Top Camera"
    assert cfg["unique_id"] == "greenery_s_camera_nursery_top"
    assert cfg["topic"] == image_topic("nursery_top")


def test_fallback_used_until_camera_aliases_arrive(mqtt):
    feed(mqtt, image_events())
    assert set(mqtt.topics()) >= {image_topic(a) for a in ALIASES}


# --- the /farm-images reader ----------------------------------------------------

class ChunkedContent:
    """aiohttp's response.content, delivering the bytes in awkward pieces."""

    def __init__(self, data, size):
        self.data, self.size = data, size

    async def iter_any(self):
        for i in range(0, len(self.data), self.size):
            yield self.data[i:i + self.size]


def test_sse_reader_splits_the_real_wire_format():
    raw = (FIXTURES / "farm_images_sample.sse").read_bytes()

    async def collect(size):
        return [json.loads(t) async for t in fb._sse_events(ChunkedContent(raw, size), "farm-images")]

    for size in (7, 1000, 65536, len(raw)):
        events = asyncio.run(collect(size))
        assert [e["camera"] for e in events] == [e["camera"] for e in image_events()]
        assert all(e["image"].startswith("/9j/") for e in events)


# --- publishing -------------------------------------------------------------------

def test_discovery_matches_the_brief(mqtt):
    feed(mqtt, image_events())
    for alias, (_module, name) in fb.CAMERA_FALLBACK.items():
        cam = json.loads(mqtt.payloads(f"homeassistant/camera/{alias}/config")[0])
        assert cam == {
            "name": f"{name} Camera",
            "unique_id": f"greenery_s_camera_{alias}",
            "topic": image_topic(alias),
            "image_encoding": "b64",
            "availability_topic": "farm/bridge/status",
            "device": fb.DEVICE_INFO,
        }
        pt = json.loads(mqtt.payloads(f"homeassistant/sensor/camera_{alias}_photo_time/config")[0])
        assert pt["name"] == f"{name} Photo Time"
        assert pt["unique_id"] == f"greenery_s_camera_{alias}_photo_time"
        assert pt["state_topic"] == time_topic(alias)
        assert pt["device_class"] == "timestamp"
        assert pt["entity_category"] == "diagnostic"
        assert pt["device"] == fb.DEVICE_INFO


def test_photo_published_retained_with_its_time(mqtt):
    events = image_events()
    feed(mqtt, events)
    by_module = {m: a for a, (m, _n) in fb.CAMERA_FALLBACK.items()}
    for ev in events:
        alias = by_module[ev["camera"]]
        [(topic, payload, qos, retain)] = [p for p in mqtt.published if p[0] == image_topic(alias)]
        assert (payload, qos, retain) == (ev["image"], 1, True)
        [stamp] = mqtt.payloads(time_topic(alias))
        assert datetime.fromisoformat(stamp).timestamp() == int(ev["last_update"])
    # The photo goes before its time, so a Photo Time on the broker always has
    # its photo there too.
    topics = mqtt.topics()
    assert topics.index(image_topic("nursery_top")) < topics.index(time_topic("nursery_top"))


def test_unchanged_photo_never_republished(mqtt):
    events = image_events()
    feed(mqtt, events)
    count = len(mqtt.published)
    feed(mqtt, events)                       # the 30-minute re-ask, nothing new
    assert len(mqtt.published) == count

    newer = dict(events[0], last_update=events[0]["last_update"] + 10800)
    fb.handle_camera_event(mqtt, newer)      # next photo cycle, one camera
    fresh = [t for t, *_ in mqtt.published[count:]]
    assert fresh.count(image_topic("nursery_top")) == 1
    assert not any(t.endswith("/image") and t != image_topic("nursery_top") for t in fresh)


def test_restart_does_not_republish_what_the_broker_holds(mqtt):
    events = image_events()
    feed(mqtt, events)
    retained = {t: p for t, p, _q, r in mqtt.published if t.endswith("/photo_time") and r}

    # A new bridge process: memory gone, broker hands back its retained Photo Times.
    fb._CAM = fb._CameraState()
    after = type(mqtt)()
    for topic, stamp in retained.items():
        fb.camera_retained(topic.split("/")[-2], stamp.encode())
    feed(after, events)
    assert not [t for t in after.topics() if t.endswith("/image") or t.endswith("/photo_time")]
    # Discovery is still announced once per run, like every other entity.
    assert "homeassistant/camera/nursery_top/config" in after.topics()


def test_image_only_ever_goes_to_a_camera_image_topic(mqtt):
    fb.handle_monitoring_event(mqtt, farm_info())
    feed(mqtt, image_events())
    fb.publish_camera_stale(mqtt)
    images = {e["image"] for e in image_events()}
    for topic, payload, _q, _r in mqtt.published:
        if isinstance(payload, str) and (payload in images or len(payload) > 5000):
            assert topic.startswith("farm/camera/") and topic.endswith("/image"), topic


def test_broker_down_photo_not_queued_but_sent_once_back(mqtt):
    mqtt.connected = False
    feed(mqtt, image_events())
    assert not [t for t in mqtt.topics() if t.endswith("/image")]
    mqtt.connected = True
    feed(mqtt, image_events())                # the next 30-minute request
    assert len([t for t in mqtt.topics() if t.endswith("/image")]) == 6


def test_unknown_camera_is_skipped_and_warned_once(mqtt, caplog):
    stranger = dict(image_events()[0], camera="AAAAAAAAAAAA")
    with caplog.at_level(logging.WARNING, logger="farm_bridge"):
        fb.handle_camera_event(mqtt, stranger)
        fb.handle_camera_event(mqtt, stranger)
    assert mqtt.published == []
    assert sum("AAAAAAAAAAAA" in r.message for r in caplog.records) == 1


def test_malformed_events_ignored(mqtt):
    good = image_events()[0]
    for bad in ({}, "SUCCESS", dict(good, image=""), dict(good, last_update=None),
                dict(good, last_update=True), dict(good, camera=None)):
        fb.handle_camera_event(mqtt, bad)
    assert mqtt.published == []


# --- unprompted photos ----------------------------------------------------------------

def test_photo_without_a_request_is_logged_once(mqtt, caplog):
    with caplog.at_level(logging.INFO, logger="farm_bridge"):
        feed(mqtt, image_events())
    assert sum("WITHOUT a request" in r.message for r in caplog.records) == 1


def test_requested_photos_are_not_called_unprompted(mqtt, caplog):
    fb._CAM.requested_until = fb.time.monotonic() + 60
    with caplog.at_level(logging.INFO, logger="farm_bridge"):
        feed(mqtt, image_events())
    assert not any("WITHOUT a request" in r.message for r in caplog.records)


def test_no_unprompted_photo_in_a_full_cycle_is_logged(caplog, monkeypatch):
    fb._CAM.started = 1_000_000.0
    monkeypatch.setattr(fb.time, "time", lambda: 1_000_000.0 + 10800 + 1800)
    with caplog.at_level(logging.INFO, logger="farm_bridge"):
        fb._log_no_unprompted_photos()
        fb._log_no_unprompted_photos()
    assert sum("only when asked" in r.message for r in caplog.records) == 1


# --- Camera Photos Stale ----------------------------------------------------------------

def stale_state(mqtt):
    states = mqtt.payloads(STALE_STATE)
    return states[-1] if states else None


def test_stale_after_two_and_a_half_photo_cycles(mqtt, monkeypatch):
    fb.handle_monitoring_event(mqtt, farm_info())       # image_cycle_time 10800
    events = image_events()
    oldest = min(e["last_update"] for e in events)
    newest = max(e["last_update"] for e in events)

    monkeypatch.setattr(fb.time, "time", lambda: oldest + 7.4 * 3600)
    feed(mqtt, events)
    fb.publish_camera_stale(mqtt)
    assert stale_state(mqtt) == "OFF"

    monkeypatch.setattr(fb.time, "time", lambda: newest + 7.6 * 3600)
    fb.publish_camera_stale(mqtt)
    assert stale_state(mqtt) == "ON"
    attrs = json.loads(mqtt.payloads(STALE_ATTRS)[-1])
    assert attrs == {"stale_cameras": [n for _m, n in fb.CAMERA_FALLBACK.values()],
                     "stale_after_hours": 7.5}
    # Attributes land before the state, so the alert can name the cameras.
    topics = mqtt.topics()
    assert len(topics) - topics[::-1].index(STALE_ATTRS) < len(topics) - topics[::-1].index(STALE_STATE)

    count = len(mqtt.published)
    fb.publish_camera_stale(mqtt)                         # no change, no publish
    assert len(mqtt.published) == count


def test_stale_decided_once_per_batch_not_per_photo(mqtt, monkeypatch):
    """Photos arrive ~2 s apart. Deciding per photo would turn ON naming only
    the first stale camera - and the alert fires on that first ON."""
    fb.handle_monitoring_event(mqtt, farm_info())
    events = image_events()
    monkeypatch.setattr(fb.time, "time", lambda: max(e["last_update"] for e in events) + 8 * 3600)
    feed(mqtt, events)
    assert mqtt.payloads(STALE_STATE) == []
    fb.publish_camera_stale(mqtt)
    assert mqtt.payloads(STALE_STATE) == ["ON"]
    assert len(json.loads(mqtt.payloads(STALE_ATTRS)[0])["stale_cameras"]) == 6


def test_one_stale_camera_named_alone(mqtt, monkeypatch):
    fb.handle_monitoring_event(mqtt, farm_info())
    events = image_events()
    now = max(e["last_update"] for e in events) + 3600
    monkeypatch.setattr(fb.time, "time", lambda: now)
    feed(mqtt, [dict(e, last_update=now - 600) for e in events[1:]])
    feed(mqtt, [dict(events[0], last_update=now - 8 * 3600)])
    fb.publish_camera_stale(mqtt)
    assert stale_state(mqtt) == "ON"
    assert json.loads(mqtt.payloads(STALE_ATTRS)[-1])["stale_cameras"] == ["Nursery Top"]


def test_lowered_photo_cycle_lowers_the_threshold(mqtt, monkeypatch):
    """Acceptance 5 'lower the threshold in a test': image_cycle_time 3600 -> 2.5 h."""
    info = farm_info()
    info["response"]["settings"]["image_cycle_time"] = 3600
    fb.handle_monitoring_event(mqtt, info)
    events = image_events()
    newest = max(e["last_update"] for e in events)
    monkeypatch.setattr(fb.time, "time", lambda: newest + 2.6 * 3600)
    feed(mqtt, events)
    fb.publish_camera_stale(mqtt)
    assert stale_state(mqtt) == "ON"
    assert json.loads(mqtt.payloads(STALE_ATTRS)[-1])["stale_after_hours"] == 2.5


def test_listed_camera_that_never_sends_goes_stale_from_start(mqtt, monkeypatch):
    fb.handle_monitoring_event(mqtt, farm_info())
    fb._CAM.started = 2_000_000_000.0
    monkeypatch.setattr(fb.time, "time", lambda: fb._CAM.started + 7.6 * 3600)
    feed(mqtt, [dict(e, last_update=fb._CAM.started + 7 * 3600) for e in image_events()[1:]])
    fb.publish_camera_stale(mqtt)
    assert json.loads(mqtt.payloads(STALE_ATTRS)[-1])["stale_cameras"] == ["Nursery Top"]


def test_camera_farmhand_no_longer_lists_is_never_stale(mqtt, monkeypatch):
    fb.handle_monitoring_event(mqtt, farm_info())
    events = image_events()
    now = max(e["last_update"] for e in events) + 600
    monkeypatch.setattr(fb.time, "time", lambda: now)
    fb.camera_retained("old_camera", b"2026-01-01T00:00:00+00:00")   # left on the broker
    feed(mqtt, events)
    fb.publish_camera_stale(mqtt)
    assert stale_state(mqtt) == "OFF"


def test_fallback_cameras_count_only_once_seen(mqtt, monkeypatch):
    """No camera_aliases (another farm, older farmhand): this farm's table must
    not raise a stale alarm for cameras that farm does not have."""
    fb._CAM.started = 2_000_000_000.0
    monkeypatch.setattr(fb.time, "time", lambda: fb._CAM.started + 30 * 3600)
    fb.publish_camera_stale(mqtt)
    assert stale_state(mqtt) == "OFF"


def test_discovery_of_the_stale_sensor(mqtt):
    fb.publish_camera_discovery(mqtt, "farm/bridge/status")
    cfg = json.loads(mqtt.payloads("homeassistant/binary_sensor/camera_photos_stale/config")[0])
    assert cfg["name"] == "Camera Photos Stale"
    assert cfg["unique_id"] == "greenery_s_camera_photos_stale"
    assert cfg["device_class"] == "problem"
    assert cfg["json_attributes_topic"] == STALE_ATTRS


# --- end to end: a fake farmhand over real HTTP ---------------------------------------------

def run_against_fake_farmhand(monkeypatch, scenario, drop_after=None):
    """A local server speaking farmhand's wire format from the fixtures:
    /farm-control takes commands, /farm-images streams the photos after a
    get_last_images_local_only, /farm-monitoring is not needed for photos."""
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    commands, streams = [], []
    blocks = [b.encode() for b in sse_blocks("farm_images_sample.sse")]

    async def images(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        queue = asyncio.Queue()
        streams.append(queue)
        try:
            while (chunk := await queue.get()) is not None:
                await resp.write(chunk)
        finally:
            streams.remove(queue)
        return resp

    async def control(request):
        cmd = await request.json()
        commands.append(cmd)
        if cmd == fb.CMD_GET_LAST_IMAGES:
            for queue in list(streams):
                for i, block in enumerate(blocks):
                    if drop_after is not None and i == drop_after:
                        queue.put_nowait(None)
                        break
                    queue.put_nowait(block)
        return web.Response(text="Control message received!")

    async def main():
        app = web.Application()
        app.router.add_get("/farm-images", images)
        app.router.add_post("/farm-control", control)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(fb, "IMAGES_URL", f"http://127.0.0.1:{port}/farm-images")
        monkeypatch.setattr(fb, "FARMHAND_CONTROL_URL", f"http://127.0.0.1:{port}/farm-control")
        monkeypatch.setattr(fb, "RECONNECT_DELAY_SECONDS", 0.2)
        monkeypatch.setattr(fb, "CAMERA_REPLY_SECONDS", 5)
        reader = asyncio.create_task(fb.run_farm_images(mqtt))
        try:
            return await scenario(mqtt)
        finally:
            reader.cancel()
            for queue in list(streams):     # end open streams, or cleanup waits 60 s
                queue.put_nowait(None)
            await runner.cleanup()

    mqtt = FakeMqtt()
    return asyncio.run(main()), commands, mqtt


def test_request_brings_all_six_photos_then_nothing_new(monkeypatch):
    async def scenario(mqtt):
        first = await fb._request_camera_photos()
        published = len(mqtt.published)
        second = await fb._request_camera_photos()
        return first, second, published

    (first, second, published), commands, mqtt = run_against_fake_farmhand(monkeypatch, scenario)

    assert first is True and second is True
    assert commands == [fb.CMD_GET_LAST_IMAGES, fb.CMD_GET_LAST_IMAGES]
    assert len([t for t in mqtt.topics() if t.endswith("/image")]) == 6
    assert len(mqtt.published) == published       # second answer: all duplicates


def test_stream_drop_mid_delivery_asks_again(monkeypatch):
    async def scenario(_mqtt):
        return await fb._request_camera_photos()

    ok, _commands, mqtt = run_against_fake_farmhand(monkeypatch, scenario, drop_after=3)
    assert ok is False                              # poll_cameras retries in 60 s
    assert len([t for t in mqtt.topics() if t.endswith("/image")]) == 3


def test_farmhand_unreachable_is_reported_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(fb, "CAMERA_REPLY_SECONDS", 1)
    monkeypatch.setattr(fb, "RECONNECT_DELAY_SECONDS", 0)
    with caplog.at_level(logging.WARNING, logger="farm_bridge"):
        assert asyncio.run(fb._request_camera_photos()) is False
    assert any("photos not requested" in r.message for r in caplog.records)


# --- Home Assistant YAML ------------------------------------------------------------------------

def test_stale_automation():
    doc = yaml.safe_load((ROOT / "automations" / "24-camera-stale.yaml").read_text(encoding="utf-8"))
    [trigger] = doc["triggers"]
    assert trigger["entity_id"] == "binary_sensor.greenery_s_farm_camera_photos_stale"
    assert (trigger["from"], trigger["to"]) == ("off", "on")
    [action] = doc["actions"]
    assert action["action"] == "script.farm_alerts"
    assert not action["data"].get("critical")
    assert "stale_cameras" in action["data"]["message"]
    assert "hours=24" in doc["conditions"][0]["value_template"]


def test_cameras_card():
    card = yaml.safe_load((ROOT / "dashboard-cameras.yaml").read_text(encoding="utf-8"))
    grid, conditional = card["cards"]
    assert (grid["type"], grid["columns"], grid["square"]) == ("grid", 2, False)
    slug = fb.farm_slug
    assert [c["entity"] for c in grid["cards"]] == [
        f"camera.greenery_s_farm_{slug(n)}_camera" for _m, n in fb.CAMERA_FALLBACK.values()]
    for c in grid["cards"]:
        assert c["type"] == "picture-entity"
        assert c["show_state"] is False
        assert c["tap_action"]["action"] == "more-info"
    assert conditional["type"] == "conditional"
    assert conditional["conditions"][0]["entity"] == "binary_sensor.greenery_s_farm_camera_photos_stale"
    assert conditional["conditions"][0]["state"] == "on"

    controls = (ROOT / "dashboard-controls.yaml").read_text(encoding="utf-8")
    assert "navigation_path: farm-cameras" in controls
