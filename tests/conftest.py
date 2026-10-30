import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "farmhand"
sys.path.insert(0, str(ROOT))

import farm_bridge as fb  # noqa: E402


class FakeMqtt:
    """Records what the bridge publishes instead of sending it."""

    def __init__(self):
        self.published = []
        self.subscribed = []
        self.connected = True

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)

    def topics(self):
        return [t for t, *_ in self.published]

    def payloads(self, topic):
        return [p for t, p, *_ in self.published if t == topic]


@pytest.fixture
def mqtt():
    return FakeMqtt()


@pytest.fixture(autouse=True)
def fresh_bridge_state(monkeypatch):
    """Every test starts like a fresh bridge with nothing from farmhand yet."""
    monkeypatch.setattr(fb, "_CAM", fb._CameraState())
    monkeypatch.setattr(fb, "LATEST_FH_SETTINGS", {})
    yield


def load_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sse_blocks(name):
    """The fixture's raw SSE events, each `data: ...` line plus its blank line."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [block + "\n\n" for block in text.split("\n\n") if block.strip()]


def image_events():
    return [json.loads(b[len("data:"):]) for b in sse_blocks("farm_images_sample.sse")]
