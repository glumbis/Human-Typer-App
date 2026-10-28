"""Tiny JSON store for AI settings (%APPDATA%\\HumanTyper\\ai.json)."""
import json
import os
from .. import plat

DEFAULTS = {
    "force_cpu": False,     # set automatically after a failed GPU (Vulkan) start
    "model": "",            # last used model path
    "length": 250,
    "language": "auto",
    "tone": "neutral",
}


def path() -> str:
    return os.path.join(plat.config_dir(), "ai.json")


def load(p: str = None) -> dict:
    data = dict(DEFAULTS)
    try:
        with open(p or path(), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update({k: loaded[k] for k in DEFAULTS if k in loaded})
    except Exception:
        pass
    return data


def save(data: dict, p: str = None) -> None:
    try:
        target = p or path()
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
