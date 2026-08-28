from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


ROOT = Path(SPECPATH).parent
hiddenimports = (
    collect_submodules("app")
    + collect_submodules("ddddocr")
    + collect_submodules("onnxruntime")
    + collect_submodules("playwright")
)
datas = collect_data_files("ddddocr") + collect_data_files("playwright")
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    [str(ROOT / "local_verifier" / "agent.py")],
    pathex=[str(ROOT / "backend")],
    binaries=binaries,
    datas=datas,
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
    name="ShipAgencySeafarerAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
