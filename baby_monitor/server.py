import hashlib
import hmac
import os
import threading
import time

import cv2
import numpy as np
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .state import StateStore


class FrameRenderer:
    def __init__(self, config, camera, state, detector):
        self.config = config
        self.camera = camera
        self.state = state
        self.detector = detector
        self._lock = threading.Lock()
        self._cache_key = None
        self._cache_jpeg = None
        self._offline_jpeg = {}

    def _offline(self, state):
        key = state if state in ("connecting", "reconnecting", "unconfigured") else "offline"
        if key not in self._offline_jpeg:
            image = np.full((480, 640, 3), 35, dtype=np.uint8)
            text = {"reconnecting": "CAMERA RECONNECTING", "connecting": "CAMERA CONNECTING", "unconfigured": "NO CAMERA CONFIGURED"}.get(key, "CAMERA OFFLINE")
            cv2.putText(image, text, (80, 225), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (230, 230, 230), 2)
            cv2.putText(image, "No live frame is being shown", (135, 275), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)
            self._offline_jpeg[key] = cv2.imencode(".jpg", image)[1].tobytes()
        return self._offline_jpeg[key]

    def render(self, packet):
        if packet is None:
            status = self.camera.get_status()
            return self._offline(status["state"])
        preferences = self.state.get_status()
        detection = self.detector.get_status() if self.detector else {"updated_at": 0, "boxes": []}
        detection_time = detection.get("updated_at") or 0
        key = (packet.sequence, detection_time, preferences["person_enabled"], preferences["debug_enabled"])
        with self._lock:
            if key == self._cache_key:
                return self._cache_jpeg
        image = packet.frame.copy()
        if preferences["person_enabled"] and detection.get("fresh"):
            for box in detection.get("boxes", []):
                x1, y1, x2, y2 = box["box"]
                cv2.rectangle(image, (x1, y1), (x2, y2), (40, 220, 80), 2)
                cv2.putText(image, f"person {box['confidence']:.2f}", (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (40, 220, 80), 2)
        if preferences["debug_enabled"]:
            roi = self.config["motion"]["roi"]
            h, w = image.shape[:2]
            x, y = int(roi[0] * w), int(roi[1] * h)
            cv2.rectangle(image, (x, y), (int((roi[0] + roi[2]) * w), int((roi[1] + roi[3]) * h)), (60, 180, 255), 2)
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.config["camera"]["jpeg_quality"]])
        if not ok:
            raise RuntimeError("JPEG 编码失败")
        payload = encoded.tobytes()
        with self._lock:
            self._cache_key, self._cache_jpeg = key, payload
        return payload


def create_app(camera, config, motion=None, detector=None, notifier=None, offline_monitor=None, state=None):
    root = config["project_root"]
    app = Flask(__name__, template_folder=f"{root}/templates", static_folder=f"{root}/static")
    app.config["JSON_AS_ASCII"] = False
    state = state or StateStore(config["storage"], root)
    renderer = FrameRenderer(config, camera, state, detector)

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    # ---------- 账号密码登录 ----------
    auth_cfg = config.get("auth") or {}
    auth_user = str(auth_cfg.get("username") or "").strip()
    auth_secret = str(auth_cfg.get("password") or auth_cfg.get("password_sha256") or "")

    def _check_auth(username, password):
        if not auth_user or not auth_secret:
            return True
        if not hmac.compare_digest(str(username).encode("utf-8"), auth_user.encode("utf-8")):
            return False
        if auth_secret.startswith("sha256:"):
            digest = hashlib.sha256(str(password).encode("utf-8")).hexdigest()
            return hmac.compare_digest(digest, auth_secret[7:])
        return hmac.compare_digest(str(password).encode("utf-8"), auth_secret.encode("utf-8"))

    # 密钥由账号密码派生：重启后会话保持有效；改密码后旧会话自动失效
    app.secret_key = hashlib.sha256(f"{auth_user}:{auth_secret}".encode("utf-8")).hexdigest() if auth_user and auth_secret else os.urandom(32).hex()

    @app.before_request
    def require_login():
        if not auth_user or not auth_secret:
            return None
        if session.get("auth_user") == auth_user:
            return None
        if request.endpoint in ("login", "static"):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "未登录"}), 401
        return redirect(url_for("login"))

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if not auth_user or not auth_secret:
            return redirect(url_for("index"))
        error = ""
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if _check_auth(username, password):
                session.clear()
                session["auth_user"] = auth_user
                return redirect(url_for("index"))
            time.sleep(1.0)
            error = "账号或密码错误"
        return render_template("login.html", error=error)

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/video.mjpeg")
    def video():
        def generate():
            last_key = None
            while True:
                packet = camera.get_latest(copy_frame=False)
                key = packet.sequence if packet else "offline-" + camera.get_status()["state"]
                if key != last_key:
                    try:
                        payload = renderer.render(packet)
                    except Exception:
                        app.logger.exception("画面编码失败")
                        break
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload + b"\r\n"
                    last_key = key
                time.sleep(0.03 if packet else 0.5)
        return app.response_class(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/status")
    def status():
        return jsonify({
            "camera": camera.get_status(),
            "cameras": camera.list_cameras(),
            "motion": motion.get_status() if motion else {"state": "starting"},
            "detection": detector.get_status() if detector else {"state": "unavailable", "boxes": []},
            "preferences": state.get_status(),
            "events": state.latest_events(),
        })

    def preference(name):
        if request.method == "GET":
            return jsonify({"enabled": state.get_status()[name]})
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return jsonify({"error": "enabled 必须是布尔值"}), 400
        state.set_preference(name, body["enabled"])
        return jsonify({"enabled": body["enabled"]})

    @app.route("/api/settings/motion", methods=("GET", "POST"))
    def motion_setting():
        return preference("motion_enabled")

    @app.route("/api/settings/person", methods=("GET", "POST"))
    def person_setting():
        return preference("person_enabled")

    @app.route("/api/settings/motion-params", methods=("GET", "POST"))
    def motion_params():
        if motion is None:
            return jsonify({"error": "动作检测模块未启动"}), 503
        if request.method == "GET":
            return jsonify({"current": motion.get_params(), "saved": state.get_motion_params()})
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "需要 JSON 请求"}), 400
        try:
            applied = motion.update_params(body)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        state.set_motion_params(applied)
        return jsonify({"applied": applied, "current": motion.get_params()})

    @app.post("/api/cameras/select")
    def select_camera():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "需要 JSON 请求"}), 400
        try:
            selected = camera.select(body.get("id"))
            state.set_selected_camera(body["id"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503
        if motion:
            motion.reset("waiting")
        if detector:
            detector.reset()
        return jsonify({"selected_id": body["id"], "camera": selected})

    @app.post("/api/mute")
    def mute():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "需要 JSON 请求"}), 400
        try:
            return jsonify(state.set_mute(body.get("minutes")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/settings/debug")
    def debug_setting():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            return jsonify({"error": "enabled 必须是布尔值"}), 400
        state.set_debug(body["enabled"])
        return jsonify({"enabled": body["enabled"]})

    @app.post("/api/notify/test")
    def notify_test():
        if notifier is None:
            return jsonify({"error": "通知模块未启动"}), 503
        notifier.send_test()
        return jsonify({"queued": True}), 202

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "接口不存在"}), 404

    @app.errorhandler(500)
    def internal_error(_):
        return jsonify({"error": "服务内部错误"}), 500

    return app
