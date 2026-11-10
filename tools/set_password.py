"""交互式修改 config.json 中的登录账号密码，密码以 SHA-256 哈希存储。

用法：
    .venv\\Scripts\\python.exe tools\\set_password.py

修改后需重启服务生效；已登录的会话会在密码变更后自动失效。
"""

import getpass
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


def main():
    if not CONFIG_PATH.exists():
        sys.exit(f"找不到配置文件：{CONFIG_PATH}，请先复制 config.example.json 为 config.json")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    username = input("账号: ").strip()
    if not username:
        sys.exit("账号不能为空")
    password = getpass.getpass("密码(至少6位): ")
    if len(password) < 6:
        sys.exit("密码至少 6 位")
    auth = config.setdefault("auth", {})
    auth["username"] = username
    auth["password"] = "sha256:" + hashlib.sha256(password.encode("utf-8")).hexdigest()
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {CONFIG_PATH}")
    print("重启服务后生效：停止当前窗口后重新双击 run.bat")


if __name__ == "__main__":
    main()
