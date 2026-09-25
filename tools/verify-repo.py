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
import re
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
    "README.md", "LICENSE", "requirements.txt",
    "CONTRIBUTING.md",
    "farm_bridge.py", "farm-bridge.env.example", "farm-bridge.service",
    "farm-alerts-script.yaml", "dashboard-controls.yaml",
    "watchdog-helpers.yaml",
    "farm-dashboard.yaml", "farm-dashboard-mobile.yaml",
    "tools/README.md", "tools/dump-relay.py", "tools/discover-farmhand-api.py",
    "tools/render-farm-yaml.py",
    "repository.yaml",
    "greenery-bridge/config.yaml", "greenery-bridge/Dockerfile",
    "greenery-bridge/run.sh", "greenery-bridge/farm_bridge.py",
    "greenery-bridge/requirements.txt", "greenery-bridge/DOCS.md",
    "greenery-bridge/CHANGELOG.md", "greenery-bridge/translations/en.yaml",
    # Retired Windows path, kept for reference and emergency fallback
    "legacy/windows-installer/README.md",
    "legacy/windows-installer/INSTALL-FARM-BRIDGE.bat",
    "legacy/windows-installer/UNINSTALL-FARM-BRIDGE.bat",
    "legacy/windows-installer/WINDOWS-INSTALL.md",
    "legacy/windows-installer/install/Install-FarmBridge.ps1",
    "legacy/windows-installer/install/Uninstall-FarmBridge.ps1",
    "legacy/windows-installer/install/README.md",
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
    ("INSTALL-FARM-BRIDGE.bat", "retired to legacy/windows-installer/"),
    ("UNINSTALL-FARM-BRIDGE.bat", "retired to legacy/windows-installer/"),
    ("install", "retired to legacy/windows-installer/install/"),
    ("WINDOWS-INSTALL.md", "retired to legacy/windows-installer/"),
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
        ('os.environ.get("FARM_NAME")', "farm name read from the environment"),
        ('LEGACY_FARM_NAME = "Greenery S Farm"', "unset farm name keeps today's device"),
        ('"name": FARM_NAME,', "device named after the farm"),
        ('"device": DEVICE_INFO,', "one shared device block"),
        ("MANUAL_CONTROL_CHANNELS = (", "pump switch channel allow-list"),
        ("/manual_control/available", "switches gated on Task Mode"),
        ('"availability_mode": "all"', "switch needs bridge AND Task Mode"),
        ("def handle_output_command", "pump switch command handler"),
        ("REFUSED - farm not in Task Mode", "Task Mode re-checked at command time"),
        ("OUTPUT_VERIFY_TIMEOUT", "switch result read back from the relay"),
        ("msg.retain", "retained commands never replayed"),
        ("_FARM_COMMAND_LOCK", "one farm command at a time"),
        ("run_farm_command(handle_control_command", "Task Mode buttons off paho's thread"),
        ("FARM_LOG_MONITORING", "/farm-monitoring investigation flag"),
    ],
    "farm-alerts-script.yaml": [
        ("notify.send_message", "durable notify ENTITY path"),
        ("notify.farm_phone", "the phone entity"),
        ("persistent_notification.create", "audit-trail fallback"),
        ("input_boolean.farm_alert_missed", "missed-alert recording"),
        ("channel: alarm_stream", "Android critical payload"),
        ("states('input_text.farm_name')", "farm name read for alert titles"),
    ],
    "watchdog-helpers.yaml": [
        ("input_text.farm_name", "Farm Name helper documented"),
        ("KNOWN ROUGH EDGE", "helper vs app option mismatch flagged"),
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
        ("Greenery-S#stable", "customer farms told to use the stable branch"),
        ("Never commit to or work from `stable`", "developers told to stay off stable"),
        ("Pump switches (Task Mode only)", "pump switch documentation"),
        ("farmhand's reply proves nothing", "the HTTP reply is not confirmation"),
        ("### Adding a channel", "physical test before a channel is added"),
    ],
    "CONTRIBUTING.md": [
        ("git merge --ff-only main", "stable only fast-forwards to main"),
        ("Never commit to `stable`", "stable is release-only"),
        ("CHANGELOG", "every release carries a changelog entry"),
    ],
    "greenery-bridge/run.sh": [
        ("bashio::services mqtt", "auto MQTT credentials from Supervisor"),
        ("No MQTT broker found", "clear failure message"),
        ("FARM_SSE_URL", "farm endpoint exported"),
        ("export FARM_NAME=\"$(bashio::config 'farm_name')\"", "farm name passed to the bridge"),
        ("FARM_CONTROL_URL", "Task Mode endpoint follows farm_host"),
        ("FARM_LOG_MONITORING", "monitoring investigation reachable from the app"),
    ],
    "greenery-bridge/config.yaml": [
        ("mqtt:want", "MQTT service declared"),
        ("aarch64", "HA Green architecture"),
        ("farm_name: Greenery S Farm", "legacy default - upgrades are not renamed"),
        ("farm_name: str(1,)", "farm name can never be blank"),
        ("log_farm_monitoring: false", "monitoring investigation off by default"),
    ],
    "greenery-bridge/translations/en.yaml": [
        ("farm_name:", "farm name is a labelled field"),
        ("log_farm_monitoring:", "monitoring option is a labelled field"),
    ],
    "greenery-bridge/DOCS.md": [
        ("`farm_name`", "farm name documented"),
        ("before the first start", "set-once instruction"),
        ("render-farm-yaml.py", "per-farm YAML step documented"),
        ("Greenery-S#stable", "customer install URL"),
        ("If either check fails", "fallback if branch selection does not work"),
        ("## Pump switches (Task Mode only)", "pump switches documented"),
        ("`log_farm_monitoring`", "monitoring option documented"),
    ],
    "tools/render-farm-yaml.py": [
        ("ENTITY_REF.subn", "entity IDs rewritten"),
        ("old entity IDs survived", "self-check of its own output"),
    ],
    "legacy/windows-installer/install/Install-FarmBridge.ps1": [
        ("Greenery S Farm Bridge", "scheduled task name"),
        ("Register-ScheduledTask", "startup task creation"),
        ("AsSecureString", "password not echoed"),
        ("SetAccessRuleProtection", "env file locked down"),
        ("Test-TcpPort", "connectivity self-test"),
        ("homeassistant", "rejects the mDNS name"),
        ('Join-Path $PSScriptRoot "..\\..\\.."', "finds the bridge files at the repo root"),
    ],
    "legacy/windows-installer/README.md": [
        ("Do not use this for a new install", "marked as retired"),
    ],
    "dashboard-controls.yaml": [
        ("confirmation:", "confirmation guard"),
        ("button.press", "correct action - not automation.trigger"),
        ("Pumps (Task Mode only)", "pump switches card"),
    ],
}


def _module_literal(tree, name):
    """The literal value assigned to a module-level name, or None."""
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == name):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None

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

    print("\n--- Every alert title carries the farm name ---")
    # Three delivery paths: persistent notification, notify entity, legacy
    # critical service. One missed means one banner with no farm on it.
    fa = root / "farm-alerts-script.yaml"
    if fa.is_file():
        t = fa.read_text(encoding="utf-8", errors="replace")
        n = t.count('title: "{{ alert_title }}"')
        check(n == 3, f"farm-alerts-script.yaml: {n}/3 delivery titles use alert_title",
              "a notification path skips the farm-name prefix")
        check('title: "{{ title }}"' not in t, "farm-alerts-script.yaml: no bare title left")

    print("\n--- Pump switches are guarded ---")
    # The bridge also refuses to start with a bad allow-list - but on a farm
    # that means no sensors and no alerts. Catch it here, before it ships.
    allowed = []
    try:
        tree = ast.parse((root / "farm_bridge.py").read_text(encoding="utf-8"))
        allowed = _module_literal(tree, "MANUAL_CONTROL_CHANNELS")
        excluded = _module_literal(tree, "TASK_MODE_EXCLUDE")
        output_map = _module_literal(tree, "OUTPUT_MAP")
        readable = None not in (allowed, excluded, output_map)
        check(readable, "MANUAL_CONTROL_CHANNELS, TASK_MODE_EXCLUDE, OUTPUT_MAP are literals")
        if readable:
            check(not set(allowed) & set(excluded),
                  f"MANUAL_CONTROL_CHANNELS {tuple(allowed)} has no TASK_MODE_EXCLUDE channel",
                  f"remove {sorted(set(allowed) & set(excluded))}")
            check(set(allowed) <= set(output_map), "every switchable channel is mapped")
    except (OSError, SyntaxError) as e:
        check(False, "farm_bridge.py readable for the allow-list check", str(e)[:70])
        allowed, output_map = [], {}

    # A switch shown without a confirmation toggles a pump on one pocket-tap.
    # Tiles also toggle on an ICON tap by default, so both actions need one.
    # A bare switch row in an entities card is a toggle with no confirmation.
    for rel in ["dashboard-controls.yaml", "farm-dashboard.yaml", "farm-dashboard-mobile.yaml"]:
        p = root / rel
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, yaml.YAMLError):
            continue   # reported by the parse checks
        text = p.read_text(encoding="utf-8", errors="replace")
        bare, unguarded, n = [], [], 0

        def walk(node):
            nonlocal n
            if isinstance(node, dict):
                ent = node.get("entity")
                if isinstance(ent, str) and ent.startswith("switch."):
                    n += 1
                    if not all(isinstance(node.get(a), dict) and node[a].get("confirmation")
                               for a in ("tap_action", "icon_tap_action")):
                        unguarded.append(ent)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    if isinstance(v, str) and v.startswith("switch."):
                        bare.append(v)
                    walk(v)

        walk(doc)
        if n or bare:
            check(not unguarded and not bare,
                  f"{rel}: every switch tap and icon tap asks for confirmation",
                  ", ".join(unguarded + bare))
        if rel == "dashboard-controls.yaml" and output_map:
            for ch in allowed:
                name = output_map[ch][0]
                ent = "switch.greenery_s_farm_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") + "_switch"
                check(ent in text, f"{rel}: tile for ch{ch} {name}", f"expected {ent}")

    print("\n--- YAML parses ---")
    bdir = root / "greenery-bridge"
    for p in (sorted(root.glob("*.yaml"))
              + sorted(adir.glob("*.yaml") if adir.is_dir() else [])
              + sorted(bdir.glob("*.yaml")) + sorted(bdir.glob("translations/*.yaml"))):
        try:
            yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
            check(True, p.relative_to(root).as_posix())
        except Exception as e:
            check(False, p.relative_to(root).as_posix(), str(e)[:70])

    print("\n--- Python parses ---")
    for rel in ["farm_bridge.py", "tools/dump-relay.py", "tools/discover-farmhand-api.py",
                "tools/render-farm-yaml.py"]:
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

    print("\n--- Release metadata in sync ---")
    # Supervisor offers an update only when config.yaml's version rises, and
    # the CHANGELOG is what the update dialog shows. A version with no entry,
    # or an entry with no version bump, is a release nobody can read or get.
    cfg, log_md = bdir / "config.yaml", bdir / "CHANGELOG.md"
    try:
        version = str(yaml.safe_load(cfg.read_text(encoding="utf-8"))["version"])
        top = next((ln[3:].strip() for ln in log_md.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("## ")), None)
        check(version == top,
              f"config.yaml version {version} is the top CHANGELOG entry",
              f"CHANGELOG starts at {top}")
    except (OSError, KeyError, TypeError, yaml.YAMLError) as e:
        check(False, "config.yaml version readable", str(e)[:70])

    print("\n--- Farm slug identical in bridge and render tool ---")
    # The render tool predicts the entity IDs the bridge's device name produces.
    # If the two slug functions disagree, rendered automations name entities
    # that do not exist - and a trigger on a missing entity never fires.
    bodies = []
    for rel in ["farm_bridge.py", "tools/render-farm-yaml.py"]:
        p = root / rel
        try:
            fn = next(n for n in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
                      if isinstance(n, ast.FunctionDef) and n.name == "farm_slug")
            bodies.append(ast.dump(ast.Module(body=fn.body[1:], type_ignores=[])))
        except (OSError, SyntaxError, StopIteration):
            bodies.append(None)
    check(None not in bodies and bodies[0] == bodies[1],
          "farm_slug() matches in farm_bridge.py and tools/render-farm-yaml.py",
          "they have DRIFTED - rendered YAML would reference the wrong entity IDs")

    print("\n--- Size sanity ---")
    fb = root / "farm_bridge.py"
    if fb.is_file():
        n = len(fb.read_text(encoding="utf-8", errors="replace").splitlines())
        check(n >= 1040, f"farm_bridge.py is {n} lines (expect ~1060)",
              "too short - a patch is probably missing")
    rm = root / "README.md"
    if rm.is_file():
        n = len(rm.read_text(encoding="utf-8", errors="replace").splitlines())
        check(n >= 1049, f"README.md is {n} lines (expect ~1069)",
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
