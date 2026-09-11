#!/usr/bin/env python3
"""
verify-repo.py - confirm a Greenery-S clone is complete and correct.

    python verify-repo.py                     (run from inside the repo)
    python verify-repo.py C:\\Users\\Travis\\Documents\\GitHub\\Greenery-S

READ-ONLY. Changes nothing. Exits 0 if everything passes, 1 if not.

Checks file presence, content markers, YAML validity, Python syntax, and the
absence of stale files - not byte-exact hashes, so Windows CRLF line endings
do not cause false failures.

Run this before telling anyone the repo is ready.
"""

import ast
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Needs PyYAML:  pip install pyyaml")
    sys.exit(2)

# ---------------------------------------------------------------------------
# Expected state
# ---------------------------------------------------------------------------

REQUIRED = [
    "README.md", "LICENSE", "WINDOWS-INSTALL.md", "requirements.txt",
    "farm_bridge.py", "farm-bridge.env.example", "farm-bridge.service",
    "farm-alerts-script.yaml", "dashboard-controls.yaml",
    "watchdog-helpers.yaml",
    "farm-dashboard.yaml", "farm-dashboard-mobile.yaml",
    "tools/README.md", "tools/dump-relay.py", "tools/discover-farmhand-api.py",
    "INSTALL-FARM-BRIDGE.bat", "UNINSTALL-FARM-BRIDGE.bat",
    "repository.yaml",
    "greenery-bridge/config.yaml", "greenery-bridge/Dockerfile",
    "greenery-bridge/run.sh", "greenery-bridge/farm_bridge.py",
    "greenery-bridge/requirements.txt", "greenery-bridge/DOCS.md",
    "greenery-bridge/CHANGELOG.md",
    "install/Install-FarmBridge.ps1", "install/Uninstall-FarmBridge.ps1",
    "install/README.md",
]

AUTOMATIONS = [
    "01-bridge-offline", "02-ph-out-of-range", "03-ec-critical",
    "04-co2-below-minimum", "05-air-temp-high", "06-humidity-high",
    "07-water-temp-high", "08-ec-low", "09-co2-high", "10-tank-depth-low",
    "11-recirc-pump-stopped", "12-task-mode-left-on",
    "13-equipment-not-responding", "14-cloudgate-watchdog",
    "15-missed-alerts-replay", "16-module-offline-critical",
    "17-module-offline-dosing", "18-air-temp-low",
    "19-hvac-cooling-stuck", "20-send-pump-pressure",
]

# Stale files that should have been removed or moved
SHOULD_NOT_EXIST = [
    ("notify-group.yaml", "superseded by farm-alerts-script.yaml"),
    ("dump-relay.py", "moved to tools/dump-relay.py"),
    ("discover-farmhand-api.py", "moved to tools/"),
    ("apply_relay_patch.py", "one-time migration, must not ship"),
    ("apply_control_patch.py", "one-time migration, must not ship"),
    ("relay_patch.py", "superseded - farm_bridge.py already contains it"),
    ("_READ-ME-FIRST.txt", "packaging note, not part of the repo"),
    ("greenery-alarm-fix.bundle", "transfer artifact, not part of the repo"),
]

# Content that proves each feature actually landed
MARKERS = {
    "farm_bridge.py": [
        ("OUTPUT_MAP = {", "relay channel map"),
        ("TASK_MODE_EXCLUDE", "ch23 exclusion from Task Mode detection"),
        ("publish_binary_discovery", "binary sensor discovery"),
        ("publish_binary_states", "binary sensor publishing"),
        ("publish_button_discovery", "Task Mode button discovery"),
        ("handle_control_command", "Task Mode command handler"),
        ("farm-control", "farmhand control endpoint"),
        ("client.on_message = on_message", "MQTT command subscription wired"),
        ("LATEST_MODES", "mode cache used for command verification"),
        ("Nursery Recirc and Chiller Pump", "ch8 chiller interlock named"),
        ("MODULE_MAP = {", "module offline map"),
        ("MODULE_STALE_SECONDS", "5-minute staleness definition"),
        ("publish_module_states", "module offline publishing"),
        ("publish_module_discovery", "module offline discovery"),
    ],
    "farm-alerts-script.yaml": [
        ("notify.send_message", "durable notify ENTITY path"),
        ("notify.farm_phone", "the phone entity"),
        ("persistent_notification.create", "audit-trail fallback"),
        ("input_boolean.farm_alert_missed", "missed-alert recording"),
        ("channel: alarm_stream", "Android critical payload"),
    ],
    "README.md": [
        ("New install", "fresh-install path"),
        ("What is in this repo", "file inventory"),
        ("/config/tools/", "HA 2026.2 navigation paths"),
        ("Output board mapping", "relay documentation"),
        ("Task Mode control", "control documentation"),
        ("Single point of failure: CloudGate", "the CloudGate dependency, stated"),
        ("Module offline detection", "module offline documentation"),
        ("Coverage against farmhand", "built-in alert parity table"),
        ("run it on Home Assistant itself", "add-on install path"),
        ("MQTT \u2192 Greenery S Farm", "where entities actually live"),
    ],
    "greenery-bridge/run.sh": [
        ("bashio::services mqtt", "auto MQTT credentials from Supervisor"),
        ("No MQTT broker found", "clear failure message"),
        ("FARM_SSE_URL", "farm endpoint exported"),
    ],
    "greenery-bridge/config.yaml": [
        ("mqtt:want", "MQTT service declared"),
        ("aarch64", "HA Green architecture"),
    ],
    "install/Install-FarmBridge.ps1": [
        ("Greenery S Farm Bridge", "scheduled task name"),
        ("Register-ScheduledTask", "startup task creation"),
        ("AsSecureString", "password not echoed"),
        ("SetAccessRuleProtection", "env file locked down"),
        ("Test-TcpPort", "connectivity self-test"),
        ("homeassistant", "rejects the mDNS name"),
    ],
    "dashboard-controls.yaml": [
        ("confirmation:", "confirmation guard"),
        ("button.press", "correct action - not automation.trigger"),
    ],
}

ok = True
def check(passed, label, detail=""):
    global ok
    if not passed:
        ok = False
    print(f"  {'PASS' if passed else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not passed else ""))


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 2

    print(f"Verifying: {root}\n")

    print("--- Required files ---")
    for rel in REQUIRED:
        check((root / rel).is_file(), rel)

    print("\n--- Automations (expect 15) ---")
    adir = root / "automations"
    for name in AUTOMATIONS:
        check((adir / f"{name}.yaml").is_file(), f"automations/{name}.yaml")
    if adir.is_dir():
        extra = sorted(p.name for p in adir.glob("*.yaml")
                       if p.stem not in AUTOMATIONS)
        if extra:
            check(False, "no unexpected automation files", ", ".join(extra))
        else:
            check(True, "no unexpected automation files")

    print("\n--- Stale files removed ---")
    for rel, why in SHOULD_NOT_EXIST:
        check(not (root / rel).exists(), f"{rel} absent", why)

    print("\n--- Feature markers ---")
    for rel, markers in MARKERS.items():
        f = root / rel
        if not f.is_file():
            check(False, f"{rel} (missing, cannot check markers)")
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for needle, label in markers:
            check(needle in text, f"{rel}: {label}")

    print("\n--- No automation names a phone directly ---")
    if adir.is_dir():
        for p in sorted(adir.glob("*.yaml")):
            t = p.read_text(encoding="utf-8", errors="replace")
            check("script.farm_alerts" in t, f"{p.name} routes via script")
            check("notify.mobile_app" not in t, f"{p.name} has no hardcoded phone")

    print("\n--- YAML parses ---")
    for p in sorted(root.glob("*.yaml")) + sorted(adir.glob("*.yaml") if adir.is_dir() else []):
        try:
            yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
            check(True, p.relative_to(root).as_posix())
        except Exception as e:
            check(False, p.relative_to(root).as_posix(), str(e)[:70])

    print("\n--- Python parses ---")
    for rel in ["farm_bridge.py", "tools/dump-relay.py", "tools/discover-farmhand-api.py"]:
        p = root / rel
        if not p.is_file():
            check(False, f"{rel} (missing)")
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            check(True, rel)
        except SyntaxError as e:
            check(False, rel, f"line {e.lineno}: {e.msg}")

    print("\n--- Add-on bridge copy matches root ---")
    a = root / "farm_bridge.py"
    b = root / "greenery-bridge" / "farm_bridge.py"
    if a.is_file() and b.is_file():
        same = (a.read_bytes().replace(b"\r\n", b"\n") ==
                b.read_bytes().replace(b"\r\n", b"\n"))
        check(same, "greenery-bridge/farm_bridge.py is identical to the root copy",
              "they have DRIFTED - copy the root one over it")
    else:
        check(False, "both copies of farm_bridge.py present")

    print("\n--- Size sanity ---")
    fb = root / "farm_bridge.py"
    if fb.is_file():
        n = len(fb.read_text(encoding="utf-8", errors="replace").splitlines())
        check(n >= 721, f"farm_bridge.py is {n} lines (expect ~741)",
              "too short - a patch is probably missing")
    rm = root / "README.md"
    if rm.is_file():
        n = len(rm.read_text(encoding="utf-8", errors="replace").splitlines())
        check(n >= 892, f"README.md is {n} lines (expect ~912)",
              "too short - doc updates missing")

    print("\n" + "=" * 62)
    if ok:
        print("ALL CHECKS PASSED - safe to tell people the repo is ready.")
    else:
        print("FAILURES ABOVE. Do not announce yet.")
        print("Send this output to Claude and it will tell you what is missing.")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
