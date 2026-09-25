# -*- mode: python ; coding: utf-8 -*-

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

# Collect full packages with assets/drivers
ttk_datas, ttk_binaries, ttk_hidden = collect_all('ttkbootstrap')
playwright_datas, playwright_binaries, playwright_hidden = collect_all('playwright')
docx_datas, docx_binaries, docx_hidden = collect_all('docx')
openpyxl_datas, openpyxl_binaries, openpyxl_hidden = collect_all('openpyxl')

# Project datas
datas = [
    ('config.example.json', '.'),
    ('version.json', '.'),
    ('assets', 'assets'),
]
if os.path.exists('config.json'):
    datas.append(('config.json', '.'))
datas += ttk_datas + playwright_datas + docx_datas + openpyxl_datas

binaries = [] + ttk_binaries + playwright_binaries + docx_binaries + openpyxl_binaries

hiddenimports = [
    'playwright',
    'playwright.sync_api',
    'playwright._impl._driver',
    'ttkbootstrap',
    'ttkbootstrap.constants',
    'docx',
    'openpyxl',
    'rich',
    'requests',
    'pystray',
    'pystray._win32' if sys.platform == 'win32' else 'pystray',
    'pynput',
    'pynput.keyboard',
    'pynput.keyboard._win32',
    'pynput.mouse',
    'pynput.mouse._win32',
    'cryptography',
    'PIL',
    'PIL.Image',
    'PIL.ImageDraw',
] + ttk_hidden + playwright_hidden + docx_hidden + openpyxl_hidden + collect_submodules('modules')

# Deduplicate
hiddenimports = list(dict.fromkeys(hiddenimports))

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Mohra',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico' if os.path.exists('assets/app.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Mohra',
)
