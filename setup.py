"""
py2app build script for NSO GameCube Controller Bridge.

Usage:
  python setup.py py2app        # Build standalone .app (for distribution)
  python setup.py py2app -A      # Alias mode (development, uses source in-place)

For best results, use Python from python.org (not Homebrew) - it includes
Tcl/Tk that bundles correctly. Homebrew Python links to a separate tcl-tk
that py2app may not bundle properly.
"""

import os
from setuptools import setup

APP = ["launcher.py"]
DATA_FILES = ["main.py", "dsu_server.py"]


def _find_tcl_tk_frameworks():
    """Find Tcl/Tk frameworks to bundle. Skip /System (SIP) - use /Library only."""
    frameworks = []
    for fw in ["Tcl.framework", "Tk.framework"]:
        path = os.path.join("/Library/Frameworks", fw)
        if os.path.exists(path):
            frameworks.append(path)
    return frameworks


def _find_tcl_tk_resources():
    """Find Tcl/Tk script directories to bundle (for Homebrew Python etc)."""
    try:
        import tkinter as tk

        tcl = tk.Tcl()
        tcl_lib = tcl.eval("info library")
        base = os.path.dirname(os.path.dirname(tcl_lib))
        resources = list(DATA_FILES)
        for sub in ["tcl9.0", "tcl8.6", "tk9.0", "tk8.6"]:
            path = os.path.join(base, "lib", sub)
            if os.path.isdir(path):
                init = os.path.join(path, "init.tcl") if "tcl" in sub else os.path.join(path, "tk.tcl")
                if os.path.exists(init):
                    resources.append(path)
        return resources
    except Exception:
        return DATA_FILES


OPTIONS = {
    "py2app": {
        "argv_emulation": False,  # Don't use with GUI toolkits
        "resources": _find_tcl_tk_resources(),
        "packages": ["usb", "bleak", "tkinter"],  # Python packages
        "includes": ["tkinter", "hid"],  # hid is C extension (.so); include so it goes to lib-dynload
        "excludes": ["test", "unittest"],  # Exclude stdlib test suite (reduces size, avoids copy issues)
        "frameworks": _find_tcl_tk_frameworks(),  # Bundle Tcl/Tk frameworks
        "plist": {
            "CFBundleName": "NSO GC Bridge",
            "CFBundleDisplayName": "NSO GameCube Controller Bridge",
            "CFBundleIdentifier": "com.nso-gc-bridge.launcher",
            "CFBundleVersion": "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
        },
    }
}

setup(
    name="NSO-GC-Bridge",
    app=APP,
    data_files=[],
    options=OPTIONS,
    setup_requires=["py2app"],
)
