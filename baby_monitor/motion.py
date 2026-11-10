import threading
import time
from collections import deque

import cv2
import numpy as np


class MotionDetector:
    PARAM_LIMITS = {
        "sensitivity": (5.0, 80.0, float),
        "min_area_ratio": (0.001, 0.5, float),
        "min_motion_intensity": (1.0, 100.0, float),
        "sustained_seconds": (0.2, 10.0, float),
        "window_seconds": (1.0, 30.0, float),
        "min_active_ratio": (0.1, 1.0, float),
        "cooldown_seconds": (5.0, 600.0, float),
        "freeze_seconds": (1.0, 60.0, float),
    }

    def __init__(self, config, camera, on_trigger=None):
        self.config = config
        self.camera = camera
        self.on_trigger = on_trigger
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._background = None
        self._previous_gray = None
        self._previous_at = None
        self._last_sequence = 0
        self._history = deque()
        self._active_since = None
        self._last_trigger = None
        self._status = {"state": "starting", "active": False, "area_ratio": 0.0, "intensity": 0.0}

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motion-detection", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def reset(self, state="waiting"):
        with self._lock:
            self._background = None
            self._previous_gray = None
            self._previous_at = None
            self._history.clear()
            self._active_since = None
            self._status = {"state": state, "active": False, "area_ratio": 0.0, "intensity": 0.0}

    def get_params(self):
        return {key: self.config[key] for key in self.PARAM_LIMITS}

    def update_params(self, updates):
        if not isinstance(updates, dict):
            raise ValueError("参数必须是 JSON 对象")
        applied = {}
        for key, value in updates.items():
            if key not in self.PARAM_LIMITS:
                raise ValueError(f"不支持的参数：{key}")
            low, high, cast = self.PARAM_LIMITS[key]
            if isinstance(value, bool):
                raise ValueError(f"参数 {key} 必须是数字")
            try:
                number = cast(value)
            except (TypeError, ValueError):
                raise ValueError(f"参数 {key} 必须是数字") from None
            if not low <= number <= high:
                raise ValueError(f"参数 {key} 必须在 {low} 到 {high} 之间")
            applied[key] = number
        with self._lock:
            self.config.update(applied)
        self.reset("waiting")
        return applied

    def _roi(self, shape):
        height, width = shape[:2]
        x, y, w, h = self.config["roi"]
        x1, y1 = int(x * width), int(y * height)
        x2, y2 = int((x + w) * width), int((y + h) * height)
        return slice(y1, y2), slice(x1, x2)

    def process_frame(self, frame, timestamp=None):
        now = time.monotonic() if timestamp is None else timestamp
        y_slice, x_slice = self._roi(frame.shape)
        roi = frame[y_slice, x_slice]
        if roi.size == 0:
            return False
        scale = self.config["process_width"] / roi.shape[1]
        size = (self.config["process_width"], max(32, int(roi.shape[0] * scale)))
        gray = cv2.cvtColor(cv2.resize(roi, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        with self._lock:
            background = None if self._background is None else self._background.copy()
            previous_gray = self._previous_gray
            previous_at = self._previous_at
            active_since = self._active_since
            last_trigger = self._last_trigger
        if background is None:
            with self._lock:
                self._background = gray.astype(np.float32)
                self._previous_gray = gray
                self._previous_at = now
                self._status = {"state": "learning", "active": False, "area_ratio": 0.0, "intensity": 0.0}
            return False
        frozen = previous_gray is not None and np.array_equal(gray, previous_gray)
        frozen_for = now - previous_at if frozen and previous_at is not None else 0.0
        if not frozen:
            previous_at = now
        if frozen_for >= self.config["freeze_seconds"]:
            with self._lock:
                self._status = {"state": "frozen", "active": False, "area_ratio": 0.0, "intensity": 0.0}
                self._active_since = None
                self._history.clear()
            return False
        background_u8 = np.clip(background, 0, 255).astype(np.uint8)
        raw_difference = cv2.absdiff(gray, background_u8)
        mean_delta = float(np.mean(gray.astype(np.float32) - background))
        raw_changed_ratio = float(np.count_nonzero(raw_difference > self.config["sensitivity"]) / raw_difference.size)
        if abs(mean_delta) >= self.config["lighting_delta_threshold"] and raw_changed_ratio > 0.65:
            with self._lock:
                self._background = gray.astype(np.float32)
                self._previous_gray = gray
                self._previous_at = now
                self._active_since = None
                self._history.clear()
                self._status = {"state": "lighting_reset", "active": False, "area_ratio": 0.0, "intensity": 0.0}
            return False
        compensated = np.clip(gray.astype(np.float32) - mean_delta, 0, 255).astype(np.uint8)
        difference = cv2.absdiff(compensated, background_u8)
        _, mask = cv2.threshold(difference, self.config["sensitivity"], 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        area_ratio = float(np.count_nonzero(mask) / mask.size)
        intensity = float(cv2.mean(difference, mask=mask)[0]) if area_ratio else 0.0
        if area_ratio > 0.8 and float(np.std(difference)) > 45:
            with self._lock:
                self._background = gray.astype(np.float32)
                self._previous_gray = gray
                self._previous_at = now
                self._active_since = None
                self._history.clear()
                self._status = {"state": "invalid_frame", "active": False, "area_ratio": area_ratio, "intensity": intensity}
            return False
        active = area_ratio >= self.config["min_area_ratio"] and intensity >= self.config["min_motion_intensity"]
        if active and active_since is None:
            active_since = now
        elif not active:
            active_since = None
        with self._lock:
            self._history.append((now, active))
            while self._history and now - self._history[0][0] > self.config["window_seconds"]:
                self._history.popleft()
            history = list(self._history)
        active_ratio = sum(item[1] for item in history) / len(history) if history else 0.0
        sustained = active and active_since is not None and now - active_since >= self.config["sustained_seconds"]
        cooled_down = last_trigger is None or now - last_trigger >= self.config["cooldown_seconds"]
        triggered = sustained and active_ratio >= self.config["min_active_ratio"] and cooled_down
        with self._lock:
            self._background = cv2.addWeighted(background, 1.0 - self.config["background_rate"], gray.astype(np.float32), self.config["background_rate"], 0)
            self._previous_gray = gray
            self._previous_at = previous_at
            self._active_since = active_since
            self._status = {
                "state": "monitoring", "active": bool(sustained and active_ratio >= self.config["min_active_ratio"]),
                "area_ratio": round(area_ratio, 4), "intensity": round(intensity, 2),
                "active_ratio": round(active_ratio, 2), "updated_at": time.time(),
            }
            if triggered:
                self._last_trigger = now
                self._status["last_trigger_at"] = time.time()
        if triggered and self.on_trigger:
            self.on_trigger({"area_ratio": area_ratio, "intensity": intensity, "active_ratio": active_ratio})
        return triggered

    def _run(self):
        while not self._stop.is_set():
            packet = self.camera.get_latest()
            if packet is None:
                self.reset("camera_offline")
                self._stop.wait(0.5)
                continue
            if packet.sequence != self._last_sequence:
                self._last_sequence = packet.sequence
                try:
                    self.process_frame(packet.frame, packet.monotonic_at)
                except (cv2.error, ValueError):
                    self.reset("invalid_frame")
            self._stop.wait(0.04)

    def get_status(self):
        with self._lock:
            return dict(self._status)
