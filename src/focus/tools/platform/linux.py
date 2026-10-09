import io
import shutil
import time

import mss
from PIL import Image
from Xlib import XK, X, display
from Xlib.error import XError
from Xlib.ext import xtest
from Xlib.protocol import event

from focus.tools.platform.base import Platform, PlatformError, WindowInfo


class LinuxPlatform(Platform):
    """behaviour shared by every linux display server"""

    name = "linux"

    protected_processes = frozenset(
        "systemd init xorg x xwayland dbus-daemon dbus-broker sshd login sddm gdm lightdm "
        "pipewire pipewire-pulse wireplumber pulseaudio "
        "i3 i3bar sway hyprland gnome-shell kwin_x11 kwin_wayland plasmashell xfwm4 "
        "openbox bspwm awesome polybar waybar".split()
    )

    def notify(self, title: str, message: str, urgency: str = "normal") -> None:
        self._require("notify-send", "notifications need `notify-send` (libnotify)")
        try:
            self._run("notify-send", "-a", "Focus", "-u", urgency, title, message, timeout=3)
        except PlatformError as e:
            raise PlatformError(
                f"{e}. no notification daemon answered; the user needs one running "
                "(e.g. dunst or mako) for notifications to show"
            ) from e


class X11Platform(LinuxPlatform):
    """screenshots via mss, window management via EWMH, keystrokes via XTest"""

    def __init__(self):
        try:
            self._display = display.Display()
            self._screen_grabber = mss.MSS()
        except Exception as e:
            raise PlatformError(f"couldn't connect to the X server: {e}") from e
        self._root = self._display.screen().root

    def grab_screens(self) -> list[Image.Image]:
        monitors = self._screen_grabber.monitors
        return [
            Image.frombytes("RGB", shot.size, shot.rgb)
            for shot in map(self._screen_grabber.grab, monitors[1:] or monitors[:1])
        ]

    def grab_window(self, window_id: str) -> Image.Image:
        window = self._display.create_resource_object("window", int(window_id, 16))
        try:
            geometry = window.get_geometry()
            origin = self._root.translate_coords(window, 0, 0)
        except XError as e:
            raise PlatformError(f"window {window_id} is gone") from e

        screen = self._screen_grabber.monitors[0]
        left, top = max(origin.x, screen["left"]), max(origin.y, screen["top"])
        right = min(origin.x + geometry.width, screen["left"] + screen["width"])
        bottom = min(origin.y + geometry.height, screen["top"] + screen["height"])
        if right <= left or bottom <= top:
            raise PlatformError(f"window {window_id} is off screen")

        shot = self._screen_grabber.grab(
            {"left": left, "top": top, "width": right - left, "height": bottom - top}
        )
        return Image.frombytes("RGB", shot.size, shot.rgb)

    def list_windows(self) -> list[WindowInfo]:
        client_ids = self._root_property("_NET_CLIENT_LIST")
        active_ids = self._root_property("_NET_ACTIVE_WINDOW")
        active_id = active_ids[0] if active_ids else None

        windows = []
        for window_id in client_ids:
            try:
                windows.append(self._window_info(window_id, focused=window_id == active_id))
            except XError:
                continue
        return windows

    def focus_window(self, window_id: str) -> None:
        window = self._display.create_resource_object("window", int(window_id, 16))
        request = event.ClientMessage(
            window=window,
            client_type=self._atom("_NET_ACTIVE_WINDOW"),
            data=(32, [2, X.CurrentTime, 0, 0, 0]),
        )
        self._root.send_event(
            request, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask
        )
        self._display.sync()
        time.sleep(0.25)

    def send_close_tab_shortcut(self) -> None:
        ctrl = self._display.keysym_to_keycode(XK.XK_Control_L)
        w = self._display.keysym_to_keycode(XK.string_to_keysym("w"))
        for event_type, key in [
            (X.KeyPress, ctrl),
            (X.KeyPress, w),
            (X.KeyRelease, w),
            (X.KeyRelease, ctrl),
        ]:
            xtest.fake_input(self._display, event_type, key)
        self._display.sync()

    def _window_info(self, window_id: int, focused: bool) -> WindowInfo:
        window = self._display.create_resource_object("window", window_id)
        title = window.get_full_text_property(
            self._atom("_NET_WM_NAME"), self._atom("UTF8_STRING")
        )
        wm_class = window.get_wm_class()
        pid = window.get_full_property(self._atom("_NET_WM_PID"), X.AnyPropertyType)
        return WindowInfo(
            id=hex(window_id),
            title=title or window.get_wm_name() or "",
            app=wm_class[1] if wm_class else "unknown",
            pid=int(pid.value[0]) if pid else None,
            focused=focused,
        )

    def _root_property(self, name: str) -> list[int]:
        prop = self._root.get_full_property(self._atom(name), X.AnyPropertyType)
        return list(prop.value) if prop else []

    def _atom(self, name: str) -> int:
        return self._display.get_atom(name)


class WaylandPlatform(LinuxPlatform):
    """
    wayland doesn't let clients inspect or control each other's windows, so this
    backend relies on compositor utilities and can't list or focus windows
    """

    def grab_screens(self) -> list[Image.Image]:
        self._require("grim", "screen capture on wayland needs `grim` (wlroots compositors)")
        return [Image.open(io.BytesIO(self._run("grim", "-t", "png", "-")))]

    def grab_window(self, window_id: str) -> Image.Image:
        raise PlatformError("capturing a single window isn't supported on wayland yet")

    def list_windows(self) -> list[WindowInfo]:
        raise PlatformError("listing windows isn't supported on wayland yet; rely on screenshots")

    def focus_window(self, window_id: str) -> None:
        raise PlatformError("focusing a specific window isn't supported on wayland")

    def send_close_tab_shortcut(self) -> None:
        if shutil.which("wtype"):
            self._run("wtype", "-M", "ctrl", "w", "-m", "ctrl")
        elif shutil.which("ydotool"):
            self._run("ydotool", "key", "29:1", "17:1", "17:0", "29:0")
        else:
            raise PlatformError("sending keystrokes on wayland needs `wtype` or `ydotool`")
