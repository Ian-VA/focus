import os
import tempfile
import time
from collections.abc import Callable

import Quartz
from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication, NSWorkspace
from ApplicationServices import (
    AXIsProcessTrusted,
    AXIsProcessTrustedWithOptions,
    kAXTrustedCheckOptionPrompt,
)
from PIL import Image

from focus.tools.platform.base import Platform, PlatformError, WindowInfo
from focus.utils.log import log, warn

PERMISSIONS = "System Settings > Privacy & Security"
SCREEN_RECORDING_HINT = (
    f"grant the terminal running Focus Screen Recording access in {PERMISSIONS} > Screen Recording"
)
ACCESSIBILITY_HINT = (
    f"grant the terminal running Focus Accessibility access in {PERMISSIONS} > Accessibility"
)
SETTINGS_PANES = {
    "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
}
PERMISSION_WAIT_SECONDS = 180
MAX_DISPLAYS = 16

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
    """
    screenshots via the screencapture utility, window management via Quartz and AppleScript.

    every capture runs in a fresh process so that Screen Recording access, once granted
    to the terminal, applies immediately instead of after the terminal is relaunched
    """

    name = "macOS"

    protected_processes = frozenset(
        "launchd kernel_task windowserver loginwindow dock finder systemuiserver "
        "controlcenter notificationcenter coreservicesuiagent spotlight coreaudiod "
        "cfprefsd distnoted securityd sshd universalaccessd".split()
    )

    def check_permissions(self) -> None:
        if not self._screen_recording_allowed():
            log("Focus needs Screen Recording access to see your screen")
            Quartz.CGRequestScreenCaptureAccess()
            self._open_settings("screen_recording")
            log(
                f"turn it on for your terminal app in {PERMISSIONS} > Screen Recording. "
                "waiting for that... (ctrl+c to give up)"
            )
            try:
                granted = _wait_for(self._screen_recording_allowed)
            except KeyboardInterrupt:
                granted = False
            if not granted:
                raise PlatformError(
                    "Screen Recording access hasn't been enabled yet. turn it on, then quit and "
                    "reopen your terminal app and run focus again"
                )
            log("Screen Recording access granted")

        if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
            log("Focus needs Accessibility access to close tabs and switch windows")
            log(
                f"turn it on for your terminal app in {PERMISSIONS} > Accessibility. "
                "waiting for that... (ctrl+c to continue without it)"
            )
            try:
                granted = _wait_for(AXIsProcessTrusted)
            except KeyboardInterrupt:
                granted = False
            if granted:
                log("Accessibility access granted")
            else:
                warn("continuing without Accessibility access; close_tab won't work until it's enabled")

    def grab_screens(self) -> list[Image.Image]:
        paths = [_temp_png() for _ in range(self._display_count())]
        try:
            self._capture("-x", *paths)
            return [_load(path) for path in paths if os.path.getsize(path)]
        finally:
            for path in paths:
                os.unlink(path)

    def grab_window(self, window_id: str) -> Image.Image:
        path = _temp_png()
        try:
            self._capture("-x", "-o", "-l", window_id, path)
            return _load(path)
        except PlatformError as e:
            raise PlatformError(f"window {window_id} couldn't be captured: {e}") from e
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

    def _capture(self, *args: str) -> None:
        try:
            self._run("screencapture", *args)
        except PlatformError as e:
            if not self._screen_recording_allowed():
                raise PlatformError(f"{e}. {SCREEN_RECORDING_HINT}") from e
            raise

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

    def _open_settings(self, pane: str) -> None:
        try:
            self._run("open", SETTINGS_PANES[pane], timeout=5)
        except PlatformError:
            pass

    @staticmethod
    def _screen_recording_allowed() -> bool:
        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        return True if preflight is None else bool(preflight())

    @staticmethod
    def _display_count() -> int:
        try:
            error, _, count = Quartz.CGGetActiveDisplayList(MAX_DISPLAYS, None, None)
            return max(1, int(count)) if error == 0 else 1
        except Exception:
            return 1

    @staticmethod
    def _frontmost_pid() -> int | None:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None


def _wait_for(check: Callable[[], bool], timeout: float = PERMISSION_WAIT_SECONDS) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(1)
    return False


def _temp_png() -> str:
    fd, path = tempfile.mkstemp(prefix="focus-", suffix=".png")
    os.close(fd)
    return path


def _load(path: str) -> Image.Image:
    with Image.open(path) as image:
        image.load()
        return image.convert("RGB")
