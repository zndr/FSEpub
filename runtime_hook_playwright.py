"""PyInstaller runtime hook: fix Playwright driver path and hide console windows."""

import os
import subprocess
import sys

if getattr(sys, "frozen", False):
    # In frozen mode, data files are in _internal/ (PyInstaller 6.x onedir)
    _bundle_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    _driver_dir = os.path.join(_bundle_dir, "playwright", "driver")
    _node_exe = os.path.join(_driver_dir, "node.exe")
    _cli_js = os.path.join(_driver_dir, "package", "cli.js")

    if os.path.exists(_node_exe):
        # Set env var so Playwright uses our bundled node
        os.environ["PLAYWRIGHT_NODEJS_PATH"] = _node_exe

        # Also monkey-patch the function for any code that calls it directly
        try:
            from playwright._impl import _driver

            def _patched_compute_driver_executable():
                return (_node_exe, _cli_js)

            _driver.compute_driver_executable = _patched_compute_driver_executable
        except ImportError:
            pass

    # Prevent console window flash when launching subprocesses (node.exe etc.)
    # Playwright uses STARTF_USESHOWWINDOW + SW_HIDE but not CREATE_NO_WINDOW;
    # without CREATE_NO_WINDOW, Windows still briefly allocates a console.
    if sys.platform == "win32":
        _orig_Popen_init = subprocess.Popen.__init__

        def _no_window_Popen_init(self, *args, **kwargs):
            if "creationflags" not in kwargs or kwargs["creationflags"] == 0:
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            _orig_Popen_init(self, *args, **kwargs)

        subprocess.Popen.__init__ = _no_window_Popen_init
