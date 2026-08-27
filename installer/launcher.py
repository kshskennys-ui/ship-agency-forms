import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
HEALTH_URL = "http://127.0.0.1:8000/api/health"
HOME_URL = "http://127.0.0.1:8000/"


def is_running():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=0.8) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def show_error(message):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "船代业务系统", 0x10)
    except Exception:
        pass


def main():
    if not is_running():
        server = ROOT / "ShipAgencyServer.exe"
        if not server.exists():
            show_error(f"找不到服务程序：{server}")
            return 1
        env = os.environ.copy()
        env.update(
            {
                "SHIP_AGENCY_ROOT": str(ROOT),
                "SHIP_AGENCY_DATA_DIR": str(ROOT / "data"),
                "SHIP_AGENCY_TEMPLATE_DIR": str(ROOT / "templates"),
                "SHIP_AGENCY_FRONTEND_DIR": str(ROOT / "frontend"),
                "SHIP_AGENCY_NODE": str(ROOT / "runtime" / "node" / "node.exe"),
                "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "runtime" / "playwright-browsers"),
            }
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(
            [str(server)],
            cwd=str(ROOT),
            env=env,
            creationflags=creation_flags,
            close_fds=True,
        )
        for _ in range(30):
            time.sleep(0.5)
            if is_running():
                break
        else:
            show_error("系统服务启动失败，请检查安装目录或查看 data\\exports。")
            return 1
    webbrowser.open(HOME_URL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
