# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# PyInstaller Spec File for Hunter - Settings Editor (Tornado Web)
# =============================================================================
# This spec file builds the Tornado web-based settings editor.
# Output: dist/settings/settings.exe
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

# playsound is imported lazily by util.py. Copy the single-file module and list
# it as a hidden import without adding site-packages to pathex; PyInstaller
# already searches its own environment and PyInstaller 7 rejects such entries.
playsound_spec = importlib.util.find_spec('playsound')
playsound_datas = []
if playsound_spec and playsound_spec.origin and playsound_spec.origin.endswith('.py'):
    playsound_datas.append((playsound_spec.origin, '.'))

a = Analysis(
    [os.path.join(project_root, 'src', 'settings.py')],
    pathex=[os.path.join(project_root, 'src')],
    binaries=onnxruntime_binaries,
    datas=[
        (os.path.join(project_root, 'src', 'www'), 'www'),
        (os.path.join(project_root, 'src', 'assets'), 'assets'),
        # settings.json excluded - program generates it automatically
    ] + ddddocr_datas + playsound_datas,
    hiddenimports=[
        # Tornado web framework
        'tornado',
        'tornado.web',
        'tornado.ioloop',
        'tornado.httpserver',
        'tornado.websocket',
        # Shared utilities (important!)
        'util',
        'NonBrowser',
        # Optional: ddddocr (if settings.py uses it)
        'ddddocr',
        'onnxruntime',
        'onnxruntime.capi.onnxruntime_pybind11_state',
        # Image processing (if needed)
        'PIL',
        'PIL.Image',
        'numpy',
        # Others
        'json',
        'base64',
        'webbrowser',
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
    name='settings',  # Output: settings.exe
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Disable UPX compression for stability
    console=True,  # Show console window for Tornado logs
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
    name='settings',
)
