"""
PC control tools — mouse, keyboard, windows, clipboard, screenshots.
Requires: pyautogui, xdotool (Linux), wmctrl (Linux)
"""
from __future__ import annotations
import base64
import io
import subprocess
from typing import Optional

import config


def _check_enabled() -> Optional[str]:
    if not config.PC_CONTROL_ENABLED:
        return "PC control is disabled. Set PC_CONTROL_ENABLED=true in .env"
    return None


# ── Mouse ──────────────────────────────────────────────────────────────────────

def mouse_move(x: int, y: int) -> str:
    if (e := _check_enabled()): return e
    import pyautogui
    pyautogui.moveTo(x, y, duration=0.2)
    return f"Mouse moved to ({x}, {y})"


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    if (e := _check_enabled()): return e
    import pyautogui
    pyautogui.click(x, y, button=button, clicks=clicks)
    return f"Clicked {button} at ({x}, {y}) × {clicks}"


def mouse_scroll(x: int, y: int, amount: int) -> str:
    if (e := _check_enabled()): return e
    import pyautogui
    pyautogui.scroll(amount, x=x, y=y)
    return f"Scrolled {amount} at ({x}, {y})"


# ── Keyboard ───────────────────────────────────────────────────────────────────

def type_text(text: str) -> str:
    if (e := _check_enabled()): return e
    import pyautogui
    pyautogui.typewrite(text, interval=0.03)
    return f"Typed: {text}"


def press_key(key: str) -> str:
    """Press a single key or hotkey combo (e.g. 'ctrl+c', 'enter', 'alt+F4')."""
    if (e := _check_enabled()): return e
    import pyautogui
    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key)
    return f"Pressed: {key}"


# ── Clipboard ──────────────────────────────────────────────────────────────────

def get_clipboard() -> str:
    try:
        result = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout or "(empty clipboard)"
        result = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, text=True)
        return result.stdout or "(empty clipboard)"
    except Exception as e:
        return f"Clipboard read error: {e}"


def set_clipboard(text: str) -> str:
    try:
        proc = subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True)
        if proc.returncode == 0:
            return "Clipboard set."
        subprocess.run(["xsel", "--clipboard", "--input"], input=text, text=True)
        return "Clipboard set."
    except Exception as e:
        return f"Clipboard write error: {e}"


# ── Screenshot ─────────────────────────────────────────────────────────────────

def take_screenshot(region: Optional[tuple] = None) -> str:
    """Return screenshot as base64 PNG string."""
    if (e := _check_enabled()): return e
    import pyautogui
    img = pyautogui.screenshot(region=region)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


# ── Windows ────────────────────────────────────────────────────────────────────

def list_windows() -> str:
    try:
        result = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True)
        return result.stdout.strip() or "No windows found."
    except FileNotFoundError:
        return "wmctrl not installed. Run: sudo apt install wmctrl"


def focus_window(title_substr: str) -> str:
    try:
        result = subprocess.run(["wmctrl", "-a", title_substr], capture_output=True, text=True)
        if result.returncode == 0:
            return f"Focused window matching '{title_substr}'"
        return f"No window matching '{title_substr}' found."
    except FileNotFoundError:
        return "wmctrl not installed. Run: sudo apt install wmctrl"


def open_app(app: str) -> str:
    try:
        subprocess.Popen(app.split(), start_new_session=True)
        return f"Launched: {app}"
    except Exception as e:
        return f"Failed to launch {app}: {e}"


def get_screen_size() -> str:
    import pyautogui
    w, h = pyautogui.size()
    return f"Screen size: {w}×{h}"


def get_mouse_position() -> str:
    import pyautogui
    x, y = pyautogui.position()
    return f"Mouse position: ({x}, {y})"
