import argparse
import hashlib
import os
from pathlib import Path
from urllib.request import urlopen

import cv2


URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"


def main():
    parser = argparse.ArgumentParser(description="下载并校验 YOLOX-Nano 0.1.1rc0 ONNX")
    parser.add_argument("--output", default="models/yolox_nano.onnx")
    parser.add_argument("--sha256", default=SHA256, required=not bool(SHA256), help="官方模型文件的 SHA-256")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".onnx.tmp")
    digest = hashlib.sha256()
    with urlopen(URL, timeout=60) as response, temporary.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
    actual = digest.hexdigest()
    if args.sha256 and actual != args.sha256.lower():
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"SHA-256 不匹配：期望 {args.sha256}，实际 {actual}")
    cv2.dnn.readNetFromONNX(str(temporary))
    os.replace(temporary, output)
    print(f"模型已下载并通过 OpenCV 加载检查：{output}\nSHA-256: {actual}")


if __name__ == "__main__":
    main()
