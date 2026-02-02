"""
py2app build script for NSO GameCube Controller Bridge.

Usage:
  python setup.py py2app        # Build standalone .app (for distribution)
  python setup.py py2app -A      # Alias mode (development, uses source in-place)
"""

from setuptools import setup

APP = ["launcher.py"]
DATA_FILES = ["main.py", "dsu_server.py"]

OPTIONS = {
    "py2app": {
        "argv_emulation": False,  # Don't use with GUI toolkits
        "resources": DATA_FILES,
        "packages": ["hid", "usb", "bleak"],  # Ensure native deps are bundled
        "excludes": ["test", "unittest"],  # Exclude stdlib test suite (reduces size, avoids copy issues)
        "force_system_tk": True,  # Use system Tcl/Tk so tkinter works in the bundle
        "includes": ["tkinter"],  # Force include tkinter
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
