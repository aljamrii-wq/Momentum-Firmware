"""
Flipper Zero skill for hermes-agent — standalone, no external package deps beyond pyserial.

Install on linode-prod:
  1. sudo cp 99-flipper.rules /etc/udev/rules.d/
     sudo udevadm control --reload && sudo udevadm trigger
  2. pip install pyserial
  3. cp flipper_skill.py /srv/hermes-agent/.hermes/hermes-agent/skills/
  4. export FLIPPER_PORT=/dev/flipper   (or add to hermes-gateway.service env)

Environment vars:
  FLIPPER_PORT       serial device (default: /dev/flipper)
  FLIPPER_AUDIT_LOG  JSONL log path (default: ~/.flipper_audit.jsonl)
"""

import functools
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import serial

# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

_BAUD = 230400
_PROMPT = b">: "
_DEFAULT_TIMEOUT = 5.0

class _FlipperTransport:
    def __init__(self):
        self.port = os.environ.get("FLIPPER_PORT", "/dev/flipper")
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._ser = serial.Serial(self.port, baudrate=_BAUD, timeout=_DEFAULT_TIMEOUT)
        self._ser.reset_input_buffer()
        self._ser.write(b"\r\n")
        self._read_until_prompt(timeout=3.0)

    def disconnect(self) -> None:
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    def is_connected(self) -> bool:
        return bool(self._ser and self._ser.is_open)

    def _read_until_prompt(self, timeout: float = _DEFAULT_TIMEOUT) -> bytes:
        buf = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            waiting = self._ser.in_waiting
            chunk = self._ser.read(waiting if waiting else 1)
            if chunk:
                buf += chunk
                if buf.endswith(_PROMPT):
                    return buf
        return buf

    def cmd(self, command: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write((command + "\r\n").encode())
            raw = self._read_until_prompt(timeout)
            text = raw.decode(errors="replace")
            lines = [
                l for l in text.splitlines()
                if l.strip() and l.strip() != ">:" and l != command
            ]
            return "\n".join(lines).strip()

    def write_file(self, remote_path: str, data: bytes, timeout: float = 15.0) -> str:
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(f"storage write {remote_path}\r\n".encode())
            time.sleep(0.05)
            self._ser.write(data)
            self._ser.write(b"\x03")  # ETX signals end of input
            raw = self._read_until_prompt(timeout)
            return raw.decode(errors="replace").strip()


_transport: Optional[_FlipperTransport] = None

def _t() -> _FlipperTransport:
    global _transport
    if not (_transport and _transport.is_connected()):
        _transport = _FlipperTransport()
        _transport.connect()
    return _transport

# ---------------------------------------------------------------------------
# Risk / audit
# ---------------------------------------------------------------------------

LOW     = "low"
MEDIUM  = "medium"
HIGH    = "high"

_AUDIT_PATH = os.environ.get("FLIPPER_AUDIT_LOG", os.path.expanduser("~/.flipper_audit.jsonl"))

_BLOCKED_PATHS = {"/int/assets", "/int/dolphin", "/int/badusb/assets"}

def _audit(tool: str, kwargs: dict, result: str, tier: str) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "tier": tier,
        "args": {k: str(v)[:200] for k, v in kwargs.items()},
        "result": result[:300],
    }
    try:
        with open(_AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass

def _classified(tier: str, confirm_prompt: str = ""):
    def decorator(fn):
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

def _check_blocked(path: str) -> None:
    for blocked in _BLOCKED_PATHS:
        if path.startswith(blocked):
            raise ValueError(f"Path {path!r} is in a protected area and cannot be modified.")

# ---------------------------------------------------------------------------
# Tools — LOW risk (read-only)
# ---------------------------------------------------------------------------

@_classified(LOW)
def flipper_ping() -> str:
    """Check whether the Flipper Zero is connected and responsive."""
    result = _t().cmd("device_info", timeout=3.0)
    return "Flipper connected." if result else "No response from Flipper."


@_classified(LOW)
def flipper_device_info() -> str:
    """Return Flipper Zero hardware model, firmware version, and battery level."""
    return _t().cmd("device_info")


@_classified(LOW)
def flipper_power_info() -> str:
    """Return battery charge percentage and charging state."""
    return _t().cmd("power_info")


@_classified(LOW)
def flipper_storage_list(path: str = "/ext") -> str:
    """
    List files and directories on Flipper storage.
    path: /ext (SD card, default) or /int (internal flash).
    """
    return _t().cmd(f"storage list {path}")


@_classified(LOW)
def flipper_storage_read(path: str) -> str:
    """Read and return the text contents of a file on Flipper storage."""
    return _t().cmd(f"storage read {path}", timeout=10.0)


@_classified(LOW)
def flipper_storage_stat(path: str) -> str:
    """Return size and type metadata for a path on Flipper storage."""
    return _t().cmd(f"storage stat {path}")


# ---------------------------------------------------------------------------
# Tools — MEDIUM risk (writes / mutations)
# ---------------------------------------------------------------------------

@_classified(MEDIUM)
def flipper_storage_mkdir(path: str) -> str:
    """Create a directory on Flipper storage."""
    _check_blocked(path)
    return _t().cmd(f"storage mkdir {path}")


@_classified(MEDIUM)
def flipper_storage_write(path: str, content: str) -> str:
    """
    Write text content to a file on Flipper storage (creates or overwrites).
    path: full storage path, e.g. /ext/subghz/my_signal.sub
    """
    _check_blocked(path)
    return _t().write_file(path, content.encode())


@_classified(MEDIUM)
def flipper_storage_delete(path: str) -> str:
    """Delete a file from Flipper storage."""
    _check_blocked(path)
    return _t().cmd(f"storage remove {path}")


@_classified(MEDIUM)
def flipper_app_launch(app_name: str) -> str:
    """
    Launch an installed app on Flipper by name.
    Examples: 'NFC', 'Sub-GHz', 'Infrared', 'iButton', 'Bad USB'
    """
    return _t().cmd(f"loader open {app_name!r}")


# ---------------------------------------------------------------------------
# Tools — HIGH risk (RF / execution, agent must confirm with user first)
# ---------------------------------------------------------------------------

@_classified(HIGH, confirm_prompt="This will transmit an IR signal. Confirm?")
def flipper_ir_send(protocol: str, address: int, command: int) -> str:
    """
    Transmit an infrared signal.
    protocol: e.g. NEC, Samsung32, RC6. address/command: decimal integers.
    """
    return _t().cmd(f"ir tx {protocol} {address} {command}", timeout=10.0)


@_classified(HIGH, confirm_prompt="This will transmit a SubGHz RF signal. Confirm?")
def flipper_subghz_tx(file_path: str) -> str:
    """
    Transmit a SubGHz signal from a .sub file on the Flipper SD card.
    file_path: e.g. /ext/subghz/my_signal.sub
    """
    return _t().cmd(f"subghz tx {file_path}", timeout=20.0)


@_classified(HIGH, confirm_prompt="This will listen for SubGHz signals. Confirm?")
def flipper_subghz_rx(frequency_hz: int = 433920000, timeout_sec: int = 10) -> str:
    """
    Listen for SubGHz signals at a given frequency.
    frequency_hz: default 433920000 (433.92 MHz). timeout_sec: listen duration.
    """
    return _t().cmd(f"subghz rx {frequency_hz}", timeout=timeout_sec + 5.0)


@_classified(HIGH, confirm_prompt="This will scan for NFC/RFID cards. Confirm?")
def flipper_nfc_read() -> str:
    """Scan for and read an NFC or RFID card placed on the Flipper."""
    return _t().cmd("nfc detect", timeout=15.0)


@_classified(HIGH, confirm_prompt="This will emulate an NFC card. Confirm?")
def flipper_nfc_emulate(file_path: str) -> str:
    """
    Emulate an NFC card from a saved .nfc file on the Flipper SD card.
    file_path: e.g. /ext/nfc/my_card.nfc
    """
    return _t().cmd(f"nfc emulate {file_path}", timeout=20.0)


@_classified(HIGH, confirm_prompt="This will execute a BadUSB payload on a connected computer. Confirm?")
def flipper_badusb_run(file_path: str) -> str:
    """
    Execute a BadUSB (Ducky Script) payload from Flipper storage.
    file_path: e.g. /ext/badusb/payload.txt
    """
    return _t().cmd(f"badusb run {file_path}", timeout=60.0)


# ---------------------------------------------------------------------------
# Tool registry — hermes-agent discovers tools from TOOLS
# ---------------------------------------------------------------------------

TOOLS = [
    flipper_ping,
    flipper_device_info,
    flipper_power_info,
    flipper_storage_list,
    flipper_storage_read,
    flipper_storage_stat,
    flipper_storage_mkdir,
    flipper_storage_write,
    flipper_storage_delete,
    flipper_app_launch,
    flipper_ir_send,
    flipper_subghz_tx,
    flipper_subghz_rx,
    flipper_nfc_read,
    flipper_nfc_emulate,
    flipper_badusb_run,
]
