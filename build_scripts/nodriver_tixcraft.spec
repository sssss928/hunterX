# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# PyInstaller Spec File for Hunter - NoDriver Version
# =============================================================================
# This spec file builds the NoDriver version of Hunter.
# Output: dist/nodriver_tixcraft/nodriver_tixcraft.exe
# =============================================================================

import os
import importlib.util
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Get the project root directory (parent of build_scripts)
project_root = os.path.abspath(os.path.join(SPECPATH, '..'))

# Collect ddddocr data files (including .onnx models)
ddddocr_datas = collect_data_files('ddddocr')

# ddddocr imports onnxruntime through its compiled capi extension. Some
# PyInstaller environments collect onnxruntime.dll but miss the .pyd module,
# which disables OCR in the packaged executable.
onnxruntime_binaries = collect_dynamic_libs('onnxruntime')
onnxruntime_pybind_spec = importlib.util.find_spec('onnxruntime.capi.onnxruntime_pybind11_state')
if onnxruntime_pybind_spec and onnxruntime_pybind_spec.origin:
    onnxruntime_binaries.append((onnxruntime_pybind_spec.origin, 'onnxruntime/capi'))

# playsound is distributed as a single playsound.py module. Copy the module as
# data and list it as a hidden import. Do not add the active environment's
# site-packages directory to pathex: PyInstaller already searches its own
# environment, and foreign-environment pathex entries are rejected in
# PyInstaller 7.
playsound_spec = importlib.util.find_spec('playsound')
playsound_datas = []
if playsound_spec and playsound_spec.origin and playsound_spec.origin.endswith('.py'):
    playsound_datas.append((playsound_spec.origin, '.'))

a = Analysis(
    [os.path.join(project_root, 'src', 'nodriver_tixcraft.py')],
    pathex=[os.path.join(project_root, 'src')],
    binaries=onnxruntime_binaries,
    datas=[
        (os.path.join(project_root, 'src', 'assets'), 'assets'),
        (os.path.join(project_root, 'src', 'www'), 'www'),
        # settings.json excluded - program generates it automatically
        # chrome-win64/ excluded - auto-downloaded at runtime if needed
    ] + ddddocr_datas + playsound_datas,
    hiddenimports=[
        # Core dependencies
        'ddddocr',
        'onnxruntime',
        'onnxruntime.capi.onnxruntime_pybind11_state',
        'zendriver',
        'zendriver.cdp',
        'zendriver.core',
        # Shared utilities (important!)
        'util',
        'ocr_cache',
        'performance',
        'NonBrowser',
        'chrome_downloader',
        # Modular architecture
        'nodriver_common',
        'platforms',
        'platforms.facebook',
        'platforms.fansigo',
        'platforms.cityline',
        'platforms.famiticket',
        'platforms.ticketplus',
        'platforms.funone',
        'platforms.kktix',
        'platforms.tixcraft',
        'platforms.ibon',
        'platforms.kham',
        'platforms.hkticketing',
        # Chrome downloader dependencies
        'requests',
        # Image processing
        'PIL',
        'PIL.Image',
        'cv2',
        'numpy',
        # Network
        'urllib3',
        'certifi',
        'cryptography',
        # Others
        'playsound',
        'pyperclip',
        'tornado',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # This enables folder mode
    name='nodriver_tixcraft',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Disable UPX compression for stability
    console=True,  # Show console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, 'src', 'www', 'favicon.ico'),  # Application icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='nodriver_tixcraft',
)
