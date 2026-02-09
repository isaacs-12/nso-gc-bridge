"""
Optional version check against GitHub releases. Does not require network for app operation.
Use check_for_updates() from the launcher to compare current version with latest release.
"""

import re
import os

# Single source for version when not running from a built .app (plist has it when frozen).
VERSION = os.environ.get("VERSION", "1.0.0")

# GitHub repo for releases (owner/repo).
GITHUB_REPO = "isaacs-12/nso-gc-bridge"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# Request timeout (seconds) so we never block the app.
CHECK_TIMEOUT_SEC = 8


def _parse_version(s):
    """Parse version string (e.g. '1.2.3' or 'v1.2.3') into tuple (major, minor, patch)."""
    s = (s or "").strip().lstrip("v")
    parts = re.split(r"[.-]", s.split("-")[0])[:3]  # ignore prerelease suffix
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _version_less(a_str, b_str):
    """Return True if version a_str is strictly less than b_str."""
    return _parse_version(a_str) < _parse_version(b_str)


def get_latest_release():
    """
    Fetch latest release tag from GitHub API. Does not require network for app to run.
    Returns (latest_version_str, releases_page_url) or None on any error (no network, timeout, etc.).
    """
    try:
        import urllib.request
        import json

        req = urllib.request.Request(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "NSO-GC-Bridge-Updater"},
        )
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = (data.get("tag_name") or "").strip().lstrip("v")
        if not tag:
            return None
        return (tag, GITHUB_RELEASES_URL)
    except Exception:
        return None


def is_newer_available(current_version, latest_version):
    """Return True if latest_version is strictly newer than current_version."""
    return _version_less(current_version, latest_version)


def check_for_updates(current_version):
    """
    Optional update check. Call from UI (e.g. menu or button); never blocks app startup.
    Returns (is_newer, latest_version, url) if check succeeded:
      - is_newer: True if latest_version > current_version
      - latest_version: tag string (e.g. '1.0.1')
      - url: releases page URL
    Returns None if check failed (no network, timeout, API error).
    """
    result = get_latest_release()
    if result is None:
        return None
    latest_version, url = result
    return (is_newer_available(current_version, latest_version), latest_version, url)
