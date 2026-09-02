#!/usr/bin/env python3
"""
discover-farmhand-api.py — find the farmhand local app's API routes.

    python discover-farmhand-api.py

STRICTLY READ-ONLY. Issues GET requests only. Never POSTs, never changes farm
state. Safe to run on a live farm at any time.

Fetches the app's HTML and JavaScript bundles and greps them for anything that
looks like an API route or a mode/task-mode call, so we can find the write
endpoint without guessing at URLs.
"""

import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://192.168.200.200:3001"
TIMEOUT = 20

INTERESTING = re.compile(
    r"(task[_-]?mode|set[_-]?mode|/api/[a-z0-9_/-]+|farm[_-]?data|"
    r"\"(?:POST|PUT|PATCH)\"|method:\s*[\"'](?:POST|PUT|PATCH)[\"']|"
    r"output_\d+|manual|auto_mode|command)",
    re.IGNORECASE,
)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "farmhand-discovery/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def main():
    print(f"Probing {BASE}  (GET only - nothing is changed)\n")

    try:
        html = get(BASE + "/")
    except urllib.error.URLError as e:
        print(f"Could not reach {BASE} - {e.reason}")
        print("Run this on a machine on the farm network.")
        return 1

    print(f"Root page: {len(html)} bytes\n")

    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I)
    srcs += re.findall(r'<link[^>]+href=["\']([^"\']+\.js)["\']', html, re.I)
    print(f"Found {len(srcs)} script reference(s):")
    for s in srcs:
        print("   ", s)
    print()

    # Routes visible in the HTML itself
    hits = {m.group(0) for m in INTERESTING.finditer(html)}
    if hits:
        print("--- matches in root HTML ---")
        for h in sorted(hits):
            print("   ", h)
        print()

    for src in srcs:
        url = urllib.parse.urljoin(BASE + "/", src)
        try:
            js = get(url)
        except Exception as e:
            print(f"[skip] {url}: {e}")
            continue

        found = {m.group(0) for m in INTERESTING.finditer(js)}
        print(f"--- {url}  ({len(js)} bytes, {len(found)} matches) ---")
        for h in sorted(found)[:60]:
            print("   ", h)
        if len(found) > 60:
            print(f"    ... and {len(found) - 60} more")
        print()

    print("=" * 60)
    print("Paste this whole output back to Claude.")
    print("Look especially for anything with /api/ plus 'mode' or 'task'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
