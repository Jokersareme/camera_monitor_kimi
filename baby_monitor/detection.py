import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np


log = logging.getLogger(__name__)


class PersonDetector:
    def __init__(self, config, camera, project_root, enabled_provider=None):
        self.config = config
        self.camera = camera
        self.model_path = Path(project_root) / config["model_path"]
        self.enabled_provider = enabled_provider or (lambda: True)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._net = None
        self._last_attempt = 0.0
        self._last_sequence = 0
        self._status = {"state": "loading", "boxes": [], "count": 0, "updated_at": None, "inference_ms": None}

    def _load(self):
        self._last_attempt = time.monotonic()
        self._net = cv2.dnn.readNetFromONNX(str(self.model_path))
        self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    @staticmethod
    def _decode(output, confidence_threshold, input_size=416):
        predictions = np.squeeze(output, axis=0)
        if predictions.ndim != 2 or predictions.shape[1] < 85:
            raise ValueError("模型输出形状不符合 YOLOX 80 类别格式")
        predictions = predictions[:, :85]
        strides = (8, 16, 32)
        grids = []
        expanded_strides = []
        for stride in strides:
            grid_size = input_size // stride
            yv, xv = np.meshgrid(np.arange(grid_size), np.arange(grid_size), indexing="ij")
            grid = np.stack((xv, yv), axis=2).reshape(1, -1, 2)
            grids.append(grid)
            expanded_strides.append(np.full((1, grid.shape[1], 1), stride))
        grid = np.concatenate(grids, axis=1)
        stride = np.concatenate(expanded_strides, axis=1)
        if predictions.shape[0] != grid.shape[1]:
            raise ValueError("模型输入尺寸或输出候选数不匹配")
        boxes = predictions.copy()
        boxes[:, :2] = (boxes[:, :2] + grid[0]) * stride[0]
        boxes[:, 2:4] = np.exp(np.clip(boxes[:, 2:4], -20, 20)) * stride[0]
        scores = boxes[:, 4] * boxes[:, 5]
        keep = scores >= confidence_threshold
        return boxes[keep], scores[keep]

    def detect(self, frame):
        if self._net is None:
            self._load()
        height, width = frame.shape[:2]
        input_size = self.config["input_size"]
        ratio = min(input_size / width, input_size / height)
        resized_width, resized_height = int(width * ratio), int(height * ratio)
        resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        padded = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
        padded[:resized_height, :resized_width] = resized
        blob = cv2.dnn.blobFromImage(padded, scalefactor=1.0, size=(input_size, input_size), swapRB=False, crop=False)
        self._net.setInput(blob)
        output = self._net.forward()
        boxes, scores = self._decode(output, self.config["confidence_threshold"], input_size)
        xywh = []
        for box in boxes:
            x = box[0] - box[2] / 2
            y = box[1] - box[3] / 2
            xywh.append([float(x), float(y), float(box[2]), float(box[3])])
        indices = cv2.dnn.NMSBoxes(xywh, scores.tolist(), self.config["confidence_threshold"], self.config["nms_threshold"])
        results = []
        if len(indices):
            for index in np.asarray(indices).reshape(-1):
                x, y, w, h = xywh[index]
                x1, y1 = x / ratio, y / ratio
                x2, y2 = (x + w) / ratio, (y + h) / ratio
                results.append({
                    "box": [max(0, int(x1)), max(0, int(y1)), min(width - 1, int(x2)), min(height - 1, int(y2))],
                    "confidence": round(float(scores[index]), 4),
                })
        results.sort(key=lambda item: item["confidence"], reverse=True)
        return results

    def reset(self):
        with self._lock:
            self._last_sequence = 0
            self._status = {"state": "running" if self._net is not None else "loading", "boxes": [], "count": 0, "updated_at": None, "inference_ms": None}

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="person-detection", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self):
        while not self._stop.is_set():
            if not self.enabled_provider():
                with self._lock:
                    self._status["state"] = "disabled"
                    self._status["boxes"] = []
                    self._status["count"] = 0
                self._stop.wait(0.3)
                continue
            if self._net is None:
                if time.monotonic() - self._last_attempt < 10:
                    self._stop.wait(0.5)
                    continue
                try:
                    self._load()
                    with self._lock:
                        self._status["state"] = "running"
                except (cv2.error, OSError, ValueError):
                    log.exception("人员模型加载失败")
                    with self._lock:
                        self._status = {"state": "unavailable", "boxes": [], "count": 0, "updated_at": None, "inference_ms": None}
                    self._stop.wait(1)
                    continue
            packet = self.camera.get_latest()
            now = time.monotonic()
            if packet is None or packet.sequence == self._last_sequence:
                self._stop.wait(0.15)
                continue
            last_inference = self._status.get("last_inference_monotonic", 0)
            if now - last_inference < self.config["interval_seconds"]:
                self._stop.wait(0.05)
                continue
            self._last_sequence = packet.sequence
            started = time.monotonic()
            status_before = self._status
            try:
                boxes = self.detect(packet.frame)
                elapsed = (time.monotonic() - started) * 1000
                with self._lock:
                    if self._status is not status_before:
                        continue
                    self._status = {
                        "state": "running", "boxes": boxes, "count": len(boxes),
                        "updated_at": time.time(), "inference_ms": round(elapsed, 1),
                        "processed_sequence": packet.sequence, "last_inference_monotonic": time.monotonic(),
                    }
            except (cv2.error, OSError, ValueError):
                log.exception("人员推理失败")
                self._net = None
                with self._lock:
                    self._status = {"state": "unavailable", "boxes": [], "count": 0, "updated_at": None, "inference_ms": None}
                self._stop.wait(1)

    def get_status(self):
        with self._lock:
            status = {key: value for key, value in self._status.items() if key != "last_inference_monotonic"}
        fresh = bool(self.enabled_provider() and status.get("updated_at") and time.time() - status["updated_at"] <= self.config["stale_seconds"])
        status["fresh"] = fresh
        if not fresh:
            status["boxes"] = []
            status["count"] = 0
            if self.enabled_provider() and status["state"] == "running":
                status["state"] = "waiting"
        packet = self.camera.get_latest(copy_frame=False)
        status["pending_frames"] = max(0, packet.sequence - status.get("processed_sequence", 0)) if packet else 0
        return status
