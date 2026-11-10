import logging

from baby_monitor.camera import CameraManager
from baby_monitor.config import load_config
from baby_monitor.detection import PersonDetector
from baby_monitor.motion import MotionDetector
from baby_monitor.notifications import NotificationManager, OfflineMonitor
from baby_monitor.server import create_app
from baby_monitor.state import StateStore


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    state = StateStore(config["storage"], config["project_root"])
    selected_camera = state.get_status()["selected_camera"] or config["selected_camera"]
    try:
        camera = CameraManager(config["camera"], config["cameras"], selected_camera)
    except ValueError:
        camera = CameraManager(config["camera"], config["cameras"], config["selected_camera"])
    notifier = NotificationManager(config, state)
    detector = PersonDetector(config["detection"], camera, config["project_root"], lambda: state.get_status()["person_enabled"])

    def on_motion(details):
        state.record_event("motion", "监视区域持续明显动作", details={key: round(value, 4) for key, value in details.items()})
        preferences = state.get_status()
        if camera.get_status()["online"] and preferences["motion_enabled"] and not preferences["mute"]["active"]:
            notifier.send_motion()

    motion = MotionDetector(config["motion"], camera, on_motion)
    saved_params = state.get_motion_params()
    if saved_params:
        applied = motion.update_params(saved_params)
        if applied:
            logging.getLogger(__name__).info("已恢复保存的动作检测参数：%s", applied)
    offline = OfflineMonitor(config, camera, state, notifier)
    app = create_app(camera, config, motion, detector, notifier, offline, state)
    camera.start()
    notifier.start()
    detector.start()
    motion.start()
    offline.start()
    try:
        app.run(host=config["server"]["host"], port=config["server"]["port"], threaded=True, use_reloader=False)
    finally:
        offline.stop()
        motion.stop()
        detector.stop()
        notifier.stop()
        camera.stop()


if __name__ == "__main__":
    main()
