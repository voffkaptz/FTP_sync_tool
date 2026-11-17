# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
project = Path(SPECPATH)
a = Analysis(
    [str(project / 'ftp_sync_tool.py')],
    pathex=[str(project)],
    binaries=[],
    datas=[(str(project / 'assets'), 'assets')],
    hiddenimports=['fluent_ui'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='ftp_sync_tool', icon=str(project / 'assets' / 'app.ico'),
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, runtime_tmpdir=None, console=False,
    disable_windowed_traceback=False, argv_emulation=False,
    target_arch=None, codesign_identity=None, entitlements_file=None,
)
