## Remote Access (Tailscale)

> Optional. The bridge, sensors, and alerts all work without this. Tailscale is a
> third-party VPN, not part of this project — this section exists because every
> Greenery S owner hits the same four gotchas, and three of them look identical to
> "the server is down."

Tailscale gives you encrypted access to Home Assistant from anywhere with no port
forwarding and no exposure to the public internet. Install the official **Tailscale**
add-on on your HA host and the Tailscale client on your phone and computers, all
signed into the same tailnet.

**Why this matters more here than in a normal home setup:** the Greenery S is a steel
shipping container. There is no cell signal inside it. Every remote-access path
depends on Wi-Fi, which means when something breaks you often cannot troubleshoot it
from inside the farm.

---

### Step 1 — Know your Home Assistant port

**Do not assume `8123`.**

| Install | Default port |
|---|---|
| Home Assistant OS, installed before 2026.8 | `8123` |
| Home Assistant OS, fresh install 2026.8+ | `80` |
| Home Assistant Container / Docker | `8123` |

As of HA 2026.8 the HTTP server port moved into the UI: **Settings → System → Network →
HTTP server**. Check it there and use that number everywhere below.

A known 2026.8 regression silently moved some *existing* HAOS installs from 8123 to 80
([core#177585](https://github.com/home-assistant/core/issues/177585)). If remote access
stopped working around an update, check this first — before touching Tailscale, your
firewall, or anything on the client.

If you change the port: **confirm the change in a browser immediately.** Home Assistant
rolls back to the previous port if it does not see a successful connection within a few
minutes. A `curl` test does not satisfy the confirmation.

---

### Step 2 — Use the Tailscale IP, not the LAN IP

Get your HA host's Tailscale address from `login.tailscale.com/admin/machines` or by
running `tailscale status` on any client. It will be in the `100.x.x.x` range.

| URL form | Works over Tailscale? |
|---|---|
| `http://100.x.x.x:PORT` | **Yes.** Use this. |
| `http://192.168.x.x:PORT` (LAN IP) | **No** — requires a subnet router. Not configured by default. |
| `http://homeassistant.local:PORT` | **No** — mDNS does not traverse the tunnel. |
| `http://homeassistant:PORT` (MagicDNS) | Only if MagicDNS is enabled in your tailnet DNS settings. |

Rows 2 and 3 fail as **silent timeouts**, which is indistinguishable from a dead server.

---

### Step 3 — Allow your HA user to log in remotely

**Settings → People → your user → disable "Can only log in from the local network" → Update.**

Tailscale addresses live in `100.64.0.0/10` (CGNAT space), which Home Assistant does
**not** classify as local. With this flag on, remote login fails with:

```
Error: Login blocked: User cannot authenticate remotely
```

That error means the network path is working — TCP connected, HA served the page, and
the auth layer rejected the login. It is an account setting, not a networking problem.

**Enable MFA on that account** (Settings → People → your user → security). You have just
removed a guardrail; replace it. Your HA instance is still not exposed to the public
internet — only to devices on your tailnet.

---

### Step 4 — Disable key expiry on the HA node

`login.tailscale.com/admin/machines` → HA host → **Disable key expiry**.

Tailscale node keys expire after 180 days by default. When the HA node's key expires,
every client loses access at once, with no notification and no error that points at the
cause. Do this during setup, not after it bites you.

---

### Step 5 — Companion App configuration

**Settings → Companion app → your server → Server settings**

| Field | Value |
|---|---|
| Home Assistant URL | `http://100.x.x.x:PORT` — the Tailscale address |
| Internal URL | `http://192.168.x.x:PORT` — the farm LAN address |
| Connect via SSID | **The farm Wi-Fi SSID only** |

The app uses the Internal URL when connected to a listed SSID, and the Home Assistant
URL everywhere else.

**The SSID list is the most common misconfiguration.** If your home or office Wi-Fi is
in that list, the app will try to reach the farm's LAN address from a network that
cannot route to it, and hang. The app offers to add whatever network you happen to be
on when you first connect — it is easy to accept that on the wrong network without
noticing. Remove everything except the farm SSID.

Symptom: **works on cellular, times out on Wi-Fi.** That is this bug, not a tunnel problem.

After changing URLs, force-stop and reopen the app. Changing the port or address changes
the origin, so the stored refresh token becomes invalid — expect to log out and back in
once. An auth error at this stage is progress, not a regression.

---

### Step 6 — Android: keep the tunnel alive

Android, and Samsung One UI in particular, will kill a backgrounded VPN client.

1. Settings → Apps → Tailscale → Battery → **Unrestricted**
2. Settings → Battery → Background usage limits → remove Tailscale from **Sleeping apps**
   and **Deep sleeping apps** (Samsung re-adds apps here on its own — re-check periodically)
3. Settings → Connections → More connection settings → VPN → Tailscale → **Always-on VPN: on**

Verify by checking that your phone shows **Connected** in the Tailscale admin console,
not a "last seen" timestamp.

---

### Troubleshooting: read the failure mode before you touch anything

Most time lost to remote-access problems is spent fixing the wrong layer. The exact
failure tells you which layer to look at. Test with `curl`, not a browser — browsers
hide the distinction.

```bash
curl -v --connect-timeout 5 http://100.x.x.x:8123/
```

| Result | What it means | Where to look |
|---|---|---|
| `200 OK` | Working | Nothing wrong at this layer |
| **`Connection refused`** | Host reached you back. Nothing is listening on that port. | **Wrong port.** Step 1. Not a firewall — firewalls drop, they do not reply. |
| **`Connection timed out`** | Packets went nowhere | Wrong address (Step 2), tunnel down (Step 6), or client not connected |
| `405 Method Not Allowed` | You sent `HEAD`. HA's root path only accepts `GET`. | Nothing — the server is up. Drop `-I`. |
| `000` (with `-w "%{http_code}"`) | No HTTP response at all | Same as refused or timed out — re-run with `-v` to see which |
| `Login blocked: User cannot authenticate remotely` | Network is fine, HA is serving, auth rejected the login | Step 3 |

**The single most useful distinction: refused is not blocked.** A connection refusal is a
TCP reset sent *by the destination host* — the host is up and reachable, and nothing is
bound to that port. Client-side firewalls, antivirus, and VPN conflicts produce silence,
not refusals. If you see "refused," the client is not the problem.

**Isolate one layer at a time:**

1. From the LAN, `curl` the HA LAN address. Fails → the problem is Home Assistant, and
   Tailscale is not involved.
2. From a remote client, `curl` the Tailscale address. Fails → check the tunnel:
   `tailscale status` on the client, and the node's status in the admin console.
3. From the phone's **browser**, load the Tailscale URL. Works there but not in the
   Companion App → the problem is app configuration (Step 5), not the network.

**"Tailscale shows connected" only means your client authenticated to Tailscale's
coordination server.** It says nothing about whether the peer you want is online,
authorized, or reachable. Check the peer's status in the admin console, not your own
client's.
