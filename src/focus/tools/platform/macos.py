from focus.tools.platform.base import UnsupportedPlatform


class MacOSPlatform(UnsupportedPlatform):
    """
    TODO: implement on top of the shared Platform base.
      - grab_screens: mss works on macOS (needs Screen Recording permission)
      - list_windows / focus_window: Quartz CGWindowListCopyWindowInfo + AppleScript
      - send_close_tab_shortcut: cmd+w via Quartz CGEvent
      - notify: `osascript -e 'display notification ...'`
    """

    def __init__(self):
        super().__init__("macOS")
