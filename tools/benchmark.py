import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baby_monitor.camera import CameraManager
from baby_monitor.config import load_config
from baby_monitor.detection import PersonDetector


def memory_mb():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="采集与人员推理资源基准")
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    config = load_config()
    camera = CameraManager(config["camera"])
    detector = PersonDetector(config["detection"], camera, config["project_root"])
    camera.start()
    detector.start()
    start_wall = time.monotonic()
    start_cpu = os.times()
    start_cpu_total = start_cpu.user + start_cpu.system
    inference_times = []
    fps_samples = []
    try:
        while time.monotonic() - start_wall < args.seconds:
            time.sleep(0.5)
            camera_status = camera.get_status()
            detection = detector.get_status()
            if camera_status["online"]:
                fps_samples.append(camera_status["fps"])
            if detection.get("inference_ms") is not None:
                inference_times.append(detection["inference_ms"])
        end_cpu = os.times()
        elapsed = time.monotonic() - start_wall
        cpu_seconds = end_cpu.user + end_cpu.system - start_cpu_total
        result = {
            "duration_seconds": round(elapsed, 1),
            "camera": camera.get_status(),
            "average_capture_fps": round(statistics.mean(fps_samples), 2) if fps_samples else 0,
            "detection_state": detector.get_status()["state"],
            "inference_samples": len(inference_times),
            "average_inference_ms": round(statistics.mean(inference_times), 1) if inference_times else None,
            "max_inference_ms": max(inference_times) if inference_times else None,
            "process_cpu_percent_one_core": round(cpu_seconds / elapsed * 100, 1),
            "memory_rss_mb": memory_mb(),
            "pending_frames_at_end": detector.get_status()["pending_frames"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        detector.stop()
        camera.stop()


if __name__ == "__main__":
    main()
