#!/usr/bin/env python3
"""
Saved BLE controller storage. Addresses are stable per device; store with names for quick connect.
"""

import json
import os


def _storage_dir():
    """Return config directory for this app."""
    if os.name == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
        return os.path.join(base, "NSO GC Bridge")
    return os.path.expanduser("~/.config/nso-gc-bridge")


def _controllers_path():
    return os.path.join(_storage_dir(), "controllers.json")


def _last_connected_path():
    return os.path.join(_storage_dir(), "last_connected.json")


def load_controllers():
    """Load saved controllers. Returns list of {address, name}."""
    path = _controllers_path()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("controllers", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_controllers(controllers):
    """Save controllers list. controllers = [{address, name}, ...]."""
    path = _controllers_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"controllers": controllers}, f, indent=2)


def add_controller(address, name):
    """Add or update a controller. If address exists, update name."""
    controllers = load_controllers()
    address = address.strip()
    controllers = [c for c in controllers if c["address"] != address]
    controllers.append({"address": address, "name": name.strip() or address})
    save_controllers(controllers)


def remove_controller(address):
    """Remove a controller by address."""
    controllers = [c for c in load_controllers() if c["address"] != address]
    save_controllers(controllers)


def get_last_connected():
    """Return last connected BLE address, or None."""
    path = _last_connected_path()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("address")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def set_last_connected(address):
    """Record the last connected address (called by driver)."""
    path = _last_connected_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"address": address}, f)
