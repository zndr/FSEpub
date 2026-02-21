"""PyInstaller runtime hook: fix Playwright driver path in frozen mode."""

import os
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
