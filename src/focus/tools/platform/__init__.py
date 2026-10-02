import os
import sys
from functools import cache

from focus.tools.platform.base import (
    KillReport,
    Platform,
    PlatformError,
    UnsupportedPlatform,
    WindowInfo,
)


@cache
def get_platform() -> Platform:
    """the Platform implementation for the OS and display server Focus is running on"""
    if sys.platform.startswith("linux"):
        if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland":
            from focus.tools.platform.linux import WaylandPlatform

            return WaylandPlatform()

        from focus.tools.platform.linux import X11Platform

        return X11Platform()

    if sys.platform == "darwin":
        from focus.tools.platform.macos import MacOSPlatform

        return MacOSPlatform()

    if sys.platform == "win32":
        from focus.tools.platform.windows import WindowsPlatform

        return WindowsPlatform()

    return UnsupportedPlatform(sys.platform)


__all__ = ["get_platform", "KillReport", "Platform", "PlatformError", "WindowInfo"]
