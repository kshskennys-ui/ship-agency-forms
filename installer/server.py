import os
import sys
from pathlib import Path


ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
os.environ.setdefault("SHIP_AGENCY_ROOT", str(ROOT))
os.environ.setdefault("SHIP_AGENCY_DATA_DIR", str(ROOT / "data"))
os.environ.setdefault("SHIP_AGENCY_TEMPLATE_DIR", str(ROOT / "templates"))
os.environ.setdefault("SHIP_AGENCY_FRONTEND_DIR", str(ROOT / "frontend"))
os.environ.setdefault("SHIP_AGENCY_NODE", str(ROOT / "runtime" / "node" / "node.exe"))
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "runtime" / "playwright-browsers"))

sys.path.insert(0, str(ROOT / "backend"))

try:
    import uvicorn
    from app.main import app
except Exception as exc:
    data_dir = Path(os.environ.get("SHIP_AGENCY_DATA_DIR", ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "startup_error.log").write_text(repr(exc), encoding="utf-8")
    raise


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.getenv("SHIP_AGENCY_PORT", "8000")),
        log_level="warning",
        log_config=None,
        access_log=False,
    )
