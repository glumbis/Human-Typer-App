"""Local AI text writer: bundled llama.cpp server + model management. Never talks to a cloud model."""
from .engine import EngineError, LocalEngine, engine_dir, engine_exe, get_engine
from .engine import shutdown as _shutdown


def shutdown_ai() -> None:
    """Stop the local llama-server (call on app exit; also registered via atexit)."""
    _shutdown()


__all__ = ["EngineError", "LocalEngine", "engine_dir", "engine_exe", "get_engine", "shutdown_ai"]
