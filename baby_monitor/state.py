import json
import os
import threading
import time
from pathlib import Path


class StateStore:
    def __init__(self, storage_config, project_root):
        self._lock = threading.Lock()
        root = Path(project_root)
        self.state_path = root / storage_config["state_file"]
        self.events_path = root / storage_config["events_file"]
        self.max_events = storage_config["max_events"]
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = {"motion_enabled": True, "person_enabled": True, "muted_until": 0.0, "selected_camera": "", "motion_params": {}}
        self._debug_enabled = False
        self._latest = {}
        self._load()

    def _load(self):
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                saved = json.load(handle)
            for key in ("motion_enabled", "person_enabled"):
                if isinstance(saved.get(key), bool):
                    self._state[key] = saved[key]
            muted_until = saved.get("muted_until", 0.0)
            if isinstance(muted_until, (int, float)) and not isinstance(muted_until, bool) and muted_until >= 0:
                self._state["muted_until"] = float(muted_until)
            if isinstance(saved.get("selected_camera"), str):
                self._state["selected_camera"] = saved["selected_camera"]
            if isinstance(saved.get("motion_params"), dict):
                self._state["motion_params"] = {key: value for key, value in saved["motion_params"].items() if isinstance(key, str) and isinstance(value, (int, float)) and not isinstance(value, bool)}
        except FileNotFoundError:
            self._save_locked()
        except (OSError, ValueError, TypeError):
            self._save_locked()
        try:
            with self.events_path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()[-self.max_events:]
            for line in lines:
                try:
                    event = json.loads(line)
                    self._latest[event["kind"]] = event
                except (ValueError, KeyError, TypeError):
                    continue
        except FileNotFoundError:
            pass

    def _save_locked(self):
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self._state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_path)

    def set_preference(self, name, enabled):
        if name not in ("motion_enabled", "person_enabled") or not isinstance(enabled, bool):
            raise ValueError("无效开关值")
        with self._lock:
            self._state[name] = enabled
            self._save_locked()
        return enabled

    def set_selected_camera(self, camera_id):
        with self._lock:
            self._state["selected_camera"] = camera_id
            self._save_locked()

    def set_mute(self, minutes):
        if isinstance(minutes, bool) or minutes not in (0, 5, 15, 30):
            raise ValueError("静音时长只能是 0、5、15 或 30 分钟")
        with self._lock:
            self._state["muted_until"] = time.time() + minutes * 60 if minutes else 0.0
            self._save_locked()
        return self.get_status()["mute"]

    def set_debug(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError("无效调试开关值")
        self._debug_enabled = enabled

    def get_motion_params(self):
        with self._lock:
            return dict(self._state["motion_params"])

    def set_motion_params(self, updates):
        if not isinstance(updates, dict):
            raise ValueError("无效参数")
        with self._lock:
            self._state["motion_params"].update(updates)
            self._save_locked()
            return dict(self._state["motion_params"])

    def get_status(self):
        with self._lock:
            muted_until = self._state["muted_until"]
            remaining = max(0, int(muted_until - time.time() + 0.999))
            return {
                "motion_enabled": self._state["motion_enabled"],
                "person_enabled": self._state["person_enabled"],
                "selected_camera": self._state["selected_camera"],
                "debug_enabled": self._debug_enabled,
                "mute": {
                    "active": remaining > 0,
                    "until": muted_until if remaining else None,
                    "remaining_seconds": remaining,
                },
            }

    def record_event(self, kind, reason, result=None, details=None):
        event = {"time": time.time(), "kind": kind, "reason": str(reason)[:300]}
        if result is not None:
            event["result"] = str(result)[:300]
        if details:
            event["details"] = details
        with self._lock:
            self._latest[kind] = event
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            if self.events_path.stat().st_size > 2_000_000:
                self._trim_events_locked()
        return event

    def _trim_events_locked(self):
        with self.events_path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()[-self.max_events:]
        temporary = self.events_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.writelines(lines)
        os.replace(temporary, self.events_path)

    def latest_events(self):
        with self._lock:
            return {kind: dict(event) for kind, event in self._latest.items()}
