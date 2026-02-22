# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for FSE Processor (onedir mode)."""

import os
import importlib

# Locate playwright driver directory
playwright_pkg = os.path.dirname(importlib.import_module("playwright").__file__)
playwright_driver = os.path.join(playwright_pkg, "driver")

a = Analysis(
    ["gui.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Include the entire Playwright driver (node.exe + cli.js + browser support)
        (playwright_driver, os.path.join("playwright", "driver")),
        # Include .installed marker for installed-mode detection
        (os.path.join("assets", ".installed"), "."),
        # User guide
        ("guida_utente.html", "."),
    ],
    hiddenimports=[
        "playwright",
        "playwright.sync_api",
        "playwright._impl",
        "playwright._impl._driver",
        "playwright._impl._transport",
        "dotenv",
        "email",
        "email.header",
        "email.utils",
        "PySide6",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "winreg",
        "ctypes",
        "ctypes.wintypes",
        "json",
        "csv",
        "cryptography",
        "cryptography.fernet",
        "cryptography.hazmat.primitives",
        "cryptography.hazmat.primitives.hashes",
        "cryptography.hazmat.primitives.kdf.pbkdf2",
        "cryptography.hazmat.backends",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook_playwright.py"],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FSE Processor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=os.path.join("assets", "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="FSE Processor",
)
