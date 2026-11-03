"""Camera Tester -- real network camera discovery.

Three discovery methods, all real (no simulated data):

1. ONVIF WS-Discovery   -- UDP multicast probe to 239.255.255.250:3702,
                           parses XAddrs / Scopes from ProbeMatch responses.
2. RTSP service scan    -- TCP connect to ports 554/8554 on every host of the
                           subnet, then a real RTSP OPTIONS (+ DESCRIBE)
                           handshake to confirm the device speaks RTSP and to
                           read its Server banner / Content-Base stream path.
3. Local webcams        -- WMI device names via PowerShell + OpenCV index
                           probe (DirectShow with Media Foundation fallback).

Run directly for a console report:
    python discovery.py [subnet]
"""

from __future__ import annotations

import concurrent.futures as futures
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from urllib.parse import quote, unquote, urlparse

# Ask FFmpeg to use TCP for RTSP (UDP drops packets on WiFi and tears the
# image). FFmpeg 7 (bundled with OpenCV 5 wheels) no longer accepts
# "stimeout", so only the transport option is set here.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2

WS_DISCOVERY_ADDR = ("239.255.255.250", 3702)
ONVIF_LISTEN_SECONDS = 3.5
RTSP_PORTS = (554, 8554)
SCAN_THREADS = 220
PORT_TIMEOUT = 0.4
RTSP_TIMEOUT = 1.5

# Paths tried (in order) when looking for a working RTSP stream URL.
PATH_CANDIDATES = [
    "/",                                        # many cameras default
    "/stream1",
    "/Streaming/Channels/101",                  # Hikvision main stream
    "/Streaming/Channels/102",                  # Hikvision sub stream
    "/cam/realmonitor?channel=1&subtype=0",     # Dahua main stream
    "/h264",
    "/h264.sdp",
    "/live/ch00_0",
    "/live.sdp",
    "/onvif1",                                  # Foscam / generic ONVIF
    "/media.smp",                               # Vivotek
    "/av0_0",
    "/video1",
    "/mpeg4",
]


def local_ip() -> str:
    """Best-effort primary LAN IPv4 (does not actually send traffic)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def default_subnet() -> str | None:
    ip = local_ip()
    if ip.startswith("127."):
        return None
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


# --------------------------------------------------------------------------
# 1. ONVIF WS-Discovery
# --------------------------------------------------------------------------

_ONVIF_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>urn:uuid:{mid}</w:MessageID>
    <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""


def find_onvif_cameras(listen: float = ONVIF_LISTEN_SECONDS) -> list[dict]:
    """Send an ONVIF WS-Discovery Probe and collect ProbeMatch replies."""
    found: list[dict] = []
    seen: set[str] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)
    probe = _ONVIF_PROBE.format(mid=uuid.uuid4())
    try:
        sock.sendto(probe.encode("utf-8"), WS_DISCOVERY_ADDR)
        deadline = time.time() + listen
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            source_ip = addr[0]
            if source_ip in seen:
                continue
            seen.add(source_ip)
            cam = _parse_wsdiscovery(data.decode("utf-8", "replace"), source_ip)
            if cam:
                found.append(cam)
    finally:
        sock.close()
    return found


def _parse_wsdiscovery(xml_text: str, source_ip: str) -> dict | None:
    xaddrs_m = re.search(r"<[^>]*:?XAddrs[^>]*>(.*?)</[^>]*:?XAddrs", xml_text, re.S)
    scopes_m = re.search(r"<[^>]*:?Scopes[^>]*>(.*?)</[^>]*:?Scopes", xml_text, re.S)

    device_url = None
    if xaddrs_m:
        urls = xaddrs_m.group(1).split()
        if urls:
            device_url = urls[0]

    name = model = None
    if scopes_m:
        scopes = unquote(scopes_m.group(1))
        m = re.search(r"onvif://www\.onvif\.org/name/([^\"< ]+)", scopes)
        if m:
            name = m.group(1)
        m = re.search(r"onvif://www\.onvif\.org/hardware/([^\"< ]+)", scopes)
        if m:
            model = m.group(1)

    ip = source_ip
    port = None
    if device_url:
        try:
            parsed = urlparse(device_url)
            if parsed.hostname:
                ip = parsed.hostname
            if parsed.port:
                port = parsed.port
        except ValueError:
            pass

    if not name:
        name = "ONVIF Camera"
    return {
        "kind": "network",
        "ip": ip,
        "port": port,                # device-service port; RTSP port found by scan
        "name": name,
        "model": model or "ONVIF Profile S",
        "protocols": ["ONVIF"],
        "device_url": device_url,
        "server": None,
        "candidate_paths": [],
        "source_ip": source_ip,
    }


# --------------------------------------------------------------------------
# 2. RTSP service scan
# --------------------------------------------------------------------------

def _rtsp_probe(ip: str, port: int) -> dict | None:
    """Return server info if a real RTSP server answers on ip:port."""
    try:
        with socket.create_connection((ip, port), timeout=PORT_TIMEOUT) as s:
            s.settimeout(RTSP_TIMEOUT)
            s.sendall(
                (
                    f"OPTIONS rtsp://{ip}:{port}/ RTSP/1.0\r\n"
                    "CSeq: 1\r\n"
                    "User-Agent: CameraTester/1.0\r\n\r\n"
                ).encode()
            )
            data = s.recv(4096)
    except OSError:
        return None

    text = data.decode("latin-1", "replace")
    if not text.startswith("RTSP/"):
        return None

    server = None
    m = re.search(r"(?im)^Server:\s*(.+)$", text)
    if m:
        server = m.group(1).strip()

    # DESCRIBE often reveals the real stream path via Content-Base, even
    # when the camera answers 401 Unauthorized.
    content_base = None
    try:
        with socket.create_connection((ip, port), timeout=PORT_TIMEOUT) as s:
            s.settimeout(RTSP_TIMEOUT)
            s.sendall(
                (
                    f"DESCRIBE rtsp://{ip}:{port}/ RTSP/1.0\r\n"
                    "CSeq: 2\r\n"
                    "Accept: application/sdp\r\n"
                    "User-Agent: CameraTester/1.0\r\n\r\n"
                ).encode()
            )
            data = s.recv(8192)
        m = re.search(r"(?im)^Content-Base:\s*(.+)$", data.decode("latin-1", "replace"))
        if m:
            content_base = m.group(1).strip()
    except OSError:
        pass

    return {"server": server, "content_base": content_base}


def scan_rtsp(subnet: str, progress=None) -> list[dict]:
    """TCP+RTSP scan of every host in the subnet on ports 554 and 8554."""
    net = ipaddress.ip_network(subnet, strict=False)
    hosts = [str(h) for h in net.hosts()]
    found: list[dict] = []

    def worker(ip: str):
        hits = []
        for port in RTSP_PORTS:
            info = _rtsp_probe(ip, port)
            if info:
                hits.append((ip, port, info))
        return hits

    if progress:
        progress(f"Probing {len(hosts)} hosts x {len(RTSP_PORTS)} ports...")
    with futures.ThreadPoolExecutor(max_workers=SCAN_THREADS) as ex:
        for hits in ex.map(worker, hosts):
            for ip, port, info in hits:
                path = None
                if info["content_base"]:
                    path = urlparse(info["content_base"]).path or "/"
                    if path == "/":
                        path = None
                found.append(
                    {
                        "kind": "network",
                        "ip": ip,
                        "port": port,
                        "name": f"RTSP Camera ({ip})",
                        "model": None,
                        "protocols": ["RTSP"],
                        "device_url": None,
                        "server": info["server"],
                        "candidate_paths": [path] if path else [],
                        "source_ip": ip,
                    }
                )
    return found


# --------------------------------------------------------------------------
# 3. Local webcams
# --------------------------------------------------------------------------

def local_camera_names() -> list[str]:
    """Camera-ish device names from Windows WMI (best effort)."""
    ps = (
        "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -eq 'Camera' "
        "-or $_.PNPClass -eq 'Image' -or $_.Name -match 'camera|webcam' } | "
        "Select-Object -ExpandProperty Name"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=25,
        ).stdout
    except Exception:
        return []
    return list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))


def probe_local_indices(max_index: int = 4) -> list[int]:
    """Return camera indices that actually deliver a frame on this machine."""
    good = []
    for idx in range(max_index):
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
            cap = cv2.VideoCapture(idx, backend)
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None:
                good.append(idx)
                break
    return good


def find_local_cameras() -> list[dict]:
    names = local_camera_names()
    indices = probe_local_indices()
    cams = []
    for i, idx in enumerate(indices):
        name = names[i] if i < len(names) else f"Webcam {idx}"
        cams.append(
            {
                "kind": "local",
                "ip": "127.0.0.1",
                "port": None,
                "name": name,
                "model": None,
                "protocols": ["Local"],
                "device_url": None,
                "server": None,
                "candidate_paths": [],
                "source_ip": "127.0.0.1",
                "local_index": idx,
            }
        )
    return cams


# --------------------------------------------------------------------------
# Stream URL probing
# --------------------------------------------------------------------------

def build_rtsp_url(ip, port, path, username=None, password=None) -> str:
    auth = ""
    if username:
        auth = quote(str(username), safe="") + ":" + quote(str(password or ""), safe="") + "@"
    return f"rtsp://{auth}{ip}:{port}{path or '/'}"


def probe_rtsp_url(url: str, max_seconds: float = 10.0) -> bool:
    """True if OpenCV/FFmpeg can pull at least one video frame from url."""
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    deadline = time.time() + max_seconds
    ok = False
    try:
        while time.time() < deadline:
            ok, frame = cap.read()
            if ok and frame is not None:
                break
            if not cap.isOpened():
                ok = False
                break
        else:
            ok = False
    finally:
        cap.release()
    return ok


def candidate_paths(cam: dict) -> list[str]:
    paths = list(PATH_CANDIDATES)
    for extra in cam.get("candidate_paths") or []:
        if extra and extra not in paths:
            paths.insert(0, extra)
    return paths[:14]


# Factory-default credential pairs commonly found on fresh / test-bench
# cameras. Trying these on your own commissioning network is standard
# practice (it is also a useful "who is still on default passwords" audit).
DEFAULT_CREDENTIALS = [
    ("admin", "12345"),      # Hikvision / CP Plus factory default
    ("admin", ""),           # many generic IPCs ship with a blank password
    ("admin", "admin"),
    ("admin", "123456"),
    ("admin", "9999"),       # CP Plus / some DVRs
    ("admin", "1234"),
    ("admin", "password"),
    ("root", "pass"),        # some Axis / embedded stacks
    ("root", "admin"),
    ("admin", "12345678"),
]

# Fleet cache: once a credential works for one camera of a given server
# banner / model, the same pair is tried first on its siblings.
_CRED_CACHE: dict[str, tuple[str, str]] = {}
_CRED_CACHE_LOCK = threading.Lock()


def _remember_credential(cam: dict, username: str, password: str) -> None:
    key = ((cam.get("server") or "").strip().lower()
           or (cam.get("model") or "").strip().lower())
    if not key:
        return
    with _CRED_CACHE_LOCK:
        _CRED_CACHE[key] = (username, password)


def _cached_credential(cam: dict) -> tuple[str, str] | None:
    key = ((cam.get("server") or "").strip().lower()
           or (cam.get("model") or "").strip().lower())
    if not key:
        return None
    with _CRED_CACHE_LOCK:
        return _CRED_CACHE.get(key)


def rtsp_port_open(ip: str, port: int, timeout: float = PORT_TIMEOUT) -> bool:
    """Cheap TCP check so we don't waste seconds probing paths on dead ports."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_camera_stream(cam: dict, username=None, password=None,
                        try_defaults: bool = False) -> dict | None:
    """Try candidate ports/paths with candidate credentials.

    Returns {"url", "username", "password"} of the first combination that
    delivers video, or None. Credential priority:
      1. explicitly given (per-camera or global) credentials
      2. a credential that already worked for a sibling device (fleet cache)
      3. factory-default pairs (when try_defaults) -- top paths only, bounded

    For cameras found by the RTSP scan the verified port is used. For
    ONVIF-only devices the port is the *device-service* port, so standard
    RTSP ports are tried first and the device port last.
    """
    if cam.get("port") and "RTSP" in (cam.get("protocols") or []):
        ports = [cam["port"]]
    else:
        ports = [554, 8554]
        if cam.get("port") and cam["port"] not in ports:
            ports.append(cam["port"])
    ports = [p for p in ports if rtsp_port_open(cam["ip"], p)]
    if not ports:
        return None

    paths = candidate_paths(cam)

    explicit: list[tuple[str, str]] = []
    if username:
        explicit.append((username, password or ""))
    cached = _cached_credential(cam)
    if cached and cached not in explicit:
        explicit.append(cached)

    defaults = [p for p in DEFAULT_CREDENTIALS if p not in explicit] if try_defaults else []

    def attempt(pair, path_list):
        user, pwd = pair
        for port in ports:
            for path in path_list:
                url = build_rtsp_url(cam["ip"], port, path, user, pwd)
                if probe_rtsp_url(url):
                    _remember_credential(cam, user, pwd)
                    return {"url": url, "username": user, "password": pwd}
        return None

    # Explicit / fleet-cached credentials get the full path sweep.
    for pair in explicit:
        hit = attempt(pair, paths)
        if hit:
            return hit

    # Factory defaults: bounded to the top few paths to keep the fleet scan fast.
    for pair in defaults:
        hit = attempt(pair, paths[:3])
        if hit:
            return hit
    return None


# --------------------------------------------------------------------------
# CLI report
# --------------------------------------------------------------------------

def _cli():
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Real camera discovery report")
    parser.add_argument("subnet", nargs="?", default=default_subnet(),
                        help="e.g. 192.168.62.0/24 (default: auto-detect)")
    parser.add_argument("--no-probe", action="store_true",
                        help="skip RTSP stream-path testing")
    args = parser.parse_args()

    print(f"Local IP : {local_ip()}")
    print(f"Subnet   : {args.subnet}")
    print()

    print("[1/3] Local webcams (WMI + DirectShow)...")
    cams = find_local_cameras()
    print(f"      {len(cams)} local camera(s)")
    for c in cams:
        print(f"      - index {c['local_index']}: {c['name']}")

    print("[2/3] ONVIF WS-Discovery probe (239.255.255.250:3702)...")
    onvif = find_onvif_cameras()
    print(f"      {len(onvif)} ONVIF device(s)")
    for c in onvif:
        print(f"      - {c['ip']}  {c['name']}  ({c['device_url']})")

    print(f"[3/3] RTSP scan of {args.subnet} (ports {RTSP_PORTS})...")
    rtsp = scan_rtsp(args.subnet)
    print(f"      {len(rtsp)} RTSP service(s)")
    for c in rtsp:
        print(f"      - {c['ip']}:{c['port']}  server={c['server']}"
              + (f"  content_base={c['candidate_paths'][0]}" if c["candidate_paths"] else ""))

    if not args.no_probe:
        targets = onvif + rtsp
        if targets:
            print()
            print("Testing RTSP stream paths (factory defaults + no-auth)...")
        for c in targets:
            res = probe_camera_stream(c, try_defaults=True)
            if res:
                c["stream_url"] = res["url"]
                c["status"] = "live"
                cred = f"{res['username']} / {res['password'] or '(blank)'}"
                print(f"  LIVE     {c['ip']:<16} {res['url']}   [auth: {cred}]")
            else:
                c["status"] = "no-preview"
                print(f"  NO VIDEO {c['ip']:<16} (needs its real password or non-default port)")

    print()
    print("Done.")


if __name__ == "__main__":
    _cli()
