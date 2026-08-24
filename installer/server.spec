from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parent
hiddenimports = (
    collect_submodules("rapidocr_onnxruntime")
    + collect_submodules("onnxruntime")
    + collect_submodules("uvicorn")
)

a = Analysis(
    [str(ROOT / "installer" / "server.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ShipAgencyServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
