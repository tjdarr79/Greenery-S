# Greenery S → Home Assistant Bridge — Windows Setup Guide

Takes local sensor data from a Freight Farms Greenery S and pipes it into
Home Assistant, with dashboard panels and trend graphs. No dependency on
Farmhand's cloud dashboard for monitoring.

**Everything in this guide assumes the folder `C:\Farm`.** Follow it
exactly and every command below will work as-is, copy-paste, no editing
required except your own credentials.

---

## What you need before starting

- A Windows PC that stays on and connected to the same network as your
  Greenery S hub controller (the "farm PC")
- Home Assistant already running, with the MQTT integration installed
  and a working broker login (see **Step 4 gotchas** if you're not sure)
- Administrator access on the Windows PC

---

## Step 1 — Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/) and
   download the latest Windows installer.
2. Run it. **On the very first screen, check the box "Add python.exe to
   PATH"** before clicking Install. This is the single most common setup
   failure — skipping it means every command below fails with
   `'python' is not recognized`.
3. Confirm it worked. Open Command Prompt and run:
   ```cmd
   python --version
   ```
   If that fails, try:
   ```cmd
   py --version
   ```
   If `py` works but `python` doesn't, use `py` in place of `python` in
   every command in this guide.

---

## Step 2 — Create the folder and add the files

1. Create the folder:
   ```cmd
   mkdir C:\Farm
   ```
2. Download these files from the repo and place them directly inside
   `C:\Farm` (not in a subfolder):
   - `farm_bridge.py`
   - `requirements.txt`
   - `farm-bridge.env.example`
   - `farm-dashboard.yaml`

3. Confirm they landed in the right place:
   ```cmd
   dir C:\Farm
   ```
   You should see all four files listed.

---

## Step 3 — Install dependencies

```cmd
cd C:\Farm
pip install -r requirements.txt
```

If `pip` isn't recognized, use:
```cmd
py -m pip install -r requirements.txt
```

Do **not** add `--break-system-packages` on Windows — that flag is
Linux-only and will throw an error here.

---

## Step 4 — Set up your credentials

1. In `C:\Farm`, copy `farm-bridge.env.example` and rename the copy to
   `farm-bridge.env`.
2. Open `farm-bridge.env` in Notepad and fill in three values:
   ```
   FARM_MQTT_HOST=<your Home Assistant IP address>
   FARM_MQTT_USER=<your MQTT broker username>
   FARM_MQTT_PASS=<your MQTT broker password>
   ```
3. Save and close.

**The script reads this file automatically — no `set` or `setx` commands
needed for credentials.**

### Gotchas that will cost you an hour if you skip reading this

- **Your MQTT login is NOT automatically your Home Assistant login.**
  Find the real one in Home Assistant: **Settings → Add-ons → Mosquitto
  broker → Configuration tab**, under `logins:`. If that section is
  empty, either add a login there or check whether "Login with Home
  Assistant" is enabled — if it's off, your HA frontend password will be
  rejected (error code 5, "not authorized").
- **Don't use `homeassistant.local` for `FARM_MQTT_HOST`.** Use the
  actual IP address (Settings → System → Network in HA). The `.local`
  hostname depends on mDNS working correctly and can fail silently when
  this script runs unattended.
- **If your Farmhand hub's local IP isn't `192.168.200.200`,** add this
  line to `farm-bridge.env` as well:
  ```
  FARM_SSE_URL=http://<your hub IP>:3001/farm-data
  ```

---

## Step 5 — Test it manually before automating anything

```cmd
cd C:\Farm
python farm_bridge.py
```

You should see, within a few seconds:
```
MQTT connected: Connected successfully
Published discovery config for Cultivation pH
... (13 total lines)
Connecting to SSE stream: http://192.168.200.200:3001/farm-data
```

If you see `MQTT connection FAILED: Refused - not authorized`, go back
to Step 4 — your credentials are wrong, not the script.

Leave it running for a minute or two, then check Home Assistant:
**Settings → Devices & Services → MQTT → Devices → "Greenery S Farm."**
Confirm all entities show real numbers, not "unknown."

Once confirmed working, press `Ctrl+C` to stop it — you'll automate it
in the next step.

---

## Step 6 — Run it automatically, every time the PC boots

This uses Windows Task Scheduler so the bridge survives reboots and you
don't have to keep a terminal window open.

1. Find your Python interpreter's exact path:
   ```cmd
   where python
   ```
   Copy the full path it prints (something like
   `C:\Users\<you>\AppData\Local\Python\bin\python.exe`).

2. Open Command Prompt **as Administrator** (right-click Command Prompt
   → "Run as administrator"), then run — substituting your real path
   from step 1:
   ```cmd
   schtasks /create /tn "FarmBridge" /tr "\"C:\path\to\python.exe\" \"C:\Farm\farm_bridge.py\"" /sc onstart /ru SYSTEM /rl highest
   ```

3. Test it immediately without rebooting:
   ```cmd
   schtasks /run /tn "FarmBridge"
   ```
   Check Task Manager → Details tab for a running `python.exe` process.
   Check Home Assistant for fresh, updating sensor values.

4. **Do a real reboot test.** Restart the PC, don't log in, wait two
   minutes, then check Home Assistant from another device. If the
   entities are updating with nobody logged into the farm PC, this step
   is done correctly.

### If Task Scheduler errors "cannot find the file specified"

This means SYSTEM can't see your user-level Python install (common with
per-user installs under `AppData\Local`). Fix: delete the task and
recreate it without `/ru SYSTEM`, letting it run under your own account
instead — Task Scheduler will prompt for your Windows password to store
for unattended runs:
```cmd
schtasks /delete /tn "FarmBridge" /f
schtasks /create /tn "FarmBridge" /tr "\"C:\path\to\python.exe\" \"C:\Farm\farm_bridge.py\"" /sc onstart /rl highest
```

---

## Step 7 — Add the dashboard

1. In Home Assistant: **Settings → Dashboards → + Add Dashboard → New
   dashboard from scratch.**
2. Open the new dashboard, click the three-dot menu (top right) →
   **Edit Dashboard** → three-dot menu again → **Edit in YAML.**
3. Delete everything in the editor and paste the full contents of
   `farm-dashboard.yaml`.
4. Save.

**If any entity shows "entity not found":** Home Assistant sometimes
renames entities by prefixing the device name (e.g.
`sensor.greenery_s_farm_cultivation_ph` instead of
`sensor.cultivation_ph`). Check **Settings → Devices & Services →
Entities**, filter by "greenery," and match the exact IDs shown there
into the YAML.

---

## Step 8 — Set up phone alerts (optional but recommended)

Six alert automations live in the repo's `automations/` folder — covering
bridge disconnection, pH/EC out of range, low CO2 (empty tank), and high
temp/humidity. See the main `README.md`'s "Alert automations" section for
full install and testing steps — they install through Home Assistant's
UI, not through anything on the farm PC.

---

## You're done when

- `C:\Farm` contains all four files plus your filled-in `farm-bridge.env`
- Task Scheduler shows the "FarmBridge" task, and it survives a real
  reboot with nobody logged in
- Home Assistant's dashboard shows three live panels (Environmental,
  Nursery, Cultivation) plus trend graphs, all updating in real time
- The "Bridge Status" indicator at the top of the dashboard shows online
