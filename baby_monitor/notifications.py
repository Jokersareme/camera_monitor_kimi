import json
import logging
import queue
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


log = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self, config, state):
        self.config = config["notifications"]
        self._motion_config = config["motion"]
        self.state = state
        self._queue = queue.Queue(maxsize=20)
        self._stop = threading.Event()
        self._thread = None
        self._last_motion = None
        self._last_offline = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="notifications", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=self.config["timeout_seconds"] + 2)

    def _enqueue(self, kind, message):
        try:
            self._queue.put_nowait((kind, message))
            return True
        except queue.Full:
            self.state.record_event("notification", kind, "failed: 通知队列已满")
            return False

    def send_test(self):
        return self._enqueue("测试通知", "婴儿监视系统文字通知测试")

    def send_motion(self):
        preferences = self.state.get_status()
        if not preferences["motion_enabled"] or preferences["mute"]["active"]:
            return False
        now = time.monotonic()
        cooldown = float(self._motion_config.get("cooldown_seconds", 60.0))
        if self._last_motion is not None and now - self._last_motion < cooldown:
            return False
        self._last_motion = now
        return self._enqueue("动作提醒", "检测到宝宝区域持续明显动作，请查看画面")

    def send_offline(self):
        if not self.config["offline_enabled"]:
            return False
        now = time.monotonic()
        if self._last_offline is not None and now - self._last_offline < self.config["offline_cooldown_seconds"]:
            return False
        self._last_offline = now
        return self._enqueue("设备异常", "婴儿监视摄像头持续离线，请检查手机和树莓派连接")

    def _deliver(self, message):
        if self.config["provider"] == "ntfy":
            topic = self.config["ntfy_topic"].strip()
            server = self.config["ntfy_server"].rstrip("/")
            if len(topic) < 16 or topic.startswith("replace-"):
                raise ValueError("请配置至少 16 字符的随机 ntfy 私有主题")
            if not server.startswith(("https://", "http://")):
                raise ValueError("ntfy 服务器地址无效")
            url = server + "/" + quote(topic, safe="")
            request = Request(url, data=message.encode("utf-8"), headers={"Title": "Baby monitor", "Priority": "high", "Content-Type": "text/plain; charset=utf-8"}, method="POST")
        else:
            key = self.config["bark_device_key"].strip()
            server = self.config["bark_server"].rstrip("/")
            if not key:
                raise ValueError("请配置 Bark device key")
            if not server.startswith("https://"):
                raise ValueError("Bark 服务器必须使用 HTTPS")
            payload = json.dumps({"device_key": key, "title": "婴儿监视", "body": message, "group": "baby-monitor"}).encode("utf-8")
            request = Request(server + "/push", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=self.config["timeout_seconds"]) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"HTTP {response.status}")

    def _run(self):
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            kind, message = item
            if kind == "动作提醒":
                preferences = self.state.get_status()
                if not preferences["motion_enabled"] or preferences["mute"]["active"]:
                    self.state.record_event("notification", kind, "suppressed: 开关关闭或静音")
                    continue
            last_error = ""
            attempts = self.config["retries"] + 1
            for attempt in range(attempts):
                try:
                    self._deliver(message)
                    self.state.record_event("notification", kind, "sent")
                    last_error = ""
                    break
                except HTTPError as exc:
                    last_error = f"HTTP {exc.code}"
                    if exc.code < 500 and exc.code not in (408, 429):
                        break
                except Exception as exc:
                    last_error = type(exc).__name__
                if attempt + 1 < attempts:
                    self._stop.wait(self.config["retry_delay_seconds"] * (attempt + 1))
            if last_error:
                log.error("通知发送失败：%s", last_error)
                self.state.record_event("notification", kind, "failed: " + last_error)


class OfflineMonitor:
    def __init__(self, config, camera, state, notifier):
        self.config = config["notifications"]
        self.camera = camera
        self.state = state
        self.notifier = notifier
        self._stop = threading.Event()
        self._thread = None
        self._offline_since = None
        self._offline_event_recorded = False
        self._last_notice = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="offline-monitor", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop.is_set():
            status = self.camera.get_status()
            now = time.time()
            if status["online"]:
                if self._offline_since is not None:
                    self.state.record_event("recovered", "摄像头连接恢复")
                self._offline_since = None
                self._offline_event_recorded = False
                self._last_notice = None
            else:
                if self._offline_since is None:
                    self._offline_since = now
                offline_for = now - self._offline_since
                if offline_for >= self.config["offline_threshold_seconds"] and not self._offline_event_recorded:
                    self.state.record_event("offline", f"摄像头持续离线 {int(offline_for)} 秒")
                    self._offline_event_recorded = True
                cooled = self._last_notice is None or now - self._last_notice >= self.config["offline_cooldown_seconds"]
                if offline_for >= self.config["offline_threshold_seconds"] and cooled:
                    if self.notifier.send_offline():
                        self._last_notice = now
            self._stop.wait(0.5)
