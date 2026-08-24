"""Filesystem locations shared by normal and packaged deployments."""

import os
import sys
from pathlib import Path


def _runtime_root() -> Path:
    configured = os.getenv("SHIP_AGENCY_ROOT")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


ROOT = _runtime_root()
DATA_DIR = Path(os.getenv("SHIP_AGENCY_DATA_DIR", ROOT / "data")).resolve()
TEMPLATE_DIR = Path(os.getenv("SHIP_AGENCY_TEMPLATE_DIR", ROOT / "templates")).resolve()
FRONTEND_DIR = Path(os.getenv("SHIP_AGENCY_FRONTEND_DIR", ROOT / "frontend")).resolve()
EXPORT_DIR = DATA_DIR / "exports"
HELPER_DIR = ROOT / "runtime" / "exporters"

DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
