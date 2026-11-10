import argparse
import math
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


scene = "motion"
frame_number = 0


def make_frame(number, selected_scene):
    height, width = 480, 640
    frame = np.full((height, width, 3), (48, 55, 65), dtype=np.uint8)
    cv2.rectangle(frame, (70, 60), (570, 425), (105, 100, 95), -1)
    cv2.rectangle(frame, (70, 60), (570, 425), (180, 180, 175), 5)
    cv2.putText(frame, f"SIM {selected_scene}  {number}", (15, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    if selected_scene in ("motion", "brief"):
        if selected_scene == "motion" or number % 30 < 3:
            x = 250 + int(70 * math.sin(number / 3.0))
            cv2.circle(frame, (x, 230), 55, (80, 170, 225), -1)
            cv2.rectangle(frame, (x - 35, 275), (x + 35, 365), (80, 170, 225), -1)
    elif selected_scene == "outside":
        x = 20 + int(15 * math.sin(number / 3.0))
        cv2.circle(frame, (x, 220), 35, (80, 170, 225), -1)
    elif selected_scene == "noise":
        noise = np.random.default_rng(number).normal(0, 2, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    elif selected_scene == "light":
        delta = 70 if (number // 20) % 2 else 0
        frame = np.clip(frame.astype(np.int16) + delta, 0, 255).astype(np.uint8)
    return frame


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global scene
        parsed = urlparse(self.path)
        if parsed.path == "/control":
            requested = parse_qs(parsed.query).get("scene", [""])[0]
            if requested in ("motion", "static", "brief", "outside", "noise", "light"):
                scene = requested
                self.send_response(204)
                self.end_headers()
            else:
                self.send_error(400)
            return
        if parsed.path != "/video":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        global frame_number
        try:
            while True:
                frame_number += 1
                frame = make_frame(frame_number, scene)
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    continue
                payload = jpeg.tobytes()
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload + b"\r\n")
                self.wfile.flush()
                time.sleep(1.0 / 10.0)
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, fmt, *args):
        return


def main():
    parser = argparse.ArgumentParser(description="本地模拟手机 HTTP MJPEG 视频源")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--scene", default="motion", choices=("motion", "static", "brief", "outside", "noise", "light"))
    args = parser.parse_args()
    scene = args.scene
    print(f"模拟视频：http://{args.host}:{args.port}/video")
    print(f"场景切换：http://{args.host}:{args.port}/control?scene=static|motion|brief|outside|noise|light")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
