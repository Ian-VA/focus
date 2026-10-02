import time
from typing import Any

from focus.tools.base import Screenshot, Tool, ToolResult, schema, tool
from focus.tools.platform import PlatformError, get_platform

MAX_WAIT_SECONDS = 60


@tool(
    "Take a screenshot of every monitor and return the images. This is your primary way "
    "of seeing what the user is doing. Call it again after any action (closing a tab, "
    "killing an app) to confirm the result."
)
def capture_screen() -> ToolResult:
    shots = [
        Screenshot.from_image(image, label=f"monitor {i}")
        for i, image in enumerate(get_platform().grab_screens(), start=1)
    ]
    sizes = ", ".join(f"{s.label}: {s.width}x{s.height}" for s in shots)
    return ToolResult(
        f"captured {len(shots)} screen(s) at {time.strftime('%H:%M:%S')} ({sizes})",
        images=shots,
    )


@tool(
    "List the user's open windows with their window id, app name, process id, and title. "
    "The focused window is marked with '*'. Browser window titles usually contain the "
    "active tab's page title, which helps tell productive browsing from distraction. Use "
    "the ids/pids/app names here for close_tab and kill_app."
)
def list_windows() -> ToolResult:
    windows = get_platform().list_windows()
    if not windows:
        return ToolResult("no open windows found")

    lines = [
        f"{'*' if w.focused else ' '} id={w.id} app={w.app} pid={w.pid} title={w.title!r}"
        for w in windows
    ]
    return ToolResult("open windows (* = focused):\n" + "\n".join(lines))


@tool(
    "Show the user a desktop notification. This is the gentlest intervention: use it "
    "first to nudge the user back on task before taking stronger action. Keep messages "
    "short, specific, and friendly.",
    schema(
        required=("title", "message"),
        title={"type": "string", "description": "short notification title"},
        message={"type": "string", "description": "notification body, one or two sentences"},
        urgency={
            "type": "string",
            "enum": ["low", "normal", "critical"],
            "description": "critical notifications stay on screen until dismissed",
        },
    ),
)
def notify_user(title: str, message: str, urgency: str = "normal") -> ToolResult:
    get_platform().notify(title, message, urgency)
    return ToolResult(f"notification shown: {title!r}")


@tool(
    "Close the active tab of a browser (or other tabbed app) by focusing the window and "
    "pressing the close-tab keyboard shortcut. Only use this when the distracting content "
    "is the ACTIVE tab of that window, as seen in a screenshot or the window title. In "
    "non-tabbed apps the same shortcut may close the whole window or document, so don't "
    "use it there.",
    schema(
        window_id={
            "type": "string",
            "description": "window id from list_windows. omit to target the focused window",
        },
    ),
)
def close_tab(window_id: str | None = None) -> ToolResult:
    get_platform().close_tab(window_id)
    target = f"window {window_id}" if window_id else "the focused window"
    return ToolResult(
        f"sent the close-tab shortcut to {target}. capture the screen to confirm it worked."
    )


@tool(
    "Terminate an application by process name (kills every process with that exact name, "
    "e.g. 'Discord') or by pid. This is the strongest intervention; reserve it for clear, "
    "repeated distraction after the user has already been warned. System processes, Focus "
    "itself, and the terminal it runs in are protected and will be refused.",
    schema(
        name={"type": "string", "description": "exact process name, case-insensitive"},
        pid={"type": "integer", "description": "a specific process id"},
    ),
)
def kill_app(name: str | None = None, pid: int | None = None) -> ToolResult:
    report = get_platform().kill_app(name=name, pid=pid)
    if not report.killed and not report.refused:
        return ToolResult(
            f"no running process matched {name or pid}. "
            "use list_windows to find the right app name or pid.",
            is_error=True,
        )

    lines = []
    if report.killed:
        lines.append("killed: " + ", ".join(report.killed))
    if report.refused:
        lines.append(
            "refused (protected system process, Focus itself, or another user's): "
            + ", ".join(report.refused)
        )
    return ToolResult("\n".join(lines), is_error=not report.killed)


@tool(
    f"Pause for a few seconds (1-{MAX_WAIT_SECONDS}) before looking again. Use this when "
    "you suspect distraction but aren't sure yet, e.g. the user might be briefly checking "
    "a message or looking something up, then capture_screen again to see whether it "
    "continued.",
    schema(
        required=("seconds",),
        seconds={"type": "number", "minimum": 1, "maximum": MAX_WAIT_SECONDS},
    ),
)
def wait(seconds: float) -> ToolResult:
    seconds = max(1.0, min(float(seconds), MAX_WAIT_SECONDS))
    time.sleep(seconds)
    return ToolResult(f"waited {seconds:g} seconds")


TOOLS: list[Tool] = [capture_screen, list_windows, notify_user, close_tab, kill_app, wait]
TOOLS_BY_NAME: dict[str, Tool] = {t.name: t for t in TOOLS}


def run_tool(name: str, args: dict[str, Any]) -> ToolResult:
    """run a tool call from any provider. failures become error results the model can
    read and react to, rather than exceptions that end the check-in"""
    if name not in TOOLS_BY_NAME:
        return ToolResult(f"unknown tool: {name}", is_error=True)

    try:
        return TOOLS_BY_NAME[name].handler(**args)
    except PlatformError as e:
        return ToolResult(str(e), is_error=True)
    except TypeError as e:
        return ToolResult(f"bad arguments for {name}: {e}", is_error=True)
    except Exception as e:
        return ToolResult(f"{name} failed: {type(e).__name__}: {e}", is_error=True)
