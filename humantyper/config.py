"""Persist GUI settings as JSON in %APPDATA%\\HumanTyper."""
import json
import os

DEFAULTS = {
    "wpm": 60,
    "typo_rate": 2.0,          # percent
    "revise_mode": "random",   # "random" | "end"
    "locate_mode": "auto",     # "auto" | "clipboard" | "count"
    "always_on_top": False,
    "overlay": True,           # always-on-top mini stats window
    "animation": True,         # animated ASCII galaxy / banner / face
    "overlay_x": None,
    "overlay_y": None,
    "last_text": "",
}


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HumanTyper", "settings.json")


def load(path: str = None) -> dict:
    data = dict(DEFAULTS)
    try:
        with open(path or config_path(), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            for k in DEFAULTS:
                if k in loaded:
                    data[k] = loaded[k]
    except Exception:
        pass
    return data


def save(data: dict, path: str = None) -> None:
    try:
        p = path or config_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
