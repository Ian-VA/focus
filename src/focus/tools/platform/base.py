import shutil
import subprocess
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass

import psutil
from PIL import Image


class PlatformError(Exception):
    """raised when the OS can't do what was asked (missing utility, no display, ...)"""


@dataclass
class WindowInfo:
    id: str
    title: str
    app: str
    pid: int | None
    focused: bool = False


@dataclass
class KillReport:
    killed: list[str]
    refused: list[str]


class Platform(ABC):
    name: str = "unknown"

    protected_processes: frozenset[str] = frozenset()

    @abstractmethod
    def grab_screens(self) -> list[Image.Image]:
        """one image per monitor"""

    @abstractmethod
    def grab_window(self, window_id: str) -> Image.Image:
        """capture a single window by the id returned from list_windows"""

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]:
        """open top-level windows, with the focused one marked"""

    @abstractmethod
    def notify(self, title: str, message: str, urgency: str = "normal") -> None:
        """show a system notification. urgency is one of low/normal/critical"""

    @abstractmethod
    def focus_window(self, window_id: str) -> None:
        """raise and focus a window by the id returned from list_windows"""

    @abstractmethod
    def send_close_tab_shortcut(self) -> None:
        """press the OS's close-tab shortcut (ctrl+w, cmd+w, ...) in the focused window"""

    def check_permissions(self) -> None:
        """ask the OS for whatever access Focus needs before a session starts. raises
        PlatformError when the user has to do something and run Focus again"""

    def focused_window(self) -> WindowInfo | None:
        return next((window for window in self.list_windows() if window.focused), None)

    def close_tab(self, window_id: str | None = None) -> None:
        if window_id is not None:
            self.focus_window(window_id)
        self.send_close_tab_shortcut()

    def kill_app(self, name: str | None = None, pid: int | None = None) -> KillReport:
        if name is None and pid is None:
            raise PlatformError("give either an app name or a pid")

        me = psutil.Process()
        user = me.username()
        protected_pids = {me.pid, *(parent.pid for parent in me.parents())}
        targets, refused = [], []

        for proc in psutil.process_iter(["pid", "name", "username"]):
            proc_pid, proc_name = proc.info["pid"], (proc.info["name"] or "").lower()
            matches = proc_pid == pid or (name is not None and proc_name == name.lower())
            if not matches:
                continue

            is_protected = (
                proc_pid in protected_pids
                or proc_name in self.protected_processes
                or proc.info["username"] != user
            )
            (refused if is_protected else targets).append(proc)

        for proc in targets:
            with suppress(psutil.NoSuchProcess):
                proc.terminate()
        _, still_alive = psutil.wait_procs(targets, timeout=3)
        for proc in still_alive:
            with suppress(psutil.NoSuchProcess):
                proc.kill()

        return KillReport(killed=_labels(targets), refused=_labels(refused))

    @staticmethod
    def _require(command: str, reason: str) -> None:
        if not shutil.which(command):
            raise PlatformError(f"{reason} installed")

    @staticmethod
    def _run(*cmd: str, timeout: float = 10) -> bytes:
        try:
            return subprocess.run(cmd, check=True, capture_output=True, timeout=timeout).stdout
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode(errors="replace").strip()
            raise PlatformError(f"`{cmd[0]}` failed: {stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise PlatformError(f"`{cmd[0]}` timed out") from e


class UnsupportedPlatform(Platform):
    """placeholder for OSes that don't have an implementation yet"""

    def __init__(self, os_name: str):
        self.name = os_name

    def _unsupported(self):
        raise PlatformError(f"{self.name} is not supported by Focus yet")

    def grab_screens(self):
        self._unsupported()

    def grab_window(self, window_id):
        self._unsupported()

    def list_windows(self):
        self._unsupported()

    def notify(self, title, message, urgency="normal"):
        self._unsupported()

    def focus_window(self, window_id):
        self._unsupported()

    def send_close_tab_shortcut(self):
        self._unsupported()


def _labels(procs: list[psutil.Process]) -> list[str]:
    return [f"{p.info['name']} (pid {p.info['pid']})" for p in procs]
