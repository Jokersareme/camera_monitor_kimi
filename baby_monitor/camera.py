import logging
import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2


log = logging.getLogger(__name__)


@dataclass
class FramePacket:
    frame: object
    sequence: int
    captured_at: float
    monotonic_at: float


class CameraManager:
    def __init__(self, config, cameras=None, selected_id=None):
        self.config = dict(config) if config.get("url") or config.get("source") == "usb" else None
        self._cameras = [dict(camera) for camera in cameras] if cameras is not None else []
        if selected_id:
            self.config = self._find_camera(selected_id)
        elif cameras is not None and not self._cameras:
            self.config = None
        self._lock = threading.Lock()
        self._switch_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._capture = None
        self._latest = None
        self._sequence = 0
        self._state = "connecting" if self.config else "unconfigured"
        self._last_error = ""
        self._state_changed_at = time.time()
        self._frames_received = 0
        self._frame_times = deque(maxlen=40)

    def _find_camera(self, camera_id):
        for camera in self._cameras:
            if camera["id"] == camera_id:
                return dict(camera)
        raise ValueError("所选摄像头不存在")

    def list_cameras(self):
        return [{"id": camera["id"], "name": camera["name"], "source": camera["source"]} for camera in self._cameras]

    def select(self, camera_id):
        if not isinstance(camera_id, str) or not camera_id:
            raise ValueError("需要有效的摄像头 id")
        selected = self._find_camera(camera_id)
        with self._switch_lock:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=8)
                if self._thread.is_alive():
                    raise RuntimeError("旧摄像头暂时无法停止，请稍后重试")
            self.config = selected
            with self._lock:
                self._latest = None
                self._sequence = 0
                self._frames_received = 0
                self._frame_times.clear()
                self._state = "connecting"
                self._last_error = ""
                self._state_changed_at = time.time()
            self._thread = None
            self.start()
        return self.get_status()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="camera-capture", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)
        with self._lock:
            self._latest = None
            self._state = "offline" if self.config else "unconfigured"

    def _set_state(self, state, error=""):
        with self._lock:
            if state != self._state:
                self._state_changed_at = time.time()
            self._state = state
            self._last_error = error
            if state != "online":
                self._latest = None

    def _open(self):
        camera = self.config
        source = camera["url"] if camera["source"] == "phone" else camera["device"]
        if camera["source"] == "usb" and isinstance(source, str) and source.isdigit():
            source = int(source)
        backend = cv2.CAP_FFMPEG if camera["source"] == "phone" else (cv2.CAP_V4L2 if hasattr(cv2, "CAP_V4L2") else cv2.CAP_ANY)
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, camera["open_timeout_ms"],
                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, camera["read_timeout_ms"]]
        try:
            capture = cv2.VideoCapture()
            opened = capture.open(source, backend, params)
        except (cv2.error, TypeError):
            capture = cv2.VideoCapture(source, backend)
            opened = capture.isOpened()
        if not opened or not capture.isOpened():
            capture.release()
            return None
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if camera["source"] == "usb":
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, camera["width"])
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, camera["height"])
            capture.set(cv2.CAP_PROP_FPS, camera["fps"])
        self._capture = capture
        return capture

    def _run(self):
        if self.config is None:
            self._set_state("unconfigured", "尚未配置摄像头")
            return
        while not self._stop.is_set():
            self._set_state("reconnecting" if self._frames_received else "connecting")
            try:
                capture = self._open()
            except Exception:
                log.exception("摄像头打开失败")
                capture = None
            if capture is None:
                self._set_state("reconnecting", "无法打开视频源，正在重试")
                self._stop.wait(self.config["reconnect_delay_seconds"])
                continue
            self._set_state("online")
            log.info("摄像头已连接：%s", self.config.get("name", self.config["source"]))
            failed_reads = 0
            while not self._stop.is_set():
                try:
                    ok, frame = capture.read()
                except cv2.error:
                    ok, frame = False, None
                if not ok or frame is None or frame.size == 0:
                    failed_reads += 1
                    if failed_reads >= 2:
                        break
                    self._stop.wait(0.2)
                    continue
                failed_reads = 0
                now = time.monotonic()
                with self._lock:
                    self._sequence += 1
                    self._frames_received += 1
                    self._latest = FramePacket(frame, self._sequence, time.time(), now)
                    self._frame_times.append(now)
            capture.release()
            self._capture = None
            if not self._stop.is_set():
                self._set_state("reconnecting", "视频流中断，正在重连")
                log.warning("视频流中断，将自动重连")
                self._stop.wait(self.config["reconnect_delay_seconds"])

    def get_latest(self, copy_frame=True):
        with self._lock:
            packet = self._latest
            if packet is None:
                return None
            frame = packet.frame.copy() if copy_frame else packet.frame
            return FramePacket(frame, packet.sequence, packet.captured_at, packet.monotonic_at)

    def get_status(self):
        with self._lock:
            fps = 0.0
            if len(self._frame_times) >= 2:
                elapsed = self._frame_times[-1] - self._frame_times[0]
                fps = (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0.0
            return {
                "state": self._state,
                "online": self._state == "online" and self._latest is not None,
                "id": self.config.get("id") if self.config else None,
                "name": self.config.get("name") if self.config else None,
                "source": self.config["source"] if self.config else None,
                "last_frame_at": self._latest.captured_at if self._latest else None,
                "last_sequence": self._sequence,
                "frames_received": self._frames_received,
                "fps": round(fps, 2),
                "state_changed_at": self._state_changed_at,
                "message": self._last_error,
            }
