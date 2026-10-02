from focus.tools.platform.base import UnsupportedPlatform


class WindowsPlatform(UnsupportedPlatform):
    """
    TODO: implement on top of the shared Platform base.
      - grab_screens: mss works on Windows
      - list_windows / focus_window: EnumWindows + SetForegroundWindow (pywin32 / ctypes)
      - send_close_tab_shortcut: ctrl+w via SendInput
      - notify: toast notifications (e.g. windows-toasts)
    """

    def __init__(self):
        super().__init__("Windows")
