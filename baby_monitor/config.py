import copy
import json
import os
from pathlib import Path


DEFAULT_CONFIG = {
    "server": {"host": "0.0.0.0", "port": 8000},
    "auth": {"username": "", "password": ""},
    "cameras": [],
    "selected_camera": "",
    "camera": {
        "source": "phone", "url": "", "device": 0,
        "width": 640, "height": 480, "fps": 10,
        "reconnect_delay_seconds": 2.0, "open_timeout_ms": 5000,
        "read_timeout_ms": 5000, "jpeg_quality": 75,
    },
    "motion": {
        "roi": [0.1, 0.1, 0.8, 0.8], "process_width": 160,
        "sensitivity": 25, "min_area_ratio": 0.025,
        "min_motion_intensity": 12.0, "sustained_seconds": 2.0,
        "window_seconds": 4.0, "min_active_ratio": 0.5,
        "cooldown_seconds": 60.0, "background_rate": 0.03,
        "lighting_delta_threshold": 18.0, "freeze_seconds": 5.0,
    },
    "detection": {
        "model_path": "models/yolox_nano.onnx", "input_size": 416,
        "confidence_threshold": 0.35, "nms_threshold": 0.45,
        "interval_seconds": 1.0, "stale_seconds": 3.0,
    },
    "notifications": {
        "provider": "ntfy", "ntfy_server": "https://ntfy.sh",
        "ntfy_topic": "", "bark_server": "https://api.day.app",
        "bark_device_key": "", "timeout_seconds": 5.0,
        "retries": 2, "retry_delay_seconds": 2.0,
        "offline_enabled": True, "offline_threshold_seconds": 30.0,
        "offline_cooldown_seconds": 300.0,
    },
    "storage": {
        "state_file": "data/state.json", "events_file": "data/events.jsonl",
        "max_events": 1000,
    },
}

ENV_OVERRIDES = {
    "CAMERA_SOURCE": ("camera", "source"),
    "CAMERA_URL": ("camera", "url"),
    "CAMERA_DEVICE": ("camera", "device"),
    "NTFY_SERVER": ("notifications", "ntfy_server"),
    "NTFY_TOPIC": ("notifications", "ntfy_topic"),
    "BARK_SERVER": ("notifications", "bark_server"),
    "BARK_DEVICE_KEY": ("notifications", "bark_device_key"),
    "NOTIFY_PROVIDER": ("notifications", "provider"),
}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _number(config, section, key, minimum, maximum, integer=False):
    value = config[section][key]
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise ValueError(f"{section}.{key} 必须是数字")
    if not minimum <= value <= maximum:
        raise ValueError(f"{section}.{key} 必须在 {minimum} 到 {maximum} 之间")


def _normalize_cameras(config, allow_legacy):
    configured = config["camera"]["source"] == "usb" or bool(config["camera"]["url"])
    raw_cameras = config["cameras"]
    if not isinstance(raw_cameras, list):
        raise ValueError("cameras 必须是摄像头列表")
    if allow_legacy and not raw_cameras and configured:
        raw_cameras = [{"id": "camera-1", "name": "默认摄像头"}]
    common = copy.deepcopy(config["camera"])
    cameras = []
    ids = set()
    for index, item in enumerate(raw_cameras):
        if not isinstance(item, dict):
            raise ValueError("cameras 中每项必须是 JSON 对象")
        camera = copy.deepcopy(common)
        _merge(camera, item)
        camera["id"] = camera.get("id", f"camera-{index + 1}")
        camera["name"] = camera.get("name", camera["id"])
        if not isinstance(camera["id"], str) or not camera["id"].strip() or len(camera["id"]) > 80:
            raise ValueError("摄像头 id 必须是 1～80 字符的非空字符串")
        if not isinstance(camera["name"], str) or not camera["name"].strip() or len(camera["name"]) > 80:
            raise ValueError("摄像头 name 必须是 1～80 字符的非空字符串")
        if camera["id"] in ids:
            raise ValueError("摄像头 id 不能重复")
        ids.add(camera["id"])
        cameras.append(camera)
    selected = config["selected_camera"] or (cameras[0]["id"] if cameras else "")
    if not isinstance(selected, str) or selected and selected not in ids:
        raise ValueError("selected_camera 必须是 cameras 中存在的 id")
    config["cameras"] = cameras
    config["selected_camera"] = selected
    config["camera"] = next((dict(camera) for camera in cameras if camera["id"] == selected), common)


def validate_config(config):
    for camera in ([config["camera"]] if not config["cameras"] else config["cameras"]):
        if camera["source"] not in ("phone", "usb"):
            raise ValueError("camera.source 只能是 phone 或 usb")
        if camera["source"] == "phone":
            url = camera["url"]
            if url and (not isinstance(url, str) or not url.startswith(("http://", "https://", "rtsp://", "rtsps://"))):
                raise ValueError("phone 模式需要有效的 http(s) 或 rtsp(s) 地址")
            if config["cameras"] and not url:
                raise ValueError("摄像头列表中的 phone 模式必须填写 url")
        device = camera["device"]
        if isinstance(device, bool) or not isinstance(device, (int, str)):
            raise ValueError("camera.device 必须是设备序号或 /dev/videoN 路径")
    for key in ("width", "height"): 
        _number(config, "camera", key, 160, 3840, True)
    _number(config, "camera", "fps", 1, 30, True)
    _number(config, "camera", "reconnect_delay_seconds", 0.2, 60)
    _number(config, "camera", "jpeg_quality", 40, 95, True)
    for key in ("open_timeout_ms", "read_timeout_ms"):
        _number(config, "camera", key, 500, 60000, True)
    roi = config["motion"]["roi"]
    if not isinstance(roi, list) or len(roi) != 4:
        raise ValueError("motion.roi 必须是 [x, y, width, height]")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in roi):
        raise ValueError("motion.roi 必须全是数字")
    x, y, width, height = roi
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError("motion.roi 必须位于归一化画面范围内")
    ranges = {
        "process_width": (80, 640, True), "sensitivity": (1, 100, True),
        "min_area_ratio": (0.001, 1.0, False),
        "min_motion_intensity": (1.0, 100.0, False),
        "sustained_seconds": (0.2, 60.0, False),
        "window_seconds": (0.5, 120.0, False),
        "min_active_ratio": (0.05, 1.0, False),
        "cooldown_seconds": (0.0, 86400.0, False),
        "background_rate": (0.001, 1.0, False),
        "lighting_delta_threshold": (2.0, 100.0, False),
        "freeze_seconds": (1.0, 300.0, False),
    }
    for key, (low, high, integer) in ranges.items():
        _number(config, "motion", key, low, high, integer)
    if config["motion"]["sustained_seconds"] > config["motion"]["window_seconds"]:
        raise ValueError("motion.sustained_seconds 不能大于 window_seconds")
    for key, low, high, integer in (
        ("input_size", 224, 640, True),
        ("confidence_threshold", 0.05, 0.95, False),
        ("nms_threshold", 0.1, 0.9, False),
        ("interval_seconds", 0.2, 30.0, False),
        ("stale_seconds", 0.5, 60.0, False),
    ):
        _number(config, "detection", key, low, high, integer)
    if config["detection"]["input_size"] % 32:
        raise ValueError("detection.input_size 必须是 32 的倍数")
    if config["notifications"]["provider"] not in ("ntfy", "bark"):
        raise ValueError("notifications.provider 只能是 ntfy 或 bark")
    for key, low, high, integer in (
        ("timeout_seconds", 1.0, 30.0, False), ("retries", 0, 5, True),
        ("retry_delay_seconds", 0.1, 60.0, False),
        ("offline_threshold_seconds", 5.0, 3600.0, False),
        ("offline_cooldown_seconds", 5.0, 86400.0, False),
    ):
        _number(config, "notifications", key, low, high, integer)
    _number(config, "server", "port", 1, 65535, True)
    _number(config, "storage", "max_events", 50, 10000, True)
    return config


def load_config(path=None):
    root = Path(__file__).resolve().parent.parent
    config_path = Path(path or os.environ.get("MONITOR_CONFIG", root / "config.json"))
    config = copy.deepcopy(DEFAULT_CONFIG)
    loaded = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError("配置文件顶层必须是 JSON 对象")
        _merge(config, loaded)
    for env_name, (section, key) in ENV_OVERRIDES.items():
        if env_name in os.environ:
            value = os.environ[env_name]
            if env_name == "CAMERA_DEVICE" and value.isdigit():
                value = int(value)
            config[section][key] = value
    camera_override = any(name in os.environ for name in ("CAMERA_SOURCE", "CAMERA_URL", "CAMERA_DEVICE"))
    if camera_override:
        config["cameras"] = []
        config["selected_camera"] = ""
    _normalize_cameras(config, "cameras" not in loaded or camera_override)
    validate_config(config)
    config["project_root"] = str(root)
    return config
