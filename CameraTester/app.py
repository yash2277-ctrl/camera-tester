"""Camera Tester -- web app.

Run:  python app.py            (then open http://127.0.0.1:8777)

Endpoints:
    GET  /                  dashboard
    GET  /view/<id>         full-page viewer for one camera
    GET  /api/config        {subnet}
    GET  /api/cameras       cameras + scan status (polled by the UI)
    POST /api/scan          {subnet} -- start a full scan (background thread)
    POST /api/test/<id>     re-test stream paths for one camera
    POST /api/creds/<id>    {username, password} -- store and re-test
    POST /api/stop/<id>     stop that camera's capture thread
    POST /api/stop_all      stop all captures
    GET  /video/<id>        live MJPEG stream
    GET  /snapshot/<id>     single JPEG frame
    GET  /report.csv|json|html
"""

from __future__ import annotations

import csv
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

import discovery
import streams

app = Flask(__name__)

STATE_LOCK = threading.Lock()
CAMERAS: dict[str, dict] = {}
CREDS: dict[str, tuple[str, str]] = {}
GLOBAL_CREDS = {"username": "", "password": ""}
SCAN = {
    "scanning": False,
    "progress": [],
    "last_scan": None,
    "subnet": discovery.default_subnet(),
}
manager = streams.StreamManager()

# Cameras survive restarts and network switches: everything found is saved
# to cameras.json and reloaded on startup as status "saved".
SAVE_PATH = Path(__file__).with_name("cameras.json")


def save_cameras() -> None:
    with STATE_LOCK:
        data = {
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cameras": [
                {k: v for k, v in cam.items() if not k.startswith("_") and k != "stream"}
                for cam in CAMERAS.values()
            ],
            "creds": {
                cid: {"username": u, "password": p} for cid, (u, p) in CREDS.items()
            },
        }
    try:
        SAVE_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError as exc:
        log_progress(f"Could not save cameras.json: {exc}")


def load_cameras() -> None:
    if not SAVE_PATH.exists():
        return
    try:
        data = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for cam in data.get("cameras", []):
        cam = dict(cam)
        cam.setdefault("protocols", [])
        cam["status"] = "saved"
        cam["error_hint"] = (
            "From an earlier scan -- press 'Test streams' (or 'Scan Network') "
            "while connected to the camera network to verify it again."
        )
        CAMERAS[cam["id"]] = cam
    for cid, c in data.get("creds", {}).items():
        CREDS[cid] = (c.get("username", ""), c.get("password", ""))
    if CAMERAS:
        log_progress(f"Loaded {len(CAMERAS)} saved camera(s) from cameras.json")


def log_progress(message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    with STATE_LOCK:
        SCAN["progress"].append(f"[{stamp}] {message}")
        SCAN["progress"] = SCAN["progress"][-12:]


load_cameras()


# ---------------------------------------------------------------------------
# Camera registry helpers
# ---------------------------------------------------------------------------

def network_id(ip: str) -> str:
    return f"net-{ip}"


def merge_camera(cam: dict) -> dict:
    cam_id = f"local-{cam['local_index']}" if cam["kind"] == "local" else network_id(cam["ip"])
    with STATE_LOCK:
        existing = CAMERAS.get(cam_id)
        if existing is None:
            cam = {
                "id": cam_id,
                "status": "found",
                "stream_url": None,
                "discovered_at": time.strftime("%H:%M:%S"),
                "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                **cam,
            }
            CAMERAS[cam_id] = cam
        else:
            existing["last_seen"] = time.strftime("%Y-%m-%d %H:%M:%S")
            for key in ("name", "model", "device_url", "server", "port"):
                if cam.get(key) and not existing.get(key):
                    existing[key] = cam[key]
            if cam.get("candidate_paths"):
                merged = list(dict.fromkeys(
                    (existing.get("candidate_paths") or []) + cam["candidate_paths"]
                ))
                existing["candidate_paths"] = merged
            for proto in cam["protocols"]:
                if proto not in existing["protocols"]:
                    existing["protocols"].append(proto)
        return CAMERAS[cam_id]


def public_cameras() -> list[dict]:
    with STATE_LOCK:
        cams = []
        for cam in CAMERAS.values():
            out = {k: v for k, v in cam.items() if not k.startswith("_")}
            stream = manager.get(out["id"])
            if stream:
                out["stream"] = stream.info()
            out["has_creds"] = out["id"] in CREDS
            cams.append(out)
    cams.sort(key=lambda c: (c["kind"] != "local", c["ip"], c.get("port") or 0))
    return cams


# ---------------------------------------------------------------------------
# Scanning (background)
# ---------------------------------------------------------------------------

def run_scan(subnet: str) -> None:
    try:
        log_progress("Enumerating local webcams...")
        for cam in discovery.find_local_cameras():
            merge_camera(cam)
        n_local = sum(1 for c in public_cameras() if c["kind"] == "local")
        log_progress(f"Local webcams found: {n_local}")

        log_progress("Sending ONVIF WS-Discovery probe (multicast 3702)...")
        for cam in discovery.find_onvif_cameras():
            merge_camera(cam)

        log_progress(f"RTSP-scanning {subnet} on ports 554/8554...")
        for cam in discovery.scan_rtsp(subnet, progress=log_progress):
            merge_camera(cam)

        with STATE_LOCK:
            targets = [c for c in CAMERAS.values() if c["kind"] == "network"]
        log_progress(f"Testing stream paths on {len(targets)} network device(s)...")

        def test(cam):
            test_camera_stream(cam["id"], announce=False)

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(test, targets))

        log_progress("Scan complete.")
        save_cameras()
    except Exception as exc:  # keep the UI alive no matter what
        log_progress(f"Scan error: {exc!r}")
    finally:
        with STATE_LOCK:
            SCAN["scanning"] = False
            SCAN["last_scan"] = time.strftime("%Y-%m-%d %H:%M:%S")


def start_scan(subnet: str) -> bool:
    with STATE_LOCK:
        if SCAN["scanning"]:
            return False
        SCAN["scanning"] = True
        SCAN["subnet"] = subnet
    threading.Thread(target=run_scan, args=(subnet,), daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# Network-change watcher: auto re-scan when the machine switches networks
# ---------------------------------------------------------------------------

NET_WATCH = {
    "current": discovery.default_subnet(),  # last confirmed subnet
    "pending": None,                        # candidate subnet (debounce)
    "stable": 0,
    "last_auto": 0.0,
}
CHECK_INTERVAL = 10       # seconds between network checks
DEBOUNCE_CHECKS = 2       # consecutive identical readings before acting
AUTO_SCAN_COOLDOWN = 30   # minimum seconds between automatic scans


def _on_network_changed(old, new) -> None:
    """Network changed: drop unreachable streams, keep data, re-scan."""
    if new is None:
        log_progress(f"Network disconnected (was {old or 'none'}). "
                     "Cameras kept as saved -- will re-test when back online.")
    else:
        log_progress(f"Network changed: {old or 'none'} -> {new} "
                     f"(this machine: {discovery.local_ip()}) -- auto-scanning...")
    with STATE_LOCK:
        net_ids = [cid for cid, c in CAMERAS.items() if c["kind"] == "network"]
        for cid in net_ids:
            cam = CAMERAS[cid]
            cam["status"] = "saved"
            cam["error_hint"] = (
                f"Network changed ({old or 'offline'} -> {new or 'offline'}); "
                "auto re-test in progress..."
            )
    for cid in net_ids:
        manager.stop(cid)
    if new:
        start_scan(new)


def network_watcher() -> None:
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            subnet = discovery.default_subnet()
        except Exception:
            continue
        with STATE_LOCK:
            current = NET_WATCH["current"]
        if subnet == current:
            NET_WATCH["pending"] = None
            NET_WATCH["stable"] = 0
            continue
        if NET_WATCH["pending"] != subnet:
            NET_WATCH["pending"] = subnet
            NET_WATCH["stable"] = 1
            continue
        NET_WATCH["stable"] += 1
        if NET_WATCH["stable"] < DEBOUNCE_CHECKS:
            continue
        # confirmed change (stable across DEBOUNCE_CHECKS readings)
        NET_WATCH["pending"] = None
        NET_WATCH["stable"] = 0
        if time.time() - NET_WATCH["last_auto"] < AUTO_SCAN_COOLDOWN:
            continue
        NET_WATCH["last_auto"] = time.time()
        with STATE_LOCK:
            NET_WATCH["current"] = subnet
        try:
            _on_network_changed(current, subnet)
        except Exception as exc:
            log_progress(f"Network watcher error: {exc!r}")


# ---------------------------------------------------------------------------
# Stream testing / credentials
# ---------------------------------------------------------------------------

def test_camera_stream(cam_id: str, announce: bool = True) -> None:
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        if cam is None or cam["kind"] != "network":
            return
        cam["status"] = "probing"
        username, password = CREDS.get(cam_id) or (
            (GLOBAL_CREDS["username"], GLOBAL_CREDS["password"])
            if GLOBAL_CREDS["username"] else (None, None)
        )
        snapshot = dict(cam)

    if announce:
        log_progress(f"Testing streams for {snapshot['ip']}...")
    res = discovery.probe_camera_stream(snapshot, username, password,
                                        try_defaults=True)

    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        if cam is None:
            return
        if res:
            cam["stream_url"] = res["url"]
            cam["status"] = "live"
            cam["cred_used"] = f"{res['username']} / {res['password'] or '(blank)'}"
            cam.pop("error_hint", None)
        elif discovery.rtsp_port_open(snapshot["ip"], 80, timeout=0.6):
            cam["status"] = "no-preview"
            cam["error_hint"] = (
                "Camera is reachable but no RTSP stream opened -- enter its real "
                "password on the card, or set a global username/password in the header."
            )
        else:
            cam["status"] = "offline"
            cam["error_hint"] = "Not reachable on the current network."
    if announce:
        log_progress(
            f"{snapshot['ip']}: "
            + (f"LIVE {res['url']} [auth: {res['username']}]"
               if res else "no working RTSP path")
        )
    save_cameras()


def test_local_camera(cam_id: str) -> None:
    """(Re)verify a local webcam still delivers frames and mark it ready."""
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        if cam is None or cam["kind"] != "local":
            return
        if manager.get(cam_id):
            cam["status"] = "found"   # capture already running == it works
            return
        cam["status"] = "probing"
    ok = cam["local_index"] in discovery.probe_local_indices(max_index=cam["local_index"] + 1)
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        if cam:
            cam["status"] = "found" if ok else "no-preview"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/view/<cam_id>")
def view(cam_id):
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        info = {k: v for k, v in cam.items() if not k.startswith("_")} if cam else None
    return render_template("view.html", cam=info)


@app.get("/api/config")
def api_config():
    with STATE_LOCK:
        return jsonify({"subnet": SCAN["subnet"], "scanning": SCAN["scanning"],
                        "global_user": GLOBAL_CREDS["username"]})


@app.get("/api/cameras")
def api_cameras():
    with STATE_LOCK:
        scan = dict(SCAN, progress=list(SCAN["progress"]))
    return jsonify({"cameras": public_cameras(), "scan": scan})


@app.post("/api/scan")
def api_scan():
    subnet = (request.get_json(silent=True) or {}).get("subnet") or SCAN["subnet"]
    if not subnet:
        return jsonify({"error": "No subnet detected; pass one, e.g. 192.168.1.0/24"}), 400
    try:
        import ipaddress
        ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        return jsonify({"error": f"Invalid subnet: {subnet}"}), 400
    if not start_scan(subnet):
        return jsonify({"error": "Scan already running"}), 409
    return jsonify({"started": True, "subnet": subnet})


@app.post("/api/global_creds")
def api_global_creds():
    """Fleet-wide credentials tried on every camera (after per-camera creds)."""
    data = request.get_json(silent=True) or {}
    GLOBAL_CREDS["username"] = (data.get("username") or "").strip()
    GLOBAL_CREDS["password"] = data.get("password") or ""
    return jsonify({"saved": bool(GLOBAL_CREDS["username"])})


@app.post("/api/test/<cam_id>")
def api_test(cam_id):
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
    if cam is None:
        return jsonify({"error": "unknown camera"}), 404
    if cam["kind"] == "network":
        threading.Thread(target=test_camera_stream, args=(cam_id,), daemon=True).start()
    else:
        threading.Thread(target=test_local_camera, args=(cam_id,), daemon=True).start()
    return jsonify({"testing": True})


@app.post("/api/creds/<cam_id>")
def api_creds(cam_id):
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username:
        return jsonify({"error": "username required"}), 400
    CREDS[cam_id] = (username, password)
    save_cameras()
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        if cam:
            cam["status"] = "found"
    threading.Thread(target=test_camera_stream, args=(cam_id,), daemon=True).start()
    return jsonify({"saved": True})


@app.post("/api/test_all")
def api_test_all():
    """Re-verify stream paths on every known network camera (e.g. after
    reconnecting to the camera network)."""
    with STATE_LOCK:
        ids = [c["id"] for c in CAMERAS.values() if c["kind"] == "network"]

    def run():
        log_progress(f"Re-testing {len(ids)} camera(s)...")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda cid: test_camera_stream(cid, announce=False), ids))
        log_progress("Re-test complete.")

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"testing": len(ids)})


@app.post("/api/stop/<cam_id>")
def api_stop(cam_id):
    manager.stop(cam_id)
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        if cam:
            cam["user_stopped"] = True
    return jsonify({"stopped": cam_id})


@app.post("/api/start/<cam_id>")
def api_start(cam_id):
    """Explicit preview: clears the stop flag and starts the capture."""
    cam = _require_camera(cam_id)
    if cam is None:
        return jsonify({"error": "unknown camera"}), 404
    with STATE_LOCK:
        if cam_id in CAMERAS:
            CAMERAS[cam_id]["user_stopped"] = False
    if cam["kind"] == "local":
        manager.start(cam_id, cam["local_index"], local=True)
    else:
        url = cam.get("stream_url") or discovery.build_rtsp_url(
            cam["ip"], cam.get("port") or 554, "/", *CREDS.get(cam_id, (None, None)))
        manager.start(cam_id, url, local=False)
    return jsonify({"started": cam_id})


@app.post("/api/stop_all")
def api_stop_all():
    return jsonify({"stopped": manager.stop_all()})


def _require_camera(cam_id) -> dict | None:
    with STATE_LOCK:
        cam = CAMERAS.get(cam_id)
        return dict(cam) if cam else None


@app.get("/video/<cam_id>")
def video(cam_id):
    cam = _require_camera(cam_id)
    if cam is None:
        return jsonify({"error": "unknown camera"}), 404

    if cam["kind"] == "local":
        stream = manager.start(cam_id, cam["local_index"], local=True)
    else:
        url = cam.get("stream_url")
        if not url:
            # allow trying the best guess directly if the user forces preview
            port = cam.get("port") or 554
            url = discovery.build_rtsp_url(cam["ip"], port, "/", *CREDS.get(cam_id, (None, None)))
        stream = manager.start(cam_id, url, local=False)

    def generate():
        while True:
            jpeg = stream.latest_jpeg()
            if jpeg is None:
                jpeg = streams.placeholder_jpeg(f"{cam['name']} - {stream.status}")
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )
            time.sleep(0.04)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.get("/snapshot/<cam_id>")
def snapshot(cam_id):
    cam = _require_camera(cam_id)
    if cam is None:
        return jsonify({"error": "unknown camera"}), 404
    stream = manager.get(cam_id)
    if stream is None:
        return jsonify({"error": "stream not running; click Preview first"}), 409
    jpeg, status = stream.snapshot()
    return Response(jpeg, mimetype="image/jpeg")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def _report_rows():
    return [
        {
            "name": c["name"],
            "ip": c["ip"],
            "port": c.get("port") or ("" if c["kind"] != "local" else "-"),
            "kind": c["kind"],
            "protocols": ",".join(c["protocols"]),
            "server": c.get("server") or "",
            "status": c["status"],
            "stream_url": c.get("stream_url") or "",
        }
        for c in public_cameras()
    ]


@app.get("/report.csv")
def report_csv():
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_report_rows()[0].keys()) if _report_rows()
                            else ["name", "ip", "port", "kind", "protocols", "server", "status", "stream_url"])
    writer.writeheader()
    writer.writerows(_report_rows())
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=camera_report.csv"})


@app.get("/report.json")
def report_json():
    return jsonify({"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "cameras": _report_rows()})


@app.get("/report.html")
def report_html():
    rows = _report_rows()
    trs = "\n".join(
        f"<tr><td>{r['name']}</td><td>{r['ip']}</td><td>{r['port']}</td>"
        f"<td>{r['protocols']}</td><td class='{ 'ok' if r['status']=='live' else 'bad' }'>"
        f"{r['status']}</td><td>{r['stream_url']}</td></tr>"
        for r in rows
    ) or "<tr><td colspan='6'>No cameras yet -- run a scan.</td></tr>"
    page = f"""<!DOCTYPE html><html><head><title>Camera Report</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;background:#111827;color:#e5e7eb;margin:24px}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #374151;padding:8px;text-align:left}}
th{{background:#1f2937}}.ok{{color:#34d399;font-weight:600}}.bad{{color:#f87171}}</style></head>
<body><h1>Camera Report</h1><p>Generated {time.strftime('%Y-%m-%d %H:%M:%S')} -- {len(rows)} camera(s)</p>
<table><tr><th>Name</th><th>IP</th><th>Port</th><th>Protocols</th><th>Status</th><th>Stream URL</th></tr>
{trs}</table></body></html>"""
    return Response(page, mimetype="text/html")


if __name__ == "__main__":
    import webbrowser

    threading.Thread(target=network_watcher, daemon=True,
                     name="network-watcher").start()
    log_progress("Network monitor active -- switching WiFi/subnet triggers an automatic re-scan.")
    if SCAN["subnet"]:
        log_progress(f"Auto-scanning current network {SCAN['subnet']}...")
        threading.Timer(3.0, lambda: start_scan(SCAN["subnet"])).start()
    print("Camera Tester running on http://127.0.0.1:8777")
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8777")).start()
    app.run(host="127.0.0.1", port=8777, threaded=True)
