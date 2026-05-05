"""
Risk classification for Flipper tool calls, ported from V3SP3R's model.

Tiers:
  low     — read-only, executes silently
  medium  — writes/mutations, result shown in Telegram reply
  high    — RF/BadUSB/destructive, requires explicit user confirmation
  blocked — never executes regardless of instruction
"""

import functools
import json
import os
from datetime import datetime, timezone
from typing import Callable

LOW = "low"
MEDIUM = "medium"
HIGH = "high"
BLOCKED = "blocked"

AUDIT_PATH = os.environ.get("FLIPPER_AUDIT_LOG", os.path.expanduser("~/.flipper_audit.jsonl"))

# Paths that are never writable/deletable, even if instructed.
BLOCKED_PATHS = {"/int/assets", "/int/dolphin", "/int/badusb/assets"}


def _audit(tool: str, kwargs: dict, result: str, tier: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "tier": tier,
        "args": {k: str(v)[:200] for k, v in kwargs.items()},
        "result": result[:300],
    }
    try:
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # audit failure must never block the tool


def classified(tier: str, confirm_prompt: str = ""):
    """
    Decorator that attaches risk metadata and writes audit entries.

    The confirm_prompt is surfaced to the agent so it can relay it to the
    user before executing HIGH-tier tools.
    """
    def decorator(fn: Callable) -> Callable:
        fn._risk_tier = tier
        fn._confirm_prompt = confirm_prompt

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _audit(fn.__name__, kwargs, "pending", tier)
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                _audit(fn.__name__, kwargs, f"ERROR: {exc}", tier)
                raise
            _audit(fn.__name__, kwargs, str(result), tier)
            return result

        wrapper._risk_tier = tier
        wrapper._confirm_prompt = confirm_prompt
        return wrapper

    return decorator


def check_path_blocked(path: str) -> None:
    """Raise ValueError if path falls under a blocked prefix."""
    for blocked in BLOCKED_PATHS:
        if path.startswith(blocked):
            raise ValueError(f"Path {path!r} is in a blocked protected area.")
