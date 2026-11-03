"""Camera Tester -- live streaming engine.

One background thread per camera keeps an OpenCV capture open, continuously
reads frames, and keeps the newest JPEG available. HTTP endpoints (MJPEG /
snapshot) only ever read the latest frame, so ten browser tabs viewing the
same camera still cost a single capture.
"""

from __future__ import annotations

import os
import threading
import time

# TCP transport for RTSP (see note in discovery.py).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2
import numpy as np

MAX_PREVIEW_WIDTH = 1600
JPEG_QUALITY = 78
FAIL_LIMIT = 30          # consecutive failed reads before reconnect
RETRY_OPEN_DELAY = 4.0   # seconds between open attempts
RECONNECT_DELAY = 2.0


def placeholder_jpeg(text: str, width=640, height=360) -> bytes:
    img = np.full((height, width, 3), 24, dtype=np.uint8)
    cv2.putText(img, text, (30, height // 2), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (120, 200, 255), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes() if ok else b""


class CameraStream:
    """Continuously captures frames from a local index or an RTSP URL."""

    def __init__(self, cam_id: str, source, local: bool = False):
        self.cam_id = cam_id
        self.source = source          # int index for local cams, URL otherwise
        self.local = local
        self.status = "starting"      # starting | live | reconnecting | error | stopped
        self.error = None
        self.started_at = time.time()
        self.frames = 0

        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._captured_at = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"stream-{cam_id}"
        )
        self._thread.start()

    # -- internals ---------------------------------------------------------

    def _open(self):
        if self.local:
            for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
                cap = cv2.VideoCapture(self.source, backend)
                ok, _ = cap.read()
                if ok:
                    return cap
                cap.release()
            return None
        return cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)

    def _publish(self, frame) -> None:
        if frame.shape[1] > MAX_PREVIEW_WIDTH:
            scale = MAX_PREVIEW_WIDTH / frame.shape[1]
            frame = cv2.resize(frame, (MAX_PREVIEW_WIDTH, int(frame.shape[0] * scale)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            return
        with self._lock:
            self._jpeg = buf.tobytes()
            self._captured_at = time.time()
            self.frames += 1

    def _run(self) -> None:
        while not self._stop.is_set():
            cap = self._open()
            if cap is None:
                self._fail("could not open stream/device")
                if self._stop.wait(RETRY_OPEN_DELAY):
                    break
                continue

            self._set("live", None)
            misses = 0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    misses += 1
                    if misses >= FAIL_LIMIT:
                        break
                    time.sleep(0.02)
                    continue
                misses = 0
                self._publish(frame)
            cap.release()

            if not self._stop.is_set():
                self._set("reconnecting", "stream interrupted")
                if self._stop.wait(RECONNECT_DELAY):
                    break

    def _set(self, status: str, error: str | None) -> None:
        with self._lock:
            self.status = status
            self.error = error

    def _fail(self, error: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = error

    # -- public API ----------------------------------------------------------

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def snapshot(self) -> tuple[bytes | None, str]:
        jpeg = self.latest_jpeg()
        if jpeg is None:
            return placeholder_jpeg(f"connecting... ({self.status})"), "starting"
        return jpeg, "live"

    def info(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "error": self.error,
                "frames": self.frames,
                "age": round(time.time() - self._captured_at, 1) if self._captured_at else None,
            }

    def stop(self) -> None:
        self._stop.set()


class StreamManager:
    def __init__(self) -> None:
        self._streams: dict[str, CameraStream] = {}
        self._lock = threading.Lock()

    def start(self, cam_id: str, source, local: bool = False) -> CameraStream:
        with self._lock:
            stream = self._streams.get(cam_id)
            if stream is None or stream.status == "stopped":
                stream = CameraStream(cam_id, source, local=local)
                self._streams[cam_id] = stream
            return stream

    def get(self, cam_id: str) -> CameraStream | None:
        with self._lock:
            return self._streams.get(cam_id)

    def stop(self, cam_id: str) -> bool:
        with self._lock:
            stream = self._streams.pop(cam_id, None)
        if stream:
            stream.stop()
            return True
        return False

    def stop_all(self) -> int:
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for s in streams:
            s.stop()
        return len(streams)

    def running(self) -> list[str]:
        with self._lock:
            return list(self._streams.keys())
