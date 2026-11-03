# Camera Tester -- Real Network Camera Discovery & Live Preview

A working tool that finds cameras on your network and shows **live video**
for every camera whose stream it can open. No simulated data.

## What it actually does

| Discovery method | How | Real? |
|---|---|---|
| ONVIF cameras | Sends a genuine WS-Discovery SOAP `Probe` to multicast `239.255.255.250:3702` and parses the `ProbeMatch` replies (XAddrs, scopes) | yes |
| RTSP cameras | TCP-connects to ports **554 / 8554** on every host of the subnet, then performs a real RTSP `OPTIONS` + `DESCRIBE` handshake to confirm the device speaks RTSP and to read its `Server` banner and `Content-Base` path | yes |
| Local webcams | Enumerates devices via WMI (`Win32_PnPEntity`) and verifies which OpenCV indices actually deliver frames (DirectShow / Media Foundation) | yes |

For every network camera found, it then **tests real RTSP stream paths**
(Hikvision `/Streaming/Channels/101`, Dahua `/cam/realmonitor?channel=1&subtype=0`,
generic `/`, `/stream1`, `/h264`, etc.) by opening each with FFmpeg/OpenCV and
checking that a video frame actually decodes. The path that works becomes the
camera's stream URL, and its live preview starts automatically in the dashboard.

## Run it

```
run.bat            (double-click)
  -- or --
python app.py      then open http://127.0.0.1:8777
```

Console-only scan (no UI):

```
python discovery.py                # auto-detects subnet
python discovery.py 192.168.1.0/24 # explicit subnet
```

## Dashboard features

- **Scan Network** -- full discovery + stream test on the subnet shown
- Live MJPEG previews, started automatically for cameras whose stream works
- Per-camera: Preview / Stop / Test streams / Full view / Snapshot
- **Re-test all streams** -- re-verify every known camera (e.g. after
  reconnecting to the camera network)
- **Credentials** per camera and **global credentials** in the header
  (tried on every camera, after per-camera credentials)
- Subnet box is editable (e.g. scan `192.168.1.0/24` from another interface)
- Reports: CSV / JSON / HTML buttons in the header
- Stop all streams (releases local webcams too)

## Credentials: how access is attempted

There is no way to bypass authentication a camera has properly configured --
the camera itself enforces it. What the tool does instead, in order:

1. Your **per-camera** credentials (entered on the card)
2. The **global** username/password (header, "Use for all")
3. A credential that already worked on a **sibling device** (same server
   banner / model -- the fleet cache), tried first for the rest of the fleet
4. **Factory-default passwords** (admin/12345, admin/(blank), admin/admin,
   admin/123456, admin/9999, ...) -- bounded to the top stream paths so a
   full-bench scan stays fast

This both logs you into the many fresh/test-bench cameras that are still on
factory passwords and doubles as a "who is still on default credentials"
security audit. The card shows which login worked (`Auth: admin / 12345`).

> Fleet tip: set one standard password on all cameras with the vendor batch
> tool (Hikvision SADP / CP Plus batch config / VIGI Security Manager), then
> enter it once as the global credential here.

## Persistence

Everything found is saved to `cameras.json` (same folder) and reloaded at
startup as status **saved** -- so your camera list survives restarts and WiFi
switches. Saved cameras show "Last seen"; press **Test streams** or
**Scan Network** while connected to the camera network to verify them again.

## Automatic network-change handling

A background monitor checks the machine's subnet every 10 seconds:

- **WiFi/subnet changes** -> streams on the old network are stopped, the
  camera list is kept, and a full re-scan starts automatically.
- **Cameras reachable on the new network** -> tested with your credentials +
  factory defaults and go **LIVE** again.
- **Cameras not reachable** -> marked **OFFLINE** ("not reachable on the
  current network") so you can instantly see what works where you are.
- Starting the app also auto-scans the network it finds itself on.

Note: `cameras.json` stores credentials in plain text on this machine -- keep
the folder private.

## Files

- `discovery.py` -- all discovery + stream probing logic (also a CLI)
- `streams.py`   -- one capture thread per camera, MJPEG frame serving
- `app.py`       -- Flask web server + JSON API + reports
- `templates/`   -- dashboard and full-view pages
- `run.bat`      -- launcher

## Notes

- Cameras that require authentication will show **no-preview** until you enter
  credentials on their card and click *Save & test*.
- Some cameras stream on non-standard ports (e.g. 10554); the scan covers 554
  and 8554 -- other ports can be reached by entering a stream URL manually in
  future versions.
- Requirements: `pip install opencv-python flask` (already installed here).
- Runs on `127.0.0.1:8777` (local machine only).
