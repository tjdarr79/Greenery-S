"""
Read-only camera dump: find out how farmhand delivers its camera photos and save them.

Sends ONLY what farmhand's own page sends every time it loads:
    read_cameras, get_last_images_local_only
and listens on farmhand's photo stream (/farm-images) and reply stream
(/farm-monitoring). Nothing here changes a setting or moves a camera.

Usage (on hydro, farm LAN):
    py -3 dump_cameras.py
Output:
    results\\cameras\\<date-time>\\   photos found, plus events.json (what each message looked like)
"""
import base64
import json
import re
import socket
import threading
import time
import urllib.request
from pathlib import Path

HOST = "192.168.200.200"
PORT = 3001
LISTEN_SECONDS = 45
MAX_BYTES_PER_STREAM = 80_000_000
COMMANDS = ({"command": "read_cameras"}, {"command": "get_last_images_local_only"})

OUT = Path(__file__).parent / "results" / "cameras" / time.strftime("%Y%m%d-%H%M%S")
events = []          # (stream, t, parsed-or-text)
stop = threading.Event()
lock = threading.Lock()


def listen(path):
    total = 0
    try:
        req = urllib.request.Request(f"http://{HOST}:{PORT}{path}",
                                     headers={"Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            buf = []
            while not stop.is_set():
                try:
                    line = resp.readline()
                except (socket.timeout, TimeoutError):
                    continue
                if not line:
                    break
                total += len(line)
                if total > MAX_BYTES_PER_STREAM:
                    break
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    buf.append(line[5:].strip())
                elif line == "" and buf:
                    text = "\n".join(buf)
                    buf = []
                    try:
                        body = json.loads(text)
                    except json.JSONDecodeError:
                        body = text
                    with lock:
                        events.append((path, time.time(), body))
    except OSError as e:
        with lock:
            events.append((path, time.time(), {"_error": str(e)}))


def post(cmd):
    req = urllib.request.Request(f"http://{HOST}:{PORT}/farm-control",
                                 data=json.dumps(cmd).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")[:100]


IMG_SIG = {"/9j/": "jpg", "iVBOR": "png"}
URL_RE = re.compile(r"\.(jpe?g|png)(\?|$)", re.I)


def walk(obj, path, found_imgs, found_urls):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}" if path else str(k), found_imgs, found_urls)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]", found_imgs, found_urls)
    elif isinstance(obj, str):
        s = obj
        if s.startswith("data:image"):
            s = s.split(",", 1)[-1]
        for sig, ext in IMG_SIG.items():
            if s.startswith(sig) and len(s) > 1000:
                found_imgs.append((path, ext, s))
                return
        if len(obj) < 500 and URL_RE.search(obj):
            found_urls.append((path, obj))


def shrink(obj):
    """Copy of a message with long strings replaced by their length, for events.json."""
    if isinstance(obj, dict):
        return {k: shrink(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shrink(v) for v in obj[:50]]
    if isinstance(obj, str) and len(obj) > 300:
        return f"<{len(obj)} chars: {obj[:40]}...>"
    return obj


def safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80] or "img"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for p in ("/farm-images", "/farm-monitoring"):
        threading.Thread(target=listen, args=(p,), daemon=True).start()
    time.sleep(2)
    for c in COMMANDS:
        try:
            print(f"Sent {c['command']} -> {post(c)}")
        except OSError as e:
            print(f"Sent {c['command']} -> ERROR {e}")
        time.sleep(0.5)

    print(f"Listening {LISTEN_SECONDS}s for photos...")
    time.sleep(LISTEN_SECONDS)
    stop.set()
    time.sleep(1)

    saved, urls, summary = [], [], []
    with lock:
        snap = list(events)
    for i, (stream, t, body) in enumerate(snap):
        imgs, found = [], []
        walk(body, "", imgs, found)
        for path, ext, b64 in imgs:
            try:
                data = base64.b64decode(b64 + "=" * (-len(b64) % 4))
            except ValueError:
                continue
            f = OUT / f"{i:03d}_{safe(path)}.{ext}"
            f.write_bytes(data)
            saved.append((f.name, len(data)))
        urls += found
        rt = body.get("response_to") if isinstance(body, dict) else None
        summary.append({"stream": stream, "t": t, "response_to": rt, "message": shrink(body)})

    # If photos are served as files, fetch a few (plain GETs, read-only).
    for path, u in urls[:12]:
        full = u if u.startswith("http") else f"http://{HOST}:{PORT}/{u.lstrip('/')}"
        try:
            with urllib.request.urlopen(full, timeout=10) as r:
                data = r.read()
            f = OUT / f"url_{safe(path)}{Path(u.split('?')[0]).suffix or '.jpg'}"
            f.write_bytes(data)
            saved.append((f.name, len(data)))
        except OSError as e:
            print(f"  could not fetch {full}: {e}")

    (OUT / "events.json").write_text(json.dumps(
        {"events": summary, "image_urls": urls}, indent=2), encoding="utf-8")

    by_stream = {}
    for s in summary:
        by_stream[s["stream"]] = by_stream.get(s["stream"], 0) + 1
    print(f"\nMessages: {by_stream}")
    print(f"Replies: {[s['response_to'] for s in summary if s['response_to']]}")
    print(f"Photos saved: {len(saved)}")
    for name, n in saved:
        print(f"  {name}  ({n // 1024} KB)")
    print(f"\nFolder: {OUT}")
    if not saved:
        print("No photos found -- tell me and I'll read events.json.")


if __name__ == "__main__":
    main()
