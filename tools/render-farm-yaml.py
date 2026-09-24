#!/usr/bin/env python3
"""
render-farm-yaml.py - produce this repo's Home Assistant YAML for one farm.

    python tools/render-farm-yaml.py "Smith Farm"
    python tools/render-farm-yaml.py "Smith Farm" --out C:\\Users\\Travis\\smith
    python tools/render-farm-yaml.py "Smith Farm" --slug smith_farm

WHY THIS EXISTS

The automations, dashboards and controls card name their entities literally:
sensor.greenery_s_farm_cultivation_ph. Home Assistant builds those IDs from
the bridge's device name, which is the app's farm_name option. A farm named
"Smith Farm" gets sensor.smith_farm_cultivation_ph instead - and an automation
whose trigger names an entity that does not exist never fires and never
errors. The notification test in README step 3 cannot catch it either, because
it tests the script, not the triggers.

So every farm not named "Greenery S Farm" pastes the files this writes, never
the originals in the repo.

WHAT IT DOES

Copies every HA-side YAML file into rendered/<slug>/ (same layout), rewriting
  - <domain>.greenery_s_farm_<x>  ->  <domain>.<slug>_<x>
  - dashboard titles              ->  the farm name
It then re-reads its own output and fails if any old entity ID survived.

Writes only under the output folder; never touches the repo's own files.
Standard library only. PyYAML, if installed, is used to parse-check the output.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

LEGACY_FARM_NAME = "Greenery S Farm"
LEGACY_SLUG = "greenery_s_farm"

# HA-side files: pasted or created in the Home Assistant UI, not run by the
# bridge. repository.yaml is Supervisor metadata and is not one of them.
ROOT_FILES = [
    "farm-alerts-script.yaml", "watchdog-helpers.yaml",
    "farm-dashboard.yaml", "farm-dashboard-mobile.yaml",
    "dashboard-controls.yaml",
]

ENTITY_REF = re.compile(r"\b([a-z_]+)\." + LEGACY_SLUG + r"_")
DASHBOARD_TITLE = re.compile(r"^(title:[ \t]*)" + re.escape(LEGACY_FARM_NAME) + r"(.*)$",
                             re.MULTILINE)


def farm_slug(name: str) -> str:
    """The prefix Home Assistant will give this farm's entity IDs.

    Mirrors HA's slugify for Latin-script names: accents folded to ASCII, every
    other run of non-alphanumerics collapsed to one underscore. Keep identical
    to the copy in farm_bridge.py - verify-repo.py checks it.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")


def render(text: str, name: str, slug: str):
    text, n_ids = ENTITY_REF.subn(lambda m: f"{m.group(1)}.{slug}_", text)
    # json.dumps gives a double-quoted string, which is valid YAML whatever
    # punctuation the farm name contains.
    text, n_titles = DASHBOARD_TITLE.subn(
        lambda m: m.group(1) + json.dumps(name + m.group(2)), text)
    return text, n_ids, n_titles


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("farm_name", help='exactly as typed in the app\'s Farm name option, e.g. "Smith Farm"')
    ap.add_argument("--slug", help="entity ID prefix, if HA's differs from the one predicted "
                                   "(check an entity ID in HA first)")
    ap.add_argument("--out", help="output folder (default: rendered/<slug> in the repo)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    name = args.farm_name.strip()
    slug = args.slug or farm_slug(name)
    if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", slug or ""):
        print(f"Cannot make an entity ID prefix from {args.farm_name!r}. "
              "Use letters, numbers and spaces, or pass --slug.")
        return 2

    out = Path(args.out).resolve() if args.out else root / "rendered" / slug
    if (root / "automations") in [out, *out.parents]:
        print("Refusing to write inside automations/ - verify-repo.py would "
              "flag the output as unexpected automation files.")
        return 2

    sources = [root / f for f in ROOT_FILES] + sorted((root / "automations").glob("*.yaml"))
    missing = [s.relative_to(root).as_posix() for s in sources if not s.is_file()]
    if missing:
        print("Missing from the repo: " + ", ".join(missing))
        print("Run tools/verify-repo.py first.")
        return 1

    try:
        import yaml
    except ImportError:
        yaml = None

    print(f"Farm name : {name}")
    print(f"Entity IDs: <domain>.{slug}_*   e.g. sensor.{slug}_cultivation_ph")
    print(f"Output    : {out}\n")

    total_ids = 0
    ok = True
    for src in sources:
        rel = src.relative_to(root)
        text, n_ids, n_titles = render(src.read_text(encoding="utf-8"), name, slug)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        total_ids += n_ids

        problems = []
        written = dest.read_text(encoding="utf-8")
        if slug != LEGACY_SLUG and ENTITY_REF.search(written):
            problems.append("old entity IDs survived")
        if yaml is not None:
            try:
                yaml.safe_load(written)
            except yaml.YAMLError as e:
                problems.append(f"YAML does not parse: {str(e)[:60]}")
        ok = ok and not problems
        detail = f"{n_ids} entity IDs" + (f", {n_titles} title" if n_titles else "")
        print(f"  {'FAIL' if problems else 'ok  '}  {rel.as_posix():<42} {detail}"
              + (f"  ({'; '.join(problems)})" if problems else ""))

    print(f"\n{total_ids} entity references rewritten across {len(sources)} files.")
    if yaml is None:
        print("(PyYAML not installed - output was not parse-checked.)")
    if not ok:
        print("\nFAILURES ABOVE. Do not paste these files.")
        return 1

    if slug == LEGACY_SLUG:
        print("\nThis is the default name: the output is identical to the repo files.")
    print(f"""
BEFORE PASTING, confirm the prefix in Home Assistant:
  Settings -> Tools -> States, search "{slug}_farm_bridge_status".
  If it is not there, find the real Farm Bridge Status ID and re-run with
  --slug <its prefix>.

THEN:
  - Set the Farm Name helper (input_text.farm_name) to: {name}
  - Paste everything from {out}
    in the order README.md "New install" gives - not the repo originals.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
