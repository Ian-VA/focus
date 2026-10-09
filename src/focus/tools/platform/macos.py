import os
import tempfile
import time

import mss
import Quartz
from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication, NSWorkspace
from mss.exception import ScreenShotError
from PIL import Image

from focus.tools.platform.base import Platform, PlatformError, WindowInfo, grab_monitors

PERMISSIONS = "System Settings > Privacy & Security"
SCREEN_RECORDING_HINT = (
    f"grant the terminal running Focus Screen Recording access in {PERMISSIONS} > Screen Recording"
)
ACCESSIBILITY_HINT = (
    f"grant the terminal running Focus Accessibility access in {PERMISSIONS} > Accessibility"
)

ACTIVATE_WINDOW_SCRIPT = """
on run argv
    set targetPid to (item 1 of argv) as integer
    set targetTitle to item 2 of argv
    tell application "System Events"
        set targetProcess to first process whose unix id is targetPid
        set frontmost of targetProcess to true
        if targetTitle is not "" then
            try
                perform action "AXRaise" of (first window of targetProcess whose name is targetTitle)
            end try
        end if
    end tell
end run
"""

CLOSE_TAB_SCRIPT = 'tell application "System Events" to keystroke "w" using command down'

NOTIFY_SCRIPT = """
on run argv
    set notificationTitle to item 1 of argv
    set notificationBody to item 2 of argv
    if item 3 of argv is "critical" then
        display notification notificationBody with title notificationTitle sound name "Sosumi"
    else
        display notification notificationBody with title notificationTitle
    end if
end run
"""


class MacOSPlatform(Platform):
    """screenshots via mss and screencapture, window management via Quartz and AppleScript"""

    name = "macOS"

    protected_processes = frozenset(
        "launchd kernel_task windowserver loginwindow dock finder systemuiserver "
        "controlcenter notificationcenter coreservicesuiagent spotlight coreaudiod "
        "cfprefsd distnoted securityd sshd universalaccessd".split()
    )

    def __init__(self):
        try:
            self._screen_grabber = mss.MSS()
        except ScreenShotError as e:
            raise PlatformError(f"couldn't access the display: {e}. {SCREEN_RECORDING_HINT}") from e

    def grab_screens(self) -> list[Image.Image]:
        try:
            return grab_monitors(self._screen_grabber)
        except ScreenShotError as e:
            raise PlatformError(f"screen capture failed: {e}. {SCREEN_RECORDING_HINT}") from e

    def grab_window(self, window_id: str) -> Image.Image:
        fd, path = tempfile.mkstemp(prefix="focus-", suffix=".png")
        os.close(fd)
        try:
            self._run("screencapture", "-x", "-o", "-l", window_id, path)
            with Image.open(path) as image:
                image.load()
                return image.convert("RGB")
        except PlatformError as e:
            raise PlatformError(f"window {window_id} couldn't be captured: {e}. {SCREEN_RECORDING_HINT}") from e
        finally:
            os.unlink(path)

    def list_windows(self) -> list[WindowInfo]:
        options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        entries = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        frontmost_pid = self._frontmost_pid()

        windows, focused_found = [], False
        for entry in entries:
            bounds = entry.get("kCGWindowBounds") or {}
            if (
                int(entry.get("kCGWindowLayer", 0)) != 0
                or float(entry.get("kCGWindowAlpha", 1)) == 0
                or bounds.get("Width", 0) <= 1
                or bounds.get("Height", 0) <= 1
            ):
                continue

            pid = int(entry["kCGWindowOwnerPID"]) if "kCGWindowOwnerPID" in entry else None
            focused = not focused_found and (frontmost_pid is None or pid == frontmost_pid)
            focused_found = focused_found or focused
            windows.append(
                WindowInfo(
                    id=str(int(entry["kCGWindowNumber"])),
                    title=str(entry.get("kCGWindowName") or ""),
                    app=str(entry.get("kCGWindowOwnerName") or "unknown"),
                    pid=pid,
                    focused=focused,
                )
            )
        return windows

    def notify(self, title: str, message: str, urgency: str = "normal") -> None:
        self._osascript(NOTIFY_SCRIPT, title, message, urgency, timeout=5)

    def focus_window(self, window_id: str) -> None:
        window = next((w for w in self.list_windows() if w.id == window_id), None)
        if window is None or window.pid is None:
            raise PlatformError(f"window {window_id} is gone")

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        self._osascript(ACTIVATE_WINDOW_SCRIPT, str(window.pid), window.title)
        time.sleep(0.25)

    def send_close_tab_shortcut(self) -> None:
        self._osascript(CLOSE_TAB_SCRIPT)

    def _osascript(self, script: str, *args: str, timeout: float = 10) -> None:
        command = ["osascript"]
        for line in script.strip().splitlines():
            command += ["-e", line]
        try:
            self._run(*command, *args, timeout=timeout)
        except PlatformError as e:
            if "assistive access" in str(e) or "-1719" in str(e) or "-25211" in str(e):
                raise PlatformError(f"{e}. {ACCESSIBILITY_HINT}") from e
            raise

    @staticmethod
    def _frontmost_pid() -> int | None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None
