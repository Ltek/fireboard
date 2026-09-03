# FireBoard Integration

A Home Assistant **custom integration** for [FireBoard](https://www.fireboard.com/) wireless thermometers and the FireBoard Drive fan controller. It surfaces every probe temperature, battery/WiFi/device diagnostic, cook session, and Drive metric the FireBoard cloud API exposes — with live-adjustable polling and optional Drive control, all as entities (no YAML).

Pulls all data from the **FireBoard cloud REST API** (`https://fireboard.io/api/v1`) using your account credentials.

---

## Requirements

- A **FireBoard account** (email + password) with at least one registered device.
- Home Assistant with outbound internet access to `fireboard.io`.
- For **On Network** detection only: the OS `ping` binary (standard on HAOS / Container).

---

## Installation

### Via HACS (recommended)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Ltek&repository=fireboard&category=integration)

1. Click the badge above (adds this repo to HACS), or in HACS → **⋮ → Custom repositories** add `https://github.com/Ltek/fireboard` as an **Integration**.
2. Install **FireBoard**, then restart Home Assistant.

### Manual
1. Copy `custom_components/fireboard/` into your Home Assistant `config/custom_components/` folder.
2. Restart Home Assistant.

### Set up
3. **Settings → Devices & Services → Add Integration → FireBoard**, enter email + password.
4. (Optional) On the second setup screen, enter each device's LAN IP — or skip and add it later.
5. Choose which optional entities to enable, then finish.

Options are changed anytime via **Settings → Devices & Services → FireBoard → Configure**, or directly from the entities below.

---

## How it works

**Polling.** One `devices.json` call returns *all* devices with *all* nested data (every probe, battery, WiFi, onboard temp, model, firmware) — so a single poll refreshes almost everything and counts as **one API request** regardless of how many entities you have. FireBoard **Drive** data is the exception: it comes from a separate `drivelog.json` call **per device**. Cook sessions come from `sessions.json` (fetched on the same schedule, no extra per-entity cost). The API allows ~**200 requests/hour**; at the 40 s default that's ~90/hour for devices, plus ~90/hour per Drive-enabled device.

**Refresh rates are entities.** The three interval **Number** entities and the polling **Switches** apply live (no restart) and persist across restarts, so you can tune them from the dashboard or automations. The 10 s minimum is for short-term probe-stability troubleshooting — sustained 10 s polling exceeds the rate limit, so return to 40 s+ afterward.

**IP address (auto + manual).** The cloud reports each device's LAN IP (`internalIP`), which the integration caches automatically — so reconnection detection generally needs **no** manual entry. You can still type an IP in the **LAN IP** entity as an override/fallback; the cloud value is preferred while the device is online (it self-corrects after a new DHCP lease), and your manual value is used when it isn't. Your typed value is never overwritten.

**Reconnection detection (offline polling).** Turn on a device's **Offline Polling** switch and the integration pings its IP every ~20 s, adapting the API poll rate:

| Device state | API poll rate |
|---|---|
| Reporting to the cloud | Normal (*Devices Refresh Interval*) |
| On the network but cloud data still stale | **Fast** (*Offline Refresh Interval*) — catch the reconnection quickly |
| Confirmed off the network | Idle backoff (15 min) — conserve API calls |

The moment the IP reappears, an immediate refresh fires. (The fast "offline" rate only runs *after* the device is seen on-network, so a low value there doesn't waste calls while it's truly offline.)

**Reliability.** Bad/expired credentials trigger Home Assistant's **re-authentication** prompt instead of silent failure. A transient rate-limit or network blip keeps the last known values (and recorder history) rather than flipping everything to *unavailable* — the blip shows on the **Last Seen** sensor's `update_state` attribute.

---

## Entities

Entities live on the physical FireBoard **device**, except the polling/connection controls which live on a **"FireBoard Server Connection"** service device. "Disabled by default" entities are created but must be enabled (per-entity, or in bulk via the Configure toggles).

### Temperature & cook
| Entity | What it's for | Default |
|---|---|---|
| **Channel N - `<label>`** | One temperature sensor per probe/port (label from the FireBoard app) | Enabled |
| **Cook Session** | Name of the active cook (unavailable when no cook running) | Enabled |
| **Cook Started** | Start time of the active cook | Enabled |
| **Session Count** | Number of cook sessions recorded for the device (diagnostic) | Disabled |
| **Last Cook** | Most-recent session name, with start/end/duration attributes | **Disabled** |

### Device health
| Entity | What it's for | Default |
|---|---|---|
| **Battery** | Device battery % | Enabled |
| **Battery Low** | On when battery is low (< 20%) — diagnostic | Enabled |
| **Connectivity** | Online/offline from data freshness — diagnostic | Enabled |
| **Last Seen** | Timestamp of last report; carries poll-health attr — diagnostic | Enabled |
| **Onboard Temperature** | The FireBoard unit's internal board temp (not a food probe) — diagnostic | Disabled |
| **Battery Voltage** | Actual battery volts (`vBatt`) — diagnostic | Disabled |
| **WiFi Signal** | RSSI in dBm, with SSID/band/IP attributes — diagnostic | Disabled |
| **WiFi Link Quality** | Link quality % — diagnostic | Disabled |

### Device info (all diagnostic, disabled by default)
**IP Address**, **Public IP**, **MAC Address**, **WiFi Network** (SSID), **WiFi Band**, **WiFi Frequency**, **Uptime**, **Firmware Version**, **Channel Count**, **Model Name**, **Serial Number**.

### FireBoard Drive (fan controller)
Read entities require **Drive Polling** on (enabled by default) and a Drive attached.
| Entity | What it's for | Default |
|---|---|---|
| **Drive Output** | Current fan/blower output % | Enabled |
| **Drive Setpoint** | Current target temperature (read-only) | Enabled |
| **Drive Lid Paused** | On when the Drive is lid-paused | Enabled |
| **Drive Battery** | Drive battery voltage — diagnostic | **Disabled** |

### Drive control ⚠️ experimental (writes to hardware)
These write to the Drive via an endpoint FireBoard does not formally document. **Drive Setpoint Target** is enabled by default; the rest are disabled and enabled together via the Configure toggle.
| Entity | What it's for | Default |
|---|---|---|
| **Drive Setpoint Target** | Set the Drive's target temperature (auto mode) | **Enabled** |
| **Drive Fan Speed** | Set a fixed fan % (manual mode) | Disabled |
| **Drive Fan Running** | Switch off = turn the Drive fan off | Disabled |
| **Drive Control Channel** | Which probe the Drive PID follows | Disabled |

### Controls (on the "FireBoard Server Connection" device)
| Entity | Type | What it controls |
|---|---|---|
| **Devices Refresh Interval** | Number (10–300 s) | How often `devices.json` is polled |
| **Drive Refresh Interval** | Number (10–300 s) | How often `drivelog.json` is polled |
| **Offline Refresh Interval** | Number (10–300 s) | Fast rate to catch a reconnection |
| **Drive Polling** | Switch | Enable/disable Drive polling globally (default on) |
| **`<device>` LAN IP** | Text | Manual IP override for reconnection detection |
| **`<device>` Offline Polling** | Switch | Enable IP-based reconnection detection for that device |
| **`<device>` On Network** | Binary sensor | Live ping result (shows effective/cloud/manual IP) |

### Configure toggles (Options flow)
- **Enable diagnostic sensors** — create the disabled-by-default diagnostic sensors enabled in bulk.
- **Enable experimental Drive controls** — create the disabled Drive control entities enabled in bulk.

Changing either reloads the integration; all other options apply live.

---

## Notes & limitations

- **Cloud-only:** HA talks to FireBoard's servers, not the device directly. "On Network" pinging is a reachability proxy — being on your LAN doesn't guarantee the cloud has a fresh reading yet, so it triggers *faster polling* rather than fabricating values.
- **Probes read *unknown* when idle:** the API omits readings older than 60 s, so an unplugged/not-reporting probe correctly shows unavailable rather than a stale value.
- **Drive control is experimental:** the write endpoint is undocumented and controls physical hardware. Use realistic setpoints.
- Credentials are entered in the config flow, not exposed as entities.

---

## Version

Build number format: `YYYY.MM.DD.N` — date code plus an increment that **never resets**. Defined in `const.py` (`VERSION`), synced to `manifest.json`, shown as the "FireBoard Server Connection" device software version, and logged at startup.

Current version: **2026.09.03.28**

---

## Credits

- FireBoard cloud API: **[FireBoard Labs](https://docs.fireboard.io/app/app-api/)**
- Field/behavior reference informed by **[fireboard2mqtt](https://github.com/gordlea/fireboard2mqtt)** (@gordlea) and **[fireboard-mcp](https://github.com/benhodgson87/fireboard-mcp)** (@benhodgson87).
