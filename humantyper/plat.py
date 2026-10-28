"""Where the app is running, and where it keeps its files.

Windows: settings in %APPDATA%\\HumanTyper, big files (models, logs) in %LOCALAPPDATA%\\HumanTyper.
macOS:   both in ~/Library/Application Support/HumanTyper.
Other:   ~/.config/HumanTyper and ~/.local/share/HumanTyper (best effort, not a supported target).
"""
import os
import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Name of the "command" modifier for shortcuts: copy, select-to-start, ... (Ctrl on Windows, Cmd on Mac)
CMD_KEY = "cmd" if IS_MAC else "ctrl"
# Modifier that jumps a word at a time with the arrow keys (Ctrl on Windows, Option on Mac)
WORD_KEY = "alt" if IS_MAC else "ctrl"


def config_dir() -> str:
    """Small settings files (settings.json, ai.json)."""
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "HumanTyper")


def data_dir() -> str:
    """Large/local files (downloaded models, engine log)."""
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "HumanTyper")
